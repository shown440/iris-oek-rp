# IRIS-OEK-RP - common tasks (Docker is the reference environment).
COMPOSE = docker compose
RUN     = $(COMPOSE) run --rm

.PHONY: help env dirs build up migrate harvest harvest-live discover discover-live validate export demo test lint down clean

help:            ## list targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

env:             ## create .env from the example (edit the passwords!)
	@test -f .env || cp .env.example .env

# The container runs as a non-root user (uid 10001) that does not exist on the
# host, so the bind-mounted output directories must be group/other writable.
# Without this, a Linux host rejects the container's writes to data/runs.
dirs:            ## make the bind-mounted output directories container-writable
	@mkdir -p data examples
	@chmod a+rwX data examples

build: env       ## build the harvester image
	$(COMPOSE) build

up: env dirs     ## start PostGIS
	$(COMPOSE) up -d --wait db

migrate: up      ## create iris_staging tables (alembic upgrade head)
	$(RUN) migrate

harvest: migrate ## harvest the fixture into iris_staging
	$(RUN) harvester harvest

harvest-live: migrate ## harvest the live WFS named in the brief
	$(RUN) harvester harvest --source live

discover: dirs   ## capabilities + schema check (fixture)
	$(RUN) --no-deps harvester discover

discover-live: dirs ## capabilities + schema check (live endpoint)
	$(RUN) --no-deps harvester discover --source live

validate:        ## acceptance SQL checks + sample spatial queries
	$(RUN) harvester validate

export: dirs     ## sample GeoJSON export -> examples/sample_export.geojson
	$(RUN) harvester export

demo: harvest    ## full demo: harvest twice (idempotency), validate, export
	$(RUN) harvester harvest
	$(RUN) harvester validate
	$(RUN) harvester export

test: up         ## unit + integration tests in Docker
	$(RUN) tests

lint:            ## ruff
	$(RUN) --no-deps --entrypoint ruff harvester check .

down:            ## stop containers (keeps data)
	$(COMPOSE) down

clean:           ## stop containers and delete the database volume
	$(COMPOSE) down -v
