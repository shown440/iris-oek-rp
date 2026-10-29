# IRIS-OEK-RP: Rhineland-Palatinate Compensation Pool (LUWG)

Task 056. A reproducible OGC WFS harvester that maps registered compensation / eco-account sites (WFS layer `rp_oekokonto`)
to **`iris_staging.compensation_sites`**. Built with Python 3.14 and OGC WFS / GeoPandas / PostGIS, laid out like `iris-sample`.

> **Data status:** the endpoint in the brief does not resolve in public DNS, so the delivered results come from a
> **fixture-based demonstrator**, as the brief allows. The live client is complete and proven against a real RLP WFS (§2).

| Contents | More |
|---|---|
| 1. [Run and test](#1-run-and-test) · 2. [Data provenance](#2-data-provenance) · 3. [Schema mapping](#3-schema-mapping) · 4. [Assumptions](#4-assumptions) · 5. [Limitations](#5-limitations) | [Architecture](docs/ARCHITECTURE.md) · [Quality report](docs/QUALITY_REPORT.md) · [Security](docs/SECURITY.md) · [Fixture edge cases](fixtures/README.md) |

## 1. Run and test

Requires Docker (Compose v2) and `make`.

```bash
cp .env.example .env    # replace the three "change-me" passwords
make demo               # PostGIS → migrate → harvest → harvest again (idempotency) → validate → export
make test               # 114 unit + PostGIS integration tests, in Docker
```

| Command | What it does |
|---|---|
| `make harvest` / `make harvest-live` | Harvest the fixture / the live endpoint from the brief into `iris_staging` |
| `make discover` / `make discover-live` | Capabilities, paging options, schema check and `harvest_blockers`; no database writes |
| `make validate` | Acceptance SQL checks + sample spatial queries ([`sql/validation_queries.sql`](sql/validation_queries.sql)) |
| `make export` | GeoJSON sample export (GeoPandas, RFC 7946) |
| `make lint` · `make down` · `make clean` | ruff · stop · stop and delete the database volume |

**Example results:** [validation output](examples/validation_output.txt) (6/6 PASS) ·
[run 1](examples/run1_quality_report.md) (`inserted 42`) · [run 2, same input](examples/run2_rerun_quality_report.md)
(`inserted 0, updated 0, unchanged 42`) · [sample export](examples/sample_export.geojson).
Each run also writes `data/runs/<run>/` with the raw responses (SHA-256 manifest), `harvest.log` and `quality_report.{json,md}`.

<details><summary>Without Docker (Python 3.14 + a PostGIS staging database you control)</summary>

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r requirements-dev.txt
IRIS_DATABASE_URL=postgresql+psycopg://MIGRATOR:PASS@HOST:5432/DB alembic upgrade head   # schema owner
export IRIS_DATABASE_URL=postgresql+psycopg://HARVESTER:PASS@HOST:5432/DB              # DML-only role
python -m app harvest                # or: discover | validate | export
pytest                               # integration tests need TEST_DATABASE_URL (+ TEST_ADMIN_DATABASE_URL)
```
</details>

## 2. Data provenance

| Item | Finding (2026-10-28) |
|---|---|
| Endpoint in the brief | `https://geodaten-wasser.rlp.de/wfs/kompensation` → **NXDOMAIN** (system resolver and 8.8.8.8). Live attempt: [`examples/live_attempt_quality_report.md`](examples/live_attempt_quality_report.md) |
| Real register | RLP's compensation register (LKompVzVO; KomOn → *Kompensationskataster* KSP, run by SGD Nord). No public WFS for it could be found. |
| What was delivered | A WFS 2.0 emulator over synthetic fixtures ([`fixtures/`](fixtures/README.md)), served through the same discovery → paging → parse → load path as live mode. Records carry `source_endpoint = fixture://…`, so demo data can never be mistaken for live data. |
| Live client proof | The same client against a **real RLP WFS 2.0** (LVermGeo *Umlegung*, MapServer): capabilities, schema, `hits` (33), paged GeoJSON. [`examples/discover_real_rlp_wfs.json`](examples/discover_real_rlp_wfs.json), `pytest -m live`. |
| Source schema basis | Field names follow the entries required by **LKompVzVO § 3**: Objektkennung, Objektbezeichnung, Lage/Abgrenzung, Aktenzeichen, Fläche in **ha**, Eintragungsstelle; object types *Kompensationsfläche / Ökokonto / Eingriffsverfahren*. |
| Reference data | BKG **VG2500** (state boundary) and **VG250** (2,300 RLP municipalities, 36 districts, 01.01.2026), dl-de/by-2-0. Built by [`scripts/build_reference_data.py`](scripts/build_reference_data.py); never downloaded at runtime. |

## 3. Schema mapping

Declared in [`config/attribute_mapping.toml`](config/attribute_mapping.toml) and checked against the actual
`DescribeFeatureType` before use. A mapped field the service does not publish is reported and loaded as `NULL`; unmapped
source fields are listed in the report and kept in `source_attributes`.

| Target column (`iris_staging.compensation_sites`) | Source | Rule |
|---|---|---|
| `state_code`, `country_code` | config | `RP`, `DE` (together ISO 3166-2 `DE-RP`) |
| **`site_id`** | `objekt_id` (Objektkennung) | trimmed; missing → **rejected** (server feature ids are not stable) |
| `source_feature_id` | `gml:id` / GeoJSON `id` | provenance only |
| `object_type` / `_source` | `objektart` | `compensation_area`, `eco_account`, `replacement_payment_measure`; *Eingriffsverfahren* → rejected |
| `site_name`, `file_reference` | `bezeichnung`, `aktenzeichen` | whitespace / Unicode cleaned |
| `municipality_key` | `gemeinde_schluessel` | 8-digit AGS; 7 digits → leading zero restored + flag |
| `municipality_name` / `_source` | `gemeinde` + VG250 | **official name from the key**; source name kept; mismatch flagged |
| `district_key`, `district_name` | VG250 via key | never derived from a name |
| `cadastral_district_name` / `_key` | `gemarkung` / `gemarkung_schluessel` | cleaned / digits only |
| `compensation_status` / `_source` | `status` | controlled vocabulary; unknown → `NULL` + flag |
| `area_source_m2` | `flaeche_ha` | ha × 10 000; German decimals; ambiguous `12.500` → `NULL` |
| `area_geom_m2` | geometry | area in EPSG:25832 |
| `eco_credits_total` / `_available` | `oekopunkte_gesamt` / `_verfuegbar` | negative → `NULL` + flag; never imputed |
| `registering_authority`, `registered_on` | `eintragungsstelle`, `datum_eintragung` | ISO or `DD.MM.YYYY` only |
| `geom` | geometry | valid `MULTIPOLYGON`, **EPSG:25832** (DB `CHECK ST_IsValid`) |
| `geom_source`, `source_crs` | geometry, `srsName` | exactly as delivered, original CRS |
| `source_layer`, `source_endpoint`, `source_attributes` | — | provenance (full raw properties as JSONB) |
| `geometry_repaired`, `repair_note`, `quality_flags` | — | quality evidence per record |
| `record_hash`, `first/last_seen_run_id`, `last_changed_run_id` | — | change tracking for idempotent upserts |

Natural key **`UNIQUE (state_code, site_id)`**. Supporting tables: `harvest_runs` (status, counts and provenance per run) and
`harvest_issues` (every rejection and warning, with the raw record). Processing details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## 4. Assumptions

1. Layer `rp_oekokonto` holds the LKompVzVO object types (incl. *Eingriffsverfahren*, which is filtered out), with the property
   names in the mapping file. **Unverified, because the service is unreachable**; renaming is a config edit.
2. `objekt_id` (Objektkennung) is stable across harvests. It is the only identifier used.
3. `flaeche_ha` is in hectares (LKompVzVO § 3 states area in ha); a decimal comma is German.
4. `state_code = RP` (German Länder abbreviation; with `country_code = DE` it equals ISO `DE-RP`).
5. Status vocabulary `planned / in_progress / implemented / maintenance / revoked`. "abgeschlossen" stays unmapped until the
   data owner defines it.
6. Eco credits are a plain number with no published unit, stored as-is.

Open questions for the data owner (LUWG / SGD Nord): the correct WFS URL and layer, stability of `Objektkennung`, the status
code list, the eco-credit unit, and the licence/attribution terms.

## 5. Limitations

- **No live data:** the brief's endpoint does not resolve. All loaded data is synthetic; live mode is ready but unverified
  against LUWG.
- Fixture `CountDefault=10` is artificial, chosen to exercise paging.
- `PagingIsTransactionSafe=FALSE` servers can also *skip* a row if data changes mid-harvest. Duplicates are handled; a skip can
  only be detected by comparing with `numberMatched` (reported) and re-running.
- The state boundary is generalised (1:2.5M). The AGS key is authoritative; geometry-only decisions near the border use a 2 km
  tolerance.
- The municipality reference is a snapshot (VG250, 01.01.2026). Later municipal mergers show as `municipality_key_unknown`.
- GeoJSON from a server in a *geographic* CRS is assumed to be lon/lat. A lat/lon server is caught by the state check, not
  auto-corrected.
- `postgis/postgis` has no arm64 image, so it runs emulated on Apple Silicon (slower start).
