# Quality report - run `7f4db2c3-0afe-4081-874d-508ee0d48cf3`

- **Status:** succeeded
- **Source mode:** fixture
- **Endpoint:** `fixture://luwg/wfs/kompensation`
- **Layer:** `luwg:rp_oekokonto`
- **Started / finished (UTC):** 2026-10-29T04:01:43.275576+00:00 / 2026-10-29T04:01:43.403882+00:00

## Provenance

| Item | Value |
|---|---|
| Service | Kompensationsverzeichnis Rheinland-Pfalz (LUWG) - SYNTHETIC FIXTURE (Landesamt für Umwelt, Wasserwirtschaft und Gewerbeaufsicht Rheinland-Pfalz (LUWG)) |
| WFS version | 2.0.0 |
| Licence / fees | geldleistungsfrei; Datenlizenz Deutschland - Namensnennung - Version 2.0 (assumed for fixture) |
| Access constraints | NONE |
| Source CRS | urn:ogc:def:crs:EPSG::25832 |
| Output format | application/json; subtype=geojson |
| Paging | ResultPaging=True, TransactionSafe=False, CountDefault=10, page size=10 |
| Sort | objekt_id ASC |
| numberMatched | 55 |
| Mapped fields missing in schema | [] |
| Schema fields not mapped (kept in source_attributes) | ['flurstuecke', 'massnahme', 'zielzustand'] |
| Verify-before-use blockers (permissions / schema) | none |

### Raw responses

| File | Bytes | SHA-256 |
|---|---:|---|
| capabilities.xml | 4,846 | `ab5d5980f401a36c...` |
| describe_feature_type.xsd | 2,645 | `11fd02d570c264bf...` |
| hits.xml | 176 | `5e531bc57fa1d906...` |
| page_0001.json | 10,343 | `63b4b2ec861e2dfe...` |
| page_0002.json | 10,336 | `fac42302e190737f...` |
| page_0003.json | 10,172 | `c16eb8bcd1455704...` |
| page_0004.json | 9,144 | `3f85701e86f5f058...` |
| page_0005.json | 10,406 | `f0a17288bd14ed48...` |
| page_0006.json | 5,247 | `3304d0f9f3644fa6...` |

## Counts by processing outcome

| Outcome | Records |
|---|---:|
| Fetched from source | 55 |
| Accepted | 42 |
| - of which with quality warnings | 13 |
| - of which geometry repaired | 1 |
| Rejected | 12 |
| Identical duplicates collapsed | 1 |
| **Reconciles (fetched = accepted + rejected + collapsed)** | **True** |
| DB inserted | 0 |
| DB updated | 0 |
| DB unchanged (idempotent no-op) | 42 |
| In staging but not seen this run | 0 |

## Rejected by reason

| Code | Count |
|---|---:|
| `conflicting_duplicate` | 2 |
| `missing_site_id` | 1 |
| `missing_geometry` | 1 |
| `unsupported_geometry_type` | 1 |
| `unrepairable_geometry` | 1 |
| `degenerate_geometry` | 1 |
| `geometry_unparseable` | 1 |
| `outside_state` | 1 |
| `geometry_outside_state` | 1 |
| `out_of_domain` | 1 |
| `unknown_object_type` | 1 |

## Warnings by reason

| Code | Count |
|---|---:|
| `unmapped_status` | 2 |
| `geometry_repaired` | 1 |
| `z_dropped` | 1 |
| `ambiguous_number` | 1 |
| `name_encoding_repaired` | 1 |
| `municipality_name_mismatch` | 1 |
| `municipality_key_zero_padded` | 1 |
| `area_mismatch` | 1 |
| `invalid_eco_credits` | 1 |
| `eco_credits_inconsistent` | 1 |
| `municipality_key_unknown` | 1 |
| `invalid_date` | 1 |
| `duplicate_in_source` | 1 |

## Rejected records

| site_id | Stage | Code | Detail |
|---|---|---|---|
| - | validate | `missing_site_id` | 'objekt_id' is empty |
| RP-OEK-2026-0031 | geometry | `missing_geometry` | missing_geometry |
| RP-OEK-2026-0032 | geometry | `unsupported_geometry_type` | unsupported_geometry_type |
| RP-OEK-2026-0033 | geometry | `unrepairable_geometry` | source: Self-intersection[438520 5465813] |
| RP-OEK-2026-0034 | geometry | `degenerate_geometry` | degenerate_geometry |
| RP-OEK-2026-0035 | parse | `geometry_unparseable` | ValueError: A linearring requires at least 4 coordinates. |
| RP-OEK-2026-0038 | domain | `outside_state` | municipality key 08222000 is not in state RP |
| RP-OEK-2026-0039 | domain | `geometry_outside_state` | geometry outside the state boundary (CRS / axis-order error?) |
| RP-OEK-2026-0040 | domain | `out_of_domain` | 'Eingriffsverfahren' is not a compensation site |
| RP-OEK-2026-0041 | domain | `unknown_object_type` | object type 'Biotopverbundfläche' |
| RP-OEK-2026-0037 | dedup | `conflicting_duplicate` | 2 different versions of the same site_id; none loaded |
| RP-OEK-2026-0037 | dedup | `conflicting_duplicate` | 2 different versions of the same site_id; none loaded |

## Attribute completeness (accepted records)

| Column | Non-null % |
|---|---:|
| object_type | 100.0 |
| site_name | 100.0 |
| file_reference | 100.0 |
| municipality_key | 97.6 |
| municipality_name | 100.0 |
| district_name | 95.2 |
| cadastral_district_key | 97.6 |
| cadastral_district_name | 97.6 |
| compensation_status | 95.2 |
| area_source_m2 | 95.2 |
| eco_credits_total | 66.7 |
| eco_credits_available | 64.3 |
| registering_authority | 100.0 |
| registered_on | 97.6 |
