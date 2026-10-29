"""
Orchestrates discover -> fetch -> parse -> transform -> load -> report.
"""

import json
import logging
import re
import uuid
from datetime import UTC
from datetime import datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
from sqlalchemy import text

from app.config.mapping import load_mapping
from app.config.settings import Settings
from app.database.session import get_engine
from app.loaders.compensation_loader import CompensationLoader
from app.loaders.compensation_loader import LoadResult
from app.reports.quality_report import build_report
from app.reports.quality_report import write_report
from app.scrapers.base.http_client import RateLimitedGetter
from app.scrapers.base.raw_archive import RawArchive
from app.scrapers.wfs.transport import FixtureTransport
from app.scrapers.wfs.transport import HttpTransport
from app.scrapers.wfs.transport import WfsTransport
from app.scrapers.wfs.wfs_scraper import WfsHarvester
from app.transformers.compensation_transformer import CompensationTransformer
from app.transformers.compensation_transformer import TransformResult

logger = logging.getLogger(__name__)

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def build_transport(settings: Settings) -> WfsTransport:
    if settings.source_mode == "fixture":
        return FixtureTransport(settings.fixture_dir)

    return HttpTransport(settings.wfs_url, RateLimitedGetter.from_settings(settings))


def _new_run_dir(settings: Settings, run_id: uuid.UUID, kind: str) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = settings.data_dir / kind / f"{stamp}_{str(run_id)[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _attach_log_file(run_dir: Path) -> logging.Handler:
    handler = logging.FileHandler(run_dir / "harvest.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger().addHandler(handler)
    return handler


# =====================================================
# DISCOVER
# =====================================================


def run_discover(settings: Settings) -> dict[str, Any]:
    """
    Capabilities + schema + hits only; no features, no database.
    """

    mapping = load_mapping(settings.mapping_file)
    run_dir = _new_run_dir(settings, uuid.uuid4(), "discover")
    archive = RawArchive(run_dir / "raw")
    transport = build_transport(settings)

    try:
        discovery = WfsHarvester(transport, settings, mapping, archive).discover()
    finally:
        archive.write_manifest()

    summary = discovery.summary(mapping)
    (run_dir / "discovery.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    return summary


# =====================================================
# HARVEST
# =====================================================


def run_harvest(settings: Settings, dry_run: bool = False) -> dict[str, Any]:
    run_id = uuid.uuid4()
    run_dir = _new_run_dir(settings, run_id, "runs")
    log_handler = _attach_log_file(run_dir)
    archive = RawArchive(run_dir / "raw")
    mapping = load_mapping(settings.mapping_file)
    transport = build_transport(settings)
    loader = None if dry_run else CompensationLoader(get_engine(settings.database_url), settings)

    run: dict[str, Any] = {
        "run_id": str(run_id),
        "status": "running",
        "source_mode": settings.source_mode,
        "endpoint": transport.endpoint,
        "layer": settings.wfs_layer,
        "dry_run": dry_run,
        "run_dir": str(run_dir),
        "started_at": datetime.now(UTC).isoformat(),
    }

    discovery_summary: dict[str, Any] | None = None
    transform: TransformResult | None = None
    load: LoadResult | None = None

    run_recorded = False

    try:
        if loader:
            loader.start_run(run_id, settings.source_mode, transport.endpoint, settings.wfs_layer)
            run_recorded = True

        harvester = WfsHarvester(transport, settings, mapping, archive)
        discovery = harvester.discover()
        discovery_summary = discovery.summary(mapping)

        features = [feature for _, page in harvester.iter_pages(discovery) for feature in page.features]
        logger.info("Fetched %d features", len(features))

        transformer = CompensationTransformer(
            settings, mapping, discovery.schema, discovery.feature_type.name, transport.endpoint
        )
        transform = transformer.transform(features)

        if loader:
            load = loader.load(run_id, transform.records, transform.issues, datetime.now(UTC))

        run["status"] = "succeeded"

    except Exception as exc:
        run["status"] = "failed"
        run["error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("Harvest failed")

        if loader and run_recorded:
            loader.record_failure(run_id, exc, {"discovery": discovery_summary, "raw_files": archive.entries})

        raise

    finally:
        archive.write_manifest()
        run["finished_at"] = datetime.now(UTC).isoformat()
        report = build_report(
            run=run, discovery=discovery_summary, transform=transform, load=load, raw_files=archive.entries
        )
        write_report(report, run_dir)
        logging.getLogger().removeHandler(log_handler)
        log_handler.close()

    if loader:
        loader.finish_run(
            run_id,
            counts={**report["counts"], "database": report["database"]},
            provenance=report["provenance"],
        )

    return report


# =====================================================
# VALIDATE / EXPORT
# =====================================================


def load_validation_queries(path: Path = SQL_DIR / "validation_queries.sql") -> list[dict[str, str]]:
    """
    Blocks start with '-- name: <id>' and '-- expect: zero_rows | info'.
    """

    queries = []

    for block in re.split(r"(?m)^-- name: ", path.read_text(encoding="utf-8"))[1:]:
        name, _, body = block.partition("\n")
        expect = re.search(r"(?m)^-- expect: (\w+)", body)
        queries.append({"name": name.strip(), "expect": expect.group(1) if expect else "info", "sql": body.strip()})

    return queries


def run_validate(settings: Settings) -> list[dict[str, Any]]:
    results = []

    with get_engine(settings.database_url).connect() as connection:
        for query in load_validation_queries():
            rows = [dict(r._mapping) for r in connection.execute(text(query["sql"]))]
            passed = None if query["expect"] == "info" else len(rows) == 0
            results.append({"name": query["name"], "expect": query["expect"], "passed": passed, "rows": rows})

    return results


def run_export(settings: Settings, out: Path, limit: int) -> int:
    """
    Sample export with GeoPandas: read_postgis -> to_crs(4326) -> RFC 7946
    GeoJSON (lon/lat). Reads the staging table only.
    """

    sql = text(
        """
        SELECT state_code || ':' || site_id AS id,
               state_code, site_id, object_type, site_name,
               municipality_key, municipality_name, district_name, cadastral_district_name,
               compensation_status,
               area_source_m2::float8 AS area_source_m2,
               area_geom_m2::float8 AS area_geom_m2,
               eco_credits_total::float8 AS eco_credits_total,
               eco_credits_available::float8 AS eco_credits_available,
               source_layer, source_crs,
               array_to_string(quality_flags, ',') AS quality_flags,
               geom
        FROM iris_staging.compensation_sites
        WHERE state_code = :state_code
        ORDER BY site_id
        LIMIT :limit
        """
    )

    with get_engine(settings.database_url).connect() as connection:
        sites = gpd.read_postgis(
            sql, connection, geom_col="geom", params={"state_code": settings.state_code, "limit": limit}
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    sites.to_crs(4326).to_file(out, driver="GeoJSON", engine="pyogrio", RFC7946="YES", ID_FIELD="id")

    return len(sites)
