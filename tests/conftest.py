import json
import os
import shutil
from pathlib import Path

import pytest

from app.config.mapping import load_mapping
from app.config.settings import Settings
from app.scrapers.base.raw_archive import RawArchive
from app.scrapers.wfs.transport import FixtureTransport
from app.scrapers.wfs.wfs_scraper import WfsHarvester

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "luwg"
# Runtime (DML-only) role - what the harvester under test uses.
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
# Schema owner - migrations and cleanup between tests. Falls back to the
# runtime URL for single-role setups.
TEST_ADMIN_DATABASE_URL = os.getenv("TEST_ADMIN_DATABASE_URL") or TEST_DATABASE_URL


def make_settings(tmp_path: Path, **overrides) -> Settings:
    """
    Settings isolated from the developer's .env file.
    """

    values = {
        "source_mode": "fixture",
        "fixture_dir": FIXTURE_DIR,
        "mapping_file": ROOT / "config" / "attribute_mapping.toml",
        "data_dir": tmp_path / "data",
        "request_delay_s": 0,
        "database_url": TEST_DATABASE_URL,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture
def mapping(settings):
    return load_mapping(settings.mapping_file)


@pytest.fixture
def expected_outcomes():
    return json.loads((FIXTURE_DIR / "expected_outcomes.json").read_text(encoding="utf-8"))


@pytest.fixture
def fixture_copy(tmp_path):
    """
    Writable copy of the fixture directory (for change-detection tests).
    """

    target = tmp_path / "fixture"
    shutil.copytree(FIXTURE_DIR, target)
    return target


def harvest_features(settings, mapping, transport=None):
    transport = transport or FixtureTransport(settings.fixture_dir)
    harvester = WfsHarvester(transport, settings, mapping, RawArchive(settings.data_dir / "raw"))
    discovery = harvester.discover()
    pages = list(harvester.iter_pages(discovery))
    return transport, discovery, pages


# =====================================================
# INTEGRATION (PostGIS)
# =====================================================


@pytest.fixture(scope="session")
def migrated_engine():
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set (run `make test` for the Docker setup)")

    from alembic import command
    from alembic.config import Config

    from app.database.session import get_engine

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", TEST_ADMIN_DATABASE_URL)
    command.upgrade(config, "head")

    return get_engine(TEST_ADMIN_DATABASE_URL)


@pytest.fixture
def engine(migrated_engine):
    """
    Schema-owner engine (cleanup + assertions). Harvests under test use the
    runtime role via make_settings(database_url=TEST_DATABASE_URL).
    """

    with migrated_engine.begin() as connection:
        connection.exec_driver_sql(
            "TRUNCATE iris_staging.harvest_issues, iris_staging.compensation_sites, iris_staging.harvest_runs"
        )
    return migrated_engine


@pytest.fixture
def app_engine(engine):
    from app.database.session import get_engine

    return get_engine(TEST_DATABASE_URL)
