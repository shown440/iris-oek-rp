# Security

Companion to the [README](../README.md). Audited 2026-10-28; every control is backed by a test or a reproducible command.

## 1. Controls

| Area | Control | Evidence |
|---|---|---|
| Write scope | All models hard-bound to `iris_staging`; the schema is not configurable | `app/database/base.py`, `test_all_tables_live_in_iris_staging` |
| DB roles | `iris_migrator` owns `iris_staging` (DDL, Alembic only). `iris_harvester` (runtime) has **SELECT/INSERT/UPDATE only**: no DELETE, TRUNCATE, DROP, ALTER or CREATE. **Neither role has any privilege on `iris_core`** | `db/init/20_iris_staging.sh`, `tests/integration/test_load.py` |
| Secrets | `.env` git-ignored and excluded from the image (`.dockerignore`); `.env.example` has placeholders only | `ls -a /app` in the image |
| Container env | No `env_file`: each container gets only its own role's connection URL. The superuser and migrator passwords never reach the harvester | `docker compose run --entrypoint env harvester` |
| Network | PostGIS published on `127.0.0.1` only | `docker compose port db 5432` |
| SSRF via schema | GDAL follows `xs:include`/`xs:import` URLs in a server-supplied XSD (verified). Server schemas are sanitised before GDAL sees them | `test_server_schema_cannot_make_gdal_fetch_urls` (local listener) |
| SSRF via service | Advertised operation URLs and redirects must stay on the endpoint's domain or `IRIS_WFS_ALLOWED_HOSTS`, keep HTTPS, and not resolve to private/loopback/link-local addresses | `test_wfs_harvester.py` |
| Resource limits | Streamed responses capped at `IRIS_MAX_RESPONSE_MB`; `MAX_FEATURES` / `MAX_PAGES` caps | `test_oversized_response_is_rejected` |
| XML | lxml with entity resolution and network disabled (XXE); GDAL WFS schema download disabled | `test_xml_entities_are_not_expanded` |
| Permissions | 401/403 never retried or bypassed; declared `AccessConstraints` need explicit acknowledgement | `test_access_constraints_block_…` |
| Supply chain | Hash-locked dependencies (`pip --require-hashes`), binary wheels only, base images pinned by digest, Actions pinned by commit SHA, read-only CI token | `requirements*.txt`, `Dockerfile`, `ci.yml` |
| Static analysis | ruff incl. flake8-bandit (`S`) rules | `make lint` |
| Runtime | Non-root container user (uid 10001) | `Dockerfile` |

## 2. Verification results

| Check | Method | Result |
|---|---|---|
| Known CVEs in all pinned packages | `pip-audit --require-hashes -r requirements-dev.txt` | ✅ no known vulnerabilities |
| Secrets in files / git history | pattern scan of `git log -p` | ✅ none |
| `.env` in the Docker image | `ls -a /app` | ✅ absent |
| Credentials visible to app containers | `env` inside harvester / migrate | ✅ own role's URL only (was: superuser password, **fixed**) |
| DB exposure | `docker compose port db 5432` | ✅ `127.0.0.1` only (was `0.0.0.0`, **fixed**) |
| SSRF via server XSD | local HTTP listener | ✅ 0 requests (was 1, **fixed**; the regression test fails without the fix) |
| SSRF via advertised URLs / redirects | mocked hostile capabilities + 302 | ✅ blocked, never called |
| Least privilege | `has_table_privilege` and denied statements | ✅ runtime role DML-only, no `iris_core` access |
| Static analysis | ruff incl. bandit rules | ✅ clean |

## 3. Dependencies

Top-level pins live in `requirements.in` / `requirements-dev.in`. The hash-locked files are regenerated with:

```bash
uv pip compile --python-version 3.14 --generate-hashes requirements.in -o requirements.txt
uv pip compile --python-version 3.14 --generate-hashes requirements-dev.in -o requirements-dev.txt
```
