# LUWG fixture (synthetic)

**Why a fixture:** the endpoint in the brief, `https://geodaten-wasser.rlp.de/wfs/kompensation`,
does not resolve in public DNS (`NXDOMAIN`, checked against the system resolver and 8.8.8.8 on
2026-10-28). The brief allows a fixture-based demonstrator in that case. No access restriction
was bypassed.

| File | What it is |
|---|---|
| `luwg/capabilities.xml` | Hand-written WFS 2.0.0 capabilities, modelled on a real RLP MapServer response (`ImplementsResultPaging=TRUE`, `PagingIsTransactionSafe=FALSE`). `CountDefault=10` forces multi-page harvesting. |
| `luwg/describe_feature_type.xsd` | Feature schema. Property names follow LKompVzVO § 3 (Objektkennung, Objektbezeichnung, Aktenzeichen, Fläche in ha, ...). |
| `luwg/features.json` | 55 features (EPSG:25832), served page by page by `FixtureTransport`. |
| `luwg/expected_outcomes.json` | Golden file: the expected outcome of every feature, asserted by `tests/unit/test_transformer.py`. |
| `luwg/sample_page.gml` | Two features as WFS 2.0 / GML 3.2, used to test the GML parsing path. |

Regenerate deterministically: `python scripts/generate_fixtures.py` (seed 56). Site locations are
points inside real RLP municipalities (BKG VG250). All other attributes are invented.

## Edge-case catalogue

| # | Case | Expected outcome |
|---|---|---|
| E01 | Self-intersecting "bow-tie" polygon | accepted, repaired with `make_valid(structure)`, flag `geometry_repaired` |
| E02 | Polygon with hole | accepted unchanged |
| E03 | 3D coordinates | accepted, flag `z_dropped` |
| E04 | `geometry: null` | rejected `missing_geometry` |
| E05 | Point instead of polygon | rejected `unsupported_geometry_type` |
| E06 | `objekt_id` is whitespace only | rejected `missing_site_id` |
| E07 | Same feature delivered twice across page 1 / page 2 | loaded once, warning `duplicate_in_source` |
| E08 | Same `objekt_id`, different content | both rejected `conflicting_duplicate` |
| E09 | Municipality key `08222000` (Mannheim, Baden-Württemberg) | rejected `outside_state` |
| E10 | X/Y axes swapped | rejected `geometry_outside_state` |
| E11 | Object type *Eingriffsverfahren* (an intervention, not compensation) | rejected `out_of_domain` |
| E12 | Status "vorläufig gesichert" | accepted, status NULL, flag `unmapped_status` |
| E13 | Status "abgeschlossen" (ambiguous) | accepted, status NULL, flag `unmapped_status` |
| E14 | Area "12.500" (12.5 or 12 500?) | accepted, area NULL, flag `ambiguous_number` |
| E15 | Mojibake "WeinstraÃŸe" | accepted, repaired, flag `name_encoding_repaired` |
| E16 | Name "Mainz" with Koblenz key | accepted, official name from key, flag `municipality_name_mismatch` |
| E17 | Key `7211000` (leading zero lost) | accepted as `07211000`, flag `municipality_key_zero_padded` |
| E18 | Declared area 3× the geometry area | accepted, flag `area_mismatch` |
| E19 | Negative available eco credits | accepted, value NULL, flag `invalid_eco_credits` |
| E20 | Available credits > total credits | accepted, flag `eco_credits_inconsistent` |
| E21 | Collinear sliver (zero area) | rejected `unrepairable_geometry` |
| E22 | Key `07999999` (not in VG250) | accepted, flag `municipality_key_unknown` |
| E23 | Date "2021/13/45" | accepted, date NULL, flag `invalid_date` |
| E25 | No municipality key | accepted, state confirmed by geometry, nothing guessed |
| E26 | Unknown object type | rejected `unknown_object_type` |
| E27 | Ring with one coordinate | rejected `geometry_unparseable` |
| E29 | 0.25 m² polygon | rejected `degenerate_geometry` |
