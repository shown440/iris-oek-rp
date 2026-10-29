# Architecture and processing details

Companion to the [README](../README.md). Security is described in [SECURITY.md](SECURITY.md); evidence and rejected data in
[QUALITY_REPORT.md](QUALITY_REPORT.md).

## Contents

1. [Pipeline](#1-pipeline)
2. [Where GeoPandas is used](#2-where-geopandas-is-used)
3. [Processing outcomes](#3-processing-outcomes)
4. [Idempotency, transactions, error logging](#4-idempotency-transactions-error-logging)
5. [Validation queries and sample export](#5-validation-queries-and-sample-export)
6. [Tests and automated checks](#6-tests-and-automated-checks)
7. [Configuration and controlled requests](#7-configuration-and-controlled-requests)
8. [Going live and integrating into iris-sample](#8-going-live-and-integrating-into-iris-sample)
9. [Design decisions](#9-design-decisions)

## 1. Pipeline

```
 WFS (live HTTPS | fixture emulator)
   │  GetCapabilities ─► DescribeFeatureType ─► GetFeature hits ─► verify before use ─► GetFeature pages
   ▼
 app/scrapers/          discovery, permission/schema gate, paging, retries, URL guard, size cap, raw archive (+SHA-256)
   ▼
 app/parsers/           GeoJSON (per-feature error isolation) | GML 3.2 (GeoPandas/pyogrio, sanitised XSD)
   ▼
 app/transformers/      site_id → domain → geometry (GeoPandas: validate/repair/to_crs) → state → attributes → dedup
   ▼
 app/loaders/           ONE transaction: advisory lock, batched ON CONFLICT upsert, issue log
   ▼
 iris_staging.compensation_sites · harvest_runs · harvest_issues   ──► report · validation SQL · GeoPandas export
```

| Where | File |
|---|---|
| CLI entry point | [`app/cli.py`](../app/cli.py) → [`app/runners/compensation_runner.py`](../app/runners/compensation_runner.py) |
| Capabilities / schema parsing, XSD sanitising | [`app/scrapers/wfs/capabilities.py`](../app/scrapers/wfs/capabilities.py) |
| Discovery, verify-before-use gate, paging | [`app/scrapers/wfs/wfs_scraper.py`](../app/scrapers/wfs/wfs_scraper.py) |
| Retries, outbound URL guard, response size cap | [`app/scrapers/base/http_client.py`](../app/scrapers/base/http_client.py) |
| Live vs fixture transport | [`app/scrapers/wfs/transport.py`](../app/scrapers/wfs/transport.py) |
| Page parsing | [`app/parsers/compensation_parser.py`](../app/parsers/compensation_parser.py) |
| Attribute rules ("never guess") | [`app/transformers/normalizers.py`](../app/transformers/normalizers.py) |
| Geometry stage (GeoPandas) and state check | [`app/transformers/geometry.py`](../app/transformers/geometry.py) |
| Record pipeline + dedup | [`app/transformers/compensation_transformer.py`](../app/transformers/compensation_transformer.py) |
| Idempotent upsert | [`app/database/repositories/compensation_site_repository.py`](../app/database/repositories/compensation_site_repository.py) |
| Transaction / run bookkeeping | [`app/loaders/compensation_loader.py`](../app/loaders/compensation_loader.py) |
| Tables | [`app/database/models/`](../app/database/models/), [`alembic/versions/0001_…`](../alembic/versions/0001_create_iris_staging_compensation.py) |
| Declarative mapping | [`config/attribute_mapping.toml`](../config/attribute_mapping.toml) |

## 2. Where GeoPandas is used

The brief names the integration pattern **OGC WFS / GeoPandas / PostGIS**.

| Step | GeoPandas API |
|---|---|
| GML pages (servers without GeoJSON) | `pyogrio.read_dataframe` → `GeoDataFrame` |
| Geometry stage, whole batch, one `GeoSeries` per source CRS | `isna`, `is_empty`, `geom_type`, `has_z` → `force_2d` → `is_valid` / `is_valid_reason` → `make_valid(method="structure")` → `to_crs(25832)` → `area` |
| Sample export | `read_postgis` → `to_crs(4326)` → `to_file(driver="GeoJSON", RFC7946="YES")` |

GeoJSON pages are turned into geometries one feature at a time (`shapely.geometry.shape`) on purpose: a single malformed
feature becomes a `geometry_unparseable` rejection instead of failing the whole page, as `GeoDataFrame.from_features` would.

## 3. Processing outcomes

Each fetched feature ends in exactly one outcome: **accepted**, **rejected**, or **collapsed duplicate**.
`fetched = accepted + rejected + collapsed` is checked on every run.

| Stage | Rejection codes | Warning flags (record still loaded) |
|---|---|---|
| identity | `missing_site_id` | `site_id_from_feature_id` (only if enabled) |
| domain | `out_of_domain`, `unknown_object_type`, `missing_object_type` | — |
| geometry | `missing_geometry`, `empty_geometry`, `unsupported_geometry_type`, `geometry_unparseable`, `unknown_source_crs`, `unrepairable_geometry`, `degenerate_geometry` | `geometry_repaired`, `z_dropped` |
| state | `outside_state` (key ≠ 07), `geometry_outside_state` (CRS/axis error) | `near_state_border` |
| attributes | — | `unmapped_status`, `ambiguous_number`, `invalid_number`, `invalid_area`, `area_mismatch`, `invalid_eco_credits`, `eco_credits_inconsistent`, `name_encoding_repaired`, `municipality_name_mismatch`, `municipality_key_zero_padded`, `municipality_key_unknown`, `invalid_municipality_key`, `invalid_cadastral_key`, `invalid_date` |
| batch | `conflicting_duplicate` | `duplicate_in_source` |

**Verify before use (run level).** Before any feature data is requested, the harvest stops with `SchemaMismatchError` if the
published schema lacks the stable id (`objekt_id`) or a geometry property. It stops with
`AccessConstraintsNotAcknowledgedError` if the service declares `AccessConstraints` other than NONE and
`IRIS_ACCESS_CONSTRAINTS_ACKNOWLEDGED=true` has not been set after reading the terms. HTTP 401/403 stops immediately.
`discover` lists these as `harvest_blockers` without failing.

**Geometry repair policy.** `make_valid(method="structure")` rebuilds rings without inventing vertices. Only the polygonal part
is kept. If nothing polygonal is left, the record is rejected rather than buffered. The original geometry is stored untouched
in `geom_source`.

**State rule.** The AGS prefix `07` is authoritative. The VG2500 polygon is a plausibility check (2 km tolerance for its
1:2.5M generalisation) that catches CRS and axis-order errors. A bounding box alone would wrongly accept Mannheim (BW).

Fixture result: **55 fetched → 42 accepted (13 with warnings, 1 repaired) · 12 rejected · 1 duplicate collapsed**.

## 4. Idempotency, transactions, error logging

- **Upsert:** `INSERT … ON CONFLICT (state_code, site_id) DO UPDATE … WHERE record_hash IS DISTINCT FROM excluded.record_hash`.
  Unchanged sites are not rewritten at all (`updated_at` untouched); `RETURNING (xmax = 0)` separates inserts from updates.
- **`record_hash`:** SHA-256 over all source-derived values plus the normalised geometry (mm precision). Run ids, timestamps
  and the server feature id are excluded (MapServer numbers `gml:id` by result position).
- **Transaction:** one transaction per run for all site rows and issues. A failure in any batch rolls back everything (tested).
- **Concurrency:** `pg_try_advisory_xact_lock` per state, so two harvests can never interleave.
- **Run audit:** `harvest_runs` is written in its own transactions before and after the load, so failed runs stay visible
  (`status = failed`, `error`, `run_failed` issue).
- **Error log:** `harvest_issues` holds every rejection (with its raw record) and every warning, per run.
- **Withdrawn sites** are never deleted from staging; they show up as `not_seen_in_run` / an old `last_seen_run_id`.

Evidence: run 1 → `inserted 42`; run 2 on the same input → **`inserted 0, updated 0, unchanged 42`**
([run 1](../examples/run1_quality_report.md), [run 2](../examples/run2_rerun_quality_report.md)).

## 5. Validation queries and sample export

[`sql/validation_queries.sql`](../sql/validation_queries.sql), run with `make validate`. Output:
[`examples/validation_output.txt`](../examples/validation_output.txt).

| Check | Result |
|---|---|
| no duplicate `(state_code, site_id)` | PASS |
| geometry valid `MULTIPOLYGON`, SRID 25832 | PASS |
| records belong to RLP (state, AGS 07, envelope) | PASS |
| records belong to compensation domain / layer | PASS |
| provenance retained (endpoint, layer, CRS, source geometry, attributes) | PASS |
| `area_geom_m2` = `ST_Area(geom)` | PASS |
| info: outcome counts per run, issues by code, area + credits per district, sites within 5 km of Mainz cathedral | — |

Sample export (25 sites, RFC 7946 / EPSG:4326, written with GeoPandas): [`examples/sample_export.geojson`](../examples/sample_export.geojson).

## 6. Tests and automated checks

| Suite | Count | What it covers |
|---|---:|---|
| `tests/unit` | 99 | normalisers; geometry repair/projection/state incl. **GeoPandas batch = single-geometry results** and mixed-CRS batches; capabilities (fixture **and a real RLP document**); paging/sort/limits; 403 not bypassed; WFS 1.1.0 fallback; GML via GDAL; **golden outcome of every fixture record**; hash stability; XXE; **SSRF regression (local listener)**; URL guard; redirect and size limits; permission/schema gate |
| `tests/integration` | 15 | PostGIS: first load, **idempotent re-run**, change + withdrawal detection, **rollback on failure**, advisory lock, SRID/validity/provenance, validation SQL, GeoPandas export (RFC 7946), **no privileges on `iris_core`**, **runtime role cannot DELETE/TRUNCATE/DROP/ALTER/CREATE** |
| `tests/integration/test_live_wfs.py` | 1 | opt-in (`pytest -m live`): real RLP WFS 2.0 |

CI ([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)): ruff lint and format (incl. bandit `S` rules), then the
Docker test suite and the demo.

## 7. Configuration and controlled requests

Environment variables (prefix `IRIS_`, see [`.env.example`](../.env.example)): `DATABASE_URL`, `SOURCE_MODE` (`fixture`|`live`),
`WFS_URL`, `WFS_LAYER`, `WFS_VERSION`, `PAGE_SIZE`, `MAX_FEATURES`, `MAX_PAGES`, `REQUEST_DELAY_S`, `HTTP_TIMEOUT_S`,
`HTTP_RETRIES`, `HTTP_BACKOFF_S`, `WFS_ALLOWED_HOSTS`, `MAX_RESPONSE_MB`, `ACCESS_CONSTRAINTS_ACKNOWLEDGED`,
`STATE_BORDER_TOLERANCE_M`, `AREA_MISMATCH_TOLERANCE`, `MIN_AREA_M2`, `BATCH_SIZE`. The target schema is deliberately
**not** configurable.

Controlled requests: page size = min(`PAGE_SIZE`, server `CountDefault`), `SORTBY` on the id when the server supports sorting,
a `hits` request first, `MAX_FEATURES`/`MAX_PAGES` caps, a polite delay between requests, retries (429/5xx/connect,
exponential backoff, `Retry-After`), 401/403 never retried, and responses streamed and capped (`MAX_RESPONSE_MB`, default 200).

## 8. Going live and integrating into iris-sample

**Going live:** set `IRIS_SOURCE_MODE=live` and the confirmed `IRIS_WFS_URL` / `IRIS_WFS_LAYER`. Run `make discover-live` and
check `harvest_blockers`, `mapped_fields_missing_in_schema` and `schema_fields_not_mapped`. Adjust
`config/attribute_mapping.toml`. If the service declares `AccessConstraints`, read them and only then set
`IRIS_ACCESS_CONSTRAINTS_ACKNOWLEDGED=true`. If operation URLs live on another domain, add it to `IRIS_WFS_ALLOWED_HOSTS`.
Then run `make harvest-live`.

**Into iris-sample:** the packages mirror its layout (`app/config/data_sources.py` entry, `scrapers/`, `parsers/`,
`transformers/`, `loaders/`, `runners/`, `database/models`). It has its own Alembic history in `iris_staging`
(`version_table_schema=iris_staging`), so it never interferes with the `iris_core` migrations.

## 9. Design decisions

- **No web framework (Django / FastAPI / Flask).** The brief asks for a harvester; its deliverables are code, Docker, README,
  tests and a report, with no API or UI. A batch CLI that a scheduler (cron, Airflow) calls is the right shape; a web framework
  would add attack surface and scope nobody asked for. If an API is needed later, a read-only FastAPI service over
  `iris_staging` would sit beside this module.
- **Standalone repo, iris-sample layout.** The same stack and folder structure as the base, without its Streamlit/OpenAI
  dependencies and `iris_core` migrations, which the brief keeps out of scope.
- **GeoPandas for batch geometry work, Shapely for per-feature parsing** (see §2).
- **Python 3.14 everywhere** (venv, Docker, CI), which satisfies the brief's "3.11+".
