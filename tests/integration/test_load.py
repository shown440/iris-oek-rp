"""
End-to-end against PostGIS (TEST_DATABASE_URL, e.g. `make test`).
"""

import json

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app.database.repositories import compensation_site_repository as repository_module
from app.loaders.compensation_loader import HarvestLockedError
from app.runners.compensation_runner import run_export
from app.runners.compensation_runner import run_harvest
from app.runners.compensation_runner import run_validate
from tests.conftest import TEST_ADMIN_DATABASE_URL
from tests.conftest import TEST_DATABASE_URL
from tests.conftest import make_settings

pytestmark = pytest.mark.integration


def scalar(engine, sql, **params):
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def test_first_run_loads_accepted_sites(engine, tmp_path):
    report = run_harvest(make_settings(tmp_path))

    assert report["database"] == {"inserted": 42, "updated": 0, "unchanged": 0, "not_seen_in_run": 0}
    assert scalar(engine, "SELECT count(*) FROM iris_staging.compensation_sites") == 42
    assert scalar(engine, "SELECT status FROM iris_staging.harvest_runs") == "succeeded"
    assert scalar(engine, "SELECT count(*) FROM iris_staging.harvest_issues WHERE severity = 'rejected'") == 12


def test_rerun_is_idempotent(engine, tmp_path):
    run_harvest(make_settings(tmp_path))
    snapshot = (
        "SELECT string_agg(site_id || record_hash || updated_at::text, ',' ORDER BY site_id) "
        "FROM iris_staging.compensation_sites"
    )
    before = scalar(engine, snapshot)

    report = run_harvest(make_settings(tmp_path))

    assert report["database"] == {"inserted": 0, "updated": 0, "unchanged": 42, "not_seen_in_run": 0}
    assert scalar(engine, "SELECT count(*) FROM iris_staging.compensation_sites") == 42
    assert scalar(engine, snapshot) == before  # no row rewritten, updated_at untouched
    assert scalar(engine, "SELECT count(DISTINCT last_seen_run_id) FROM iris_staging.compensation_sites") == 1
    assert scalar(engine, "SELECT count(*) FROM iris_staging.harvest_runs WHERE status = 'succeeded'") == 2


def test_changed_and_withdrawn_sites(engine, tmp_path, fixture_copy):
    run_harvest(make_settings(tmp_path))

    collection = json.loads((fixture_copy / "features.json").read_text(encoding="utf-8"))
    collection["features"][0]["properties"]["status"] = "aufgehoben"  # RP-OEK-2026-0001 changed
    del collection["features"][1]  # RP-OEK-2026-0002 withdrawn upstream
    (fixture_copy / "features.json").write_text(json.dumps(collection), encoding="utf-8")

    report = run_harvest(make_settings(tmp_path, fixture_dir=fixture_copy))

    assert report["database"] == {"inserted": 0, "updated": 1, "unchanged": 40, "not_seen_in_run": 1}
    assert (
        scalar(
            engine, "SELECT compensation_status FROM iris_staging.compensation_sites WHERE site_id = 'RP-OEK-2026-0001'"
        )
        == "revoked"
    )
    # Withdrawn site is kept (staging never deletes) but identifiable: not seen by the latest run.
    latest_run = scalar(engine, "SELECT run_id FROM iris_staging.harvest_runs ORDER BY started_at DESC LIMIT 1")
    withdrawn_seen = scalar(
        engine, "SELECT last_seen_run_id FROM iris_staging.compensation_sites WHERE site_id = 'RP-OEK-2026-0002'"
    )
    assert withdrawn_seen is not None and withdrawn_seen != latest_run


def test_failure_rolls_back_everything_and_is_logged(engine, tmp_path, monkeypatch):
    original = repository_module.CompensationSiteRepository.upsert
    calls = {"n": 0}

    def failing_upsert(self, rows):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated failure in batch 2")
        return original(self, rows)

    monkeypatch.setattr(repository_module.CompensationSiteRepository, "upsert", failing_upsert)

    with pytest.raises(RuntimeError, match="batch 2"):
        run_harvest(make_settings(tmp_path, batch_size=10))

    assert scalar(engine, "SELECT count(*) FROM iris_staging.compensation_sites") == 0  # batch 1 rolled back
    assert scalar(engine, "SELECT status FROM iris_staging.harvest_runs") == "failed"
    assert scalar(engine, "SELECT code FROM iris_staging.harvest_issues") == "run_failed"


def test_concurrent_harvest_is_blocked(engine, tmp_path):
    with engine.connect() as holder:
        holder.execute(text("SELECT pg_advisory_lock(hashtext('iris_staging.compensation_sites:RP'))"))

        try:
            with pytest.raises(HarvestLockedError):
                run_harvest(make_settings(tmp_path))
        finally:
            holder.execute(text("SELECT pg_advisory_unlock(hashtext('iris_staging.compensation_sites:RP'))"))


def test_geometry_crs_and_provenance_in_database(engine, tmp_path):
    run_harvest(make_settings(tmp_path))

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT ST_SRID(geom), GeometryType(geom), bool_and(ST_IsValid(geom)), "
                "ST_SRID(geom_source), count(*) "
                "FROM iris_staging.compensation_sites GROUP BY 1, 2, 4"
            )
        ).all()

    assert rows == [(25832, "MULTIPOLYGON", True, 25832, 42)]

    # The repaired bow-tie: source geometry kept exactly as delivered (invalid), working geometry valid.
    assert scalar(
        engine,
        "SELECT NOT ST_IsValid(geom_source) AND ST_IsValid(geom) FROM iris_staging.compensation_sites "
        "WHERE geometry_repaired",
    )


def test_validation_queries_pass(engine, tmp_path):
    settings = make_settings(tmp_path)
    run_harvest(settings)
    run_harvest(settings)

    results = run_validate(settings)
    failures = [r["name"] for r in results if r["passed"] is False]

    assert failures == []
    assert {r["name"] for r in results if r["passed"] is True} >= {
        "no_duplicate_natural_keys",
        "working_geometry_valid_multipolygon_25832",
        "records_belong_to_rhineland_palatinate",
        "provenance_retained",
    }


@pytest.mark.parametrize("role", ["runtime", "migrator"])
def test_no_role_can_touch_iris_core(engine, app_engine, role):
    current = app_engine if role == "runtime" else engine

    if not scalar(current, "SELECT count(*) FROM pg_namespace WHERE nspname = 'iris_core'"):
        pytest.skip("iris_core schema not present in this database")

    assert scalar(current, "SELECT has_schema_privilege(current_user, 'iris_core', 'CREATE')") is False
    assert scalar(current, "SELECT has_schema_privilege(current_user, 'iris_core', 'USAGE')") is False

    with pytest.raises(ProgrammingError, match="permission denied"):
        scalar(current, "CREATE TABLE iris_core.should_fail (id int)")


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM iris_staging.compensation_sites",
        "TRUNCATE iris_staging.compensation_sites",
        "DROP TABLE iris_staging.compensation_sites",
        "ALTER TABLE iris_staging.compensation_sites ADD COLUMN x int",
        "CREATE TABLE iris_staging.should_fail (id int)",
    ],
)
def test_runtime_role_can_only_read_insert_update(engine, app_engine, statement):
    if TEST_ADMIN_DATABASE_URL == TEST_DATABASE_URL:
        pytest.skip("single-role setup (no TEST_ADMIN_DATABASE_URL)")

    with pytest.raises(ProgrammingError, match="permission denied|must be owner"), app_engine.begin() as connection:
        connection.execute(text(statement))


def test_geopandas_sample_export_is_rfc7946(engine, tmp_path):
    settings = make_settings(tmp_path)
    run_harvest(settings)

    out = tmp_path / "sample.geojson"
    assert run_export(settings, out, limit=10) == 10

    collection = json.loads(out.read_text(encoding="utf-8"))
    assert "crs" not in collection  # RFC 7946: WGS84 lon/lat implied
    assert [f["id"] for f in collection["features"]][:1] == ["RP:RP-OEK-2026-0001"]

    for feature in collection["features"]:
        lon, lat = feature["geometry"]["coordinates"][0][0][0]
        assert 6.0 < lon < 8.6 and 48.9 < lat < 51.0  # inside Rhineland-Palatinate, lon first
