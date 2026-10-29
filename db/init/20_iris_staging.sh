#!/bin/bash
# Runs once, on first start of the PostGIS container (after 10_postgis.sh).
#
#   IRIS_MIGRATOR_USER  owns iris_staging; DDL only (used by Alembic)
#   IRIS_DB_USER        runtime harvester; SELECT / INSERT / UPDATE only -
#                       no DROP, DELETE, TRUNCATE, ALTER, no CREATE anywhere
#   iris_core           exists (owned by the superuser, as in the real
#                       platform); neither role has any privilege on it
#   iris_test           separate database for the integration tests
#
# Changing these roles later requires `make clean` (re-initialises the volume).
set -euo pipefail

: "${IRIS_MIGRATOR_USER:?IRIS_MIGRATOR_USER must be set}"
: "${IRIS_MIGRATOR_PASSWORD:?IRIS_MIGRATOR_PASSWORD must be set}"
: "${IRIS_DB_USER:?IRIS_DB_USER must be set}"
: "${IRIS_DB_PASSWORD:?IRIS_DB_PASSWORD must be set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v migrator="$IRIS_MIGRATOR_USER" -v migrator_password="$IRIS_MIGRATOR_PASSWORD" \
     -v app_user="$IRIS_DB_USER" -v app_password="$IRIS_DB_PASSWORD" <<'SQL'
CREATE ROLE :"migrator" LOGIN PASSWORD :'migrator_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE ROLE :"app_user" LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE iris_test OWNER :"migrator";
SQL

for db in "$POSTGRES_DB" iris_test; do
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$db" \
     -v migrator="$IRIS_MIGRATOR_USER" -v app_user="$IRIS_DB_USER" <<'SQL'
CREATE EXTENSION IF NOT EXISTS postgis;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA IF NOT EXISTS iris_core;
REVOKE ALL ON SCHEMA iris_core FROM PUBLIC;

CREATE SCHEMA IF NOT EXISTS iris_staging AUTHORIZATION :"migrator";
GRANT USAGE ON SCHEMA iris_staging TO :"app_user";

-- Applies to every table / sequence the migrator creates from now on.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator" IN SCHEMA iris_staging
    GRANT SELECT, INSERT, UPDATE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator" IN SCHEMA iris_staging
    GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
SQL
done
