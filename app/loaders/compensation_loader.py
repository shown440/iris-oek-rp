"""
Transactional, idempotent load into iris_staging.

    start_run()       own transaction  -> harvest_runs(status=running)
    load()            ONE transaction  -> advisory lock, batched upserts,
                                          mark_seen, issue log
    finish_run()      own transaction  -> status, counts, provenance
    record_failure()  own transaction  -> status=failed + load_failed issue

If anything inside load() fails, the whole batch rolls back (no partial
site data), while the run record and the failure stay auditable.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from itertools import batched
from typing import Any

from geoalchemy2.shape import from_shape
from sqlalchemy import Engine
from sqlalchemy import insert
from sqlalchemy import update

from app.config.settings import Settings
from app.database.models import HarvestRun
from app.database.repositories.compensation_site_repository import CompensationSiteRepository
from app.transformers.compensation_transformer import Issue
from app.transformers.compensation_transformer import SiteRecord
from app.transformers.geometry import TARGET_SRID

RUNS = HarvestRun.__table__


class HarvestLockedError(RuntimeError):
    """
    Another harvest for the same state holds the advisory lock.
    """


@dataclass(frozen=True)
class LoadResult:
    inserted: int
    updated: int
    unchanged: int
    not_seen_in_run: int


def site_row(record: SiteRecord, run_id: uuid.UUID, harvested_at: datetime) -> dict[str, Any]:
    row = {key: value for key, value in vars(record).items() if key not in ("geom", "geom_source", "source_srid")}
    row["geom"] = from_shape(record.geom, srid=TARGET_SRID, extended=True)
    row["geom_source"] = from_shape(record.geom_source, srid=record.source_srid, extended=True)
    row["first_seen_run_id"] = run_id
    row["last_seen_run_id"] = run_id
    row["last_changed_run_id"] = run_id
    row["harvested_at"] = harvested_at
    return row


def issue_row(issue: Issue, run_id: uuid.UUID) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "severity": issue.severity,
        "stage": issue.stage,
        "code": issue.code,
        "site_id": issue.site_id,
        "source_feature_id": issue.feature_id,
        "message": issue.message,
        "raw_record": issue.raw_record,
    }


class CompensationLoader:
    def __init__(self, engine: Engine, settings: Settings):
        self.engine = engine
        self.settings = settings

    # =====================================================
    # RUN BOOKKEEPING (separate transactions)
    # =====================================================

    def start_run(self, run_id: uuid.UUID, source_mode: str, endpoint: str, layer: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                insert(RUNS).values(
                    run_id=run_id,
                    state_code=self.settings.state_code,
                    source_mode=source_mode,
                    source_endpoint=endpoint,
                    source_layer=layer,
                    status="running",
                )
            )

    def finish_run(
        self,
        run_id: uuid.UUID,
        counts: dict[str, Any],
        provenance: dict[str, Any],
    ) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                update(RUNS)
                .where(RUNS.c.run_id == run_id)
                .values(status="succeeded", finished_at=datetime.now(UTC), counts=counts, provenance=provenance)
            )

    def record_failure(self, run_id: uuid.UUID, error: BaseException, provenance: dict[str, Any]) -> None:
        message = f"{type(error).__name__}: {error}"

        with self.engine.begin() as connection:
            connection.execute(
                update(RUNS)
                .where(RUNS.c.run_id == run_id)
                .values(status="failed", finished_at=datetime.now(UTC), error=message, provenance=provenance)
            )
            CompensationSiteRepository(connection).insert_issues(
                [issue_row(Issue("rejected", "load", "run_failed", message), run_id)]
            )

    # =====================================================
    # LOAD (single transaction)
    # =====================================================

    def load(
        self,
        run_id: uuid.UUID,
        records: list[SiteRecord],
        issues: list[Issue],
        harvested_at: datetime,
    ) -> LoadResult:
        state_code = self.settings.state_code
        inserted = updated = unchanged = 0

        with self.engine.begin() as connection:
            repository = CompensationSiteRepository(connection)

            if not repository.try_lock(state_code):
                raise HarvestLockedError(f"Another harvest for state {state_code} is running")

            for batch in batched(records, self.settings.batch_size, strict=False):
                outcome = repository.upsert([site_row(r, run_id, harvested_at) for r in batch])
                repository.mark_seen(state_code, outcome.unchanged, run_id)

                inserted += len(outcome.inserted)
                updated += len(outcome.updated)
                unchanged += len(outcome.unchanged)

            for batch in batched(issues, self.settings.batch_size, strict=False):
                repository.insert_issues([issue_row(i, run_id) for i in batch])

            not_seen = repository.count_not_seen(state_code, run_id)

        return LoadResult(inserted, updated, unchanged, not_seen)
