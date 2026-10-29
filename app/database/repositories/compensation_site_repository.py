import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from sqlalchemy import Connection
from sqlalchemy import func
from sqlalchemy import insert
from sqlalchemy import literal_column
from sqlalchemy import select
from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database.models import CompensationSite
from app.database.models import HarvestIssue

SITES = CompensationSite.__table__

ISSUES = HarvestIssue.__table__

# Columns overwritten when the source content changed. Natural key,
# surrogate id, first_seen_run_id and created_at are never touched.
_IMMUTABLE = {"id", "state_code", "site_id", "first_seen_run_id", "created_at", "updated_at"}

MUTABLE_COLUMNS = [c.name for c in SITES.columns if c.name not in _IMMUTABLE]


@dataclass
class UpsertOutcome:
    inserted: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)


class CompensationSiteRepository:
    """
    Data access for iris_staging.compensation_sites. All methods run on
    the caller's connection/transaction; nothing here commits.
    """

    def __init__(self, connection: Connection):
        self.connection = connection

    def try_lock(self, state_code: str) -> bool:
        """
        Transaction-scoped advisory lock: two harvests of the same state
        can never interleave their upserts.
        """

        key = f"iris_staging.compensation_sites:{state_code}"
        return bool(self.connection.execute(select(func.pg_try_advisory_xact_lock(func.hashtext(key)))).scalar())

    def upsert(self, rows: list[dict[str, Any]]) -> UpsertOutcome:
        """
        INSERT ... ON CONFLICT (state_code, site_id) DO UPDATE
        WHERE record_hash IS DISTINCT FROM excluded.record_hash

        - new site            -> inserted  (xmax = 0)
        - content changed     -> updated
        - content identical   -> no write at all (not returned)
        """

        outcome = UpsertOutcome()

        if not rows:
            return outcome

        statement = pg_insert(SITES).values(rows)
        excluded = statement.excluded

        set_ = {name: excluded[name] for name in MUTABLE_COLUMNS}
        set_["updated_at"] = func.now()

        statement = statement.on_conflict_do_update(
            constraint="uq_compensation_sites_state_site",
            set_=set_,
            where=SITES.c.record_hash.is_distinct_from(excluded.record_hash),
        ).returning(SITES.c.site_id, literal_column("(xmax = 0)").label("inserted"))

        written = {row.site_id: row.inserted for row in self.connection.execute(statement)}

        for row in rows:
            site_id = row["site_id"]

            if site_id not in written:
                outcome.unchanged.append(site_id)
            elif written[site_id]:
                outcome.inserted.append(site_id)
            else:
                outcome.updated.append(site_id)

        return outcome

    def mark_seen(self, state_code: str, site_ids: list[str], run_id: uuid.UUID) -> None:
        """
        Unchanged sites: record that this run saw them, without touching
        updated_at (the content did not change).
        """

        if not site_ids:
            return

        self.connection.execute(
            update(SITES)
            .where(SITES.c.state_code == state_code, SITES.c.site_id.in_(site_ids))
            .values(last_seen_run_id=run_id, updated_at=SITES.c.updated_at)
        )

    def count_not_seen(self, state_code: str, run_id: uuid.UUID) -> int:
        """
        Sites present in staging but absent from this run's source data
        (withdrawn upstream, or filtered). Reported, never deleted.
        """

        return int(
            self.connection.execute(
                select(func.count())
                .select_from(SITES)
                .where(SITES.c.state_code == state_code, SITES.c.last_seen_run_id != run_id)
            ).scalar_one()
        )

    def insert_issues(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self.connection.execute(insert(ISSUES), rows)
