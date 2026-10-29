# syntax=docker/dockerfile:1
# Base image pinned by digest (python:3.14-slim) for reproducible builds.
FROM python:3.14-slim@sha256:cd89c30eda26ee911a4611b18680086e0ea42e6f5f8cbe653d07fa7958c9cec8

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 iris

# Hash-locked, binary wheels only (shapely, pyproj, pyogrio and psycopg bundle
# GEOS/PROJ/GDAL/libpq). requirements-dev.txt is a superset of requirements.txt.
COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=false
RUN if [ "$INSTALL_DEV" = "true" ]; then LOCK=requirements-dev.txt; else LOCK=requirements.txt; fi \
 && pip install --require-hashes --only-binary=:all: -r "$LOCK"

COPY --chown=iris:iris . .
RUN mkdir -p /app/data && chown iris:iris /app /app/data

USER iris

ENTRYPOINT ["python", "-m", "app"]
CMD ["--help"]
