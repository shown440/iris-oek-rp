-- Validation and sample spatial queries for iris_staging.compensation_sites.
--
-- Run all of them:   python -m app validate      (or: make validate)
-- Or paste any block into psql.
--
-- "expect: zero_rows" blocks are acceptance checks: any returned row is a
-- failure. "expect: info" blocks are evidence / reproducible examples.

-- name: no_duplicate_natural_keys
-- expect: zero_rows
SELECT state_code, site_id, count(*) AS copies
FROM iris_staging.compensation_sites
GROUP BY state_code, site_id
HAVING count(*) > 1;

-- name: working_geometry_valid_multipolygon_25832
-- expect: zero_rows
SELECT site_id, ST_SRID(geom) AS srid, GeometryType(geom) AS type, ST_IsValidReason(geom) AS reason
FROM iris_staging.compensation_sites
WHERE ST_SRID(geom) <> 25832
   OR GeometryType(geom) <> 'MULTIPOLYGON'
   OR NOT ST_IsValid(geom)
   OR ST_IsEmpty(geom);

-- name: records_belong_to_rhineland_palatinate
-- expect: zero_rows
-- State key RP, municipality key prefix 07 (when present), and the
-- working geometry inside the RLP envelope in EPSG:25832.
SELECT site_id, state_code, municipality_key
FROM iris_staging.compensation_sites
WHERE state_code <> 'RP'
   OR (municipality_key IS NOT NULL AND municipality_key NOT LIKE '07%')
   OR NOT ST_Within(
        ST_PointOnSurface(geom),
        ST_MakeEnvelope(288000, 5423000, 466000, 5649000, 25832)
      );

-- name: records_belong_to_compensation_domain
-- expect: zero_rows
SELECT site_id, object_type, object_type_source, source_layer
FROM iris_staging.compensation_sites
WHERE source_layer NOT LIKE '%rp_oekokonto'
   OR (object_type IS NOT NULL
       AND object_type NOT IN ('compensation_area', 'eco_account', 'replacement_payment_measure'));

-- name: provenance_retained
-- expect: zero_rows
SELECT site_id
FROM iris_staging.compensation_sites
WHERE source_endpoint IS NULL
   OR source_layer IS NULL
   OR source_crs IS NULL
   OR geom_source IS NULL
   OR ST_SRID(geom_source) = 0
   OR source_attributes = '{}'::jsonb
   OR first_seen_run_id IS NULL;

-- name: area_consistent_with_geometry
-- expect: zero_rows
-- area_geom_m2 must equal the PostGIS area of the stored geometry (+-1 m2).
SELECT site_id, area_geom_m2, round(ST_Area(geom)::numeric, 2) AS st_area
FROM iris_staging.compensation_sites
WHERE abs(area_geom_m2 - ST_Area(geom)::numeric) > 1;

-- name: latest_run_outcome_counts
-- expect: info
SELECT run_id, status, source_mode, started_at,
       counts->>'fetched'                 AS fetched,
       counts->>'accepted'                AS accepted,
       counts->>'rejected'                AS rejected,
       counts->>'duplicates_collapsed'    AS duplicates_collapsed,
       counts->'database'->>'inserted'    AS db_inserted,
       counts->'database'->>'updated'     AS db_updated,
       counts->'database'->>'unchanged'   AS db_unchanged
FROM iris_staging.harvest_runs
ORDER BY started_at DESC
LIMIT 5;

-- name: latest_run_issues_by_code
-- expect: info
SELECT i.severity, i.code, count(*) AS records
FROM iris_staging.harvest_issues i
WHERE i.run_id = (SELECT run_id FROM iris_staging.harvest_runs ORDER BY started_at DESC LIMIT 1)
GROUP BY i.severity, i.code
ORDER BY i.severity, records DESC, i.code;

-- name: spatial_area_and_credits_by_district
-- expect: info
-- Reproducible spatial aggregation: compensation area (ha) and available
-- eco credits per district (Landkreis / kreisfreie Stadt).
SELECT coalesce(district_name, '(unknown)')           AS district,
       count(*)                                       AS sites,
       round((sum(ST_Area(geom)) / 10000)::numeric, 2) AS area_ha,
       sum(eco_credits_available)                     AS eco_credits_available
FROM iris_staging.compensation_sites
WHERE state_code = 'RP'
GROUP BY district_name
ORDER BY area_ha DESC;

-- name: spatial_sites_within_5km_of_mainz_cathedral
-- expect: info
-- Reproducible proximity query (point given in WGS84, evaluated in EPSG:25832).
SELECT site_id, site_name, municipality_name, compensation_status,
       round(ST_Distance(geom, p.pt)::numeric) AS distance_m
FROM iris_staging.compensation_sites,
     (SELECT ST_Transform(ST_SetSRID(ST_MakePoint(8.2741, 49.9990), 4326), 25832) AS pt) p
WHERE ST_DWithin(geom, p.pt, 5000)
ORDER BY distance_m;
