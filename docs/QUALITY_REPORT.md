# Quality and error report: IRIS-OEK-RP

Run date 2026-10-28 · Python 3.14 · PostGIS 17-3.5 · data: synthetic fixture (see [README §2](../README.md#2-data-provenance))

Scope as requested: rejected data, edge cases and evidence for acceptance.

## 1. Verdict against the acceptance criteria

| Criterion | Evidence | Status |
|---|---|---|
| Records belong to the correct **state** | AGS prefix `07` enforced; VG2500 boundary check; Mannheim (`08222000`) and an axis-swapped record rejected; SQL check `records_belong_to_rhineland_palatinate` = 0 rows | ✅ |
| Correct **layer** | Layer resolved from capabilities (exact name, no fuzzy substitution); `source_layer = luwg:rp_oekokonto` on every row | ✅ |
| Correct **compensation-site domain** | LKompVzVO object types; *Eingriffsverfahren* rejected `out_of_domain`, unknown types rejected; SQL check = 0 rows | ✅ |
| **Provenance** retained | endpoint, layer, source CRS, original geometry (`geom_source`), all raw attributes (JSONB), run ids, raw responses with SHA-256 | ✅ |
| **Valid projected geometry** | `geom` = valid `MULTIPOLYGON` EPSG:25832, enforced by a DB CHECK constraint; SQL check = 0 rows | ✅ |
| **Re-run creates no duplicates** | run 2: `inserted 0 · updated 0 · unchanged 42`; `UNIQUE(state_code, site_id)`; SQL check = 0 rows | ✅ |
| **Quality issues reported** | `harvest_issues` table + per-run `quality_report.{json,md}` + this document | ✅ |
| Live LUWG data | endpoint NXDOMAIN; fixture demonstrator used as permitted | ⚠️ limitation |

## 2. Counts by processing outcome (fixture run 1)

| Outcome | Records |
|---|---:|
| Fetched (6 pages × ≤10, sorted by `objekt_id`) | 55 |
| Accepted | **42** |
| · with quality warnings | 13 |
| · geometry repaired | 1 |
| Rejected | **12** |
| Identical duplicate collapsed | 1 |
| Reconciles (55 = 42 + 12 + 1) | ✅ |
| DB inserted / updated / unchanged, run 1 | 42 / 0 / 0 |
| DB inserted / updated / unchanged, run 2 (same input) | **0 / 0 / 42** |

## 3. Rejected data

| Code | n | Example cause | Why rejected, not fixed |
|---|---:|---|---|
| `conflicting_duplicate` | 2 | same `objekt_id`, statuses "umgesetzt" vs "in Pflege" | choosing one version would be a guess |
| `missing_site_id` | 1 | `objekt_id` blank | no stable key means no idempotent upsert |
| `missing_geometry` | 1 | `geometry: null` | site without location |
| `unsupported_geometry_type` | 1 | Point | compensation sites are areas |
| `geometry_unparseable` | 1 | ring with one coordinate | not a geometry |
| `unrepairable_geometry` | 1 | collinear sliver | repair would invent an area |
| `degenerate_geometry` | 1 | 0.25 m² | below `MIN_AREA_M2` |
| `outside_state` | 1 | AGS `08222000` (Mannheim, BW) | wrong state |
| `geometry_outside_state` | 1 | X/Y swapped | CRS/axis error, not auto-corrected |
| `out_of_domain` | 1 | *Eingriffsverfahren* | an intervention, not a compensation site |
| `unknown_object_type` | 1 | "Biotopverbundfläche" | domain unverifiable |

Every rejection is stored in `iris_staging.harvest_issues` with its raw attributes.

## 4. Edge cases accepted with warnings

| Flag | Handling |
|---|---|
| `geometry_repaired` | bow-tie split into 2 valid polygons; original kept in `geom_source` |
| `z_dropped` | 3D → 2D |
| `duplicate_in_source` | identical copy across a page boundary (`PagingIsTransactionSafe=FALSE`), loaded once |
| `unmapped_status` (2) | "vorläufig gesichert", "abgeschlossen": status `NULL`, source value kept |
| `ambiguous_number` | area "12.500", left as `NULL` |
| `area_mismatch` | declared area 3× the geometry area, both values kept |
| `invalid_eco_credits` | negative credits set to `NULL` |
| `eco_credits_inconsistent` | available > total, flagged |
| `name_encoding_repaired` | "WeinstraÃŸe" → "Weinstraße" |
| `municipality_name_mismatch` | name "Mainz" with the Koblenz key: official name from the key, source kept |
| `municipality_key_zero_padded` | `7211000` → `07211000` |
| `municipality_key_unknown` | `07999999`, not in VG250 |
| `invalid_date` | "2021/13/45" → `NULL` |

## 5. Evidence for error handling (tested)

| Scenario | Behaviour |
|---|---|
| Endpoint unreachable (the real brief URL) | 3 retries with backoff, run `failed`, error recorded, no site rows touched ([report](../examples/live_attempt_quality_report.md)) |
| Stable id or geometry missing from the published schema | `SchemaMismatchError` before any feature request (`discover` lists it under `harvest_blockers`) |
| `AccessConstraints` declared, not acknowledged / HTTP 401-403 | stops before any feature request / immediately; never bypassed |
| Layer not advertised | abort before any feature request, available layers listed |
| OWS ExceptionReport with HTTP 200 | raised as `WfsServiceError` |
| DB error in batch 2 | full rollback (0 rows), run `failed`, `run_failed` issue |
| Concurrent harvest | blocked by advisory lock |
| Write attempt to `iris_core` | `permission denied` |

Security verification is reported separately in [SECURITY.md](SECURITY.md).
