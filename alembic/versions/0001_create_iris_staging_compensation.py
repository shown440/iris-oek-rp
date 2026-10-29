"""create iris_staging compensation tables

Revision ID: 0001_compensation
Revises:
Create Date: 2026-10-28 23:30:00

"""

from typing import Sequence, Union

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_compensation"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "iris_staging"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    """Upgrade schema."""

    has_postgis = op.get_bind().exec_driver_sql("SELECT 1 FROM pg_extension WHERE extname = 'postgis'").scalar()

    if not has_postgis:
        raise RuntimeError("PostGIS extension is missing - ask a DBA to run CREATE EXTENSION postgis")

    has_schema = op.get_bind().exec_driver_sql(f"SELECT 1 FROM pg_namespace WHERE nspname = '{SCHEMA}'").scalar()

    if not has_schema:
        op.execute(f"CREATE SCHEMA {SCHEMA}")

    # ------------------------------------------------------
    # harvest_runs
    # ------------------------------------------------------

    op.create_table(
        "harvest_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state_code", sa.String(2), nullable=False),
        sa.Column("source_mode", sa.String(10), nullable=False),
        sa.Column("source_endpoint", sa.Text(), nullable=False),
        sa.Column("source_layer", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counts", postgresql.JSONB(), nullable=True),
        sa.Column("provenance", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("run_id", name="pk_harvest_runs"),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_harvest_runs_status"),
        schema=SCHEMA,
    )

    # ------------------------------------------------------
    # compensation_sites
    # ------------------------------------------------------

    op.create_table(
        "compensation_sites",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("state_code", sa.String(2), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("site_id", sa.String(128), nullable=False),
        sa.Column("source_feature_id", sa.String(255), nullable=True),
        sa.Column("source_layer", sa.String(255), nullable=False),
        sa.Column("source_endpoint", sa.Text(), nullable=False),
        sa.Column("source_crs", sa.String(32), nullable=False),
        sa.Column("source_attributes", postgresql.JSONB(), nullable=False),
        sa.Column("object_type", sa.String(40), nullable=True),
        sa.Column("object_type_source", sa.String(100), nullable=True),
        sa.Column("site_name", sa.Text(), nullable=True),
        sa.Column("file_reference", sa.String(255), nullable=True),
        sa.Column("municipality_key", sa.String(8), nullable=True),
        sa.Column("municipality_name", sa.String(150), nullable=True),
        sa.Column("municipality_name_source", sa.String(150), nullable=True),
        sa.Column("district_key", sa.String(5), nullable=True),
        sa.Column("district_name", sa.String(150), nullable=True),
        sa.Column("cadastral_district_key", sa.String(20), nullable=True),
        sa.Column("cadastral_district_name", sa.String(150), nullable=True),
        sa.Column("compensation_status", sa.String(20), nullable=True),
        sa.Column("compensation_status_source", sa.String(100), nullable=True),
        sa.Column("area_source_m2", sa.Numeric(14, 2), nullable=True),
        sa.Column("area_geom_m2", sa.Numeric(14, 2), nullable=False),
        sa.Column("eco_credits_total", sa.Numeric(16, 2), nullable=True),
        sa.Column("eco_credits_available", sa.Numeric(16, 2), nullable=True),
        sa.Column("registering_authority", sa.String(255), nullable=True),
        sa.Column("registered_on", sa.Date(), nullable=True),
        sa.Column(
            "geom_source",
            geoalchemy2.types.Geometry(geometry_type=None, spatial_index=False, from_text="ST_GeomFromEWKT"),
            nullable=False,
        ),
        sa.Column(
            "geom",
            geoalchemy2.types.Geometry(
                geometry_type="MULTIPOLYGON", srid=25832, spatial_index=False, from_text="ST_GeomFromEWKT"
            ),
            nullable=False,
        ),
        sa.Column("geometry_repaired", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("repair_note", sa.Text(), nullable=True),
        sa.Column("quality_flags", postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("record_hash", sa.String(64), nullable=False),
        sa.Column("first_seen_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_seen_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("last_changed_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("harvested_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_compensation_sites"),
        sa.UniqueConstraint("state_code", "site_id", name="uq_compensation_sites_state_site"),
        sa.CheckConstraint("ST_IsValid(geom) AND NOT ST_IsEmpty(geom)", name="ck_compensation_sites_geom_valid"),
        sa.CheckConstraint("state_code ~ '^[A-Z]{2}$'", name="ck_compensation_sites_state_code_format"),
        sa.CheckConstraint(
            "compensation_status IS NULL OR compensation_status IN "
            "('planned', 'in_progress', 'implemented', 'maintenance', 'revoked')",
            name="ck_compensation_sites_status_vocabulary",
        ),
        sa.CheckConstraint(
            "object_type IS NULL OR object_type IN "
            "('compensation_area', 'eco_account', 'replacement_payment_measure')",
            name="ck_compensation_sites_object_type_vocabulary",
        ),
        sa.CheckConstraint("area_geom_m2 > 0", name="ck_compensation_sites_area_positive"),
        sa.ForeignKeyConstraint(
            ["first_seen_run_id"],
            [f"{SCHEMA}.harvest_runs.run_id"],
            name="fk_compensation_sites_first_seen_run_id_harvest_runs",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_run_id"],
            [f"{SCHEMA}.harvest_runs.run_id"],
            name="fk_compensation_sites_last_seen_run_id_harvest_runs",
        ),
        sa.ForeignKeyConstraint(
            ["last_changed_run_id"],
            [f"{SCHEMA}.harvest_runs.run_id"],
            name="fk_compensation_sites_last_changed_run_id_harvest_runs",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_compensation_sites_geom", "compensation_sites", ["geom"], schema=SCHEMA, postgresql_using="gist"
    )
    op.create_index(
        "ix_compensation_sites_municipality_key", "compensation_sites", ["municipality_key"], schema=SCHEMA
    )
    op.create_index("ix_compensation_sites_status", "compensation_sites", ["compensation_status"], schema=SCHEMA)
    op.create_index("ix_compensation_sites_last_seen", "compensation_sites", ["last_seen_run_id"], schema=SCHEMA)

    # ------------------------------------------------------
    # harvest_issues
    # ------------------------------------------------------

    op.create_table(
        "harvest_issues",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("site_id", sa.String(128), nullable=True),
        sa.Column("source_feature_id", sa.String(255), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("raw_record", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_harvest_issues"),
        sa.CheckConstraint("severity IN ('rejected', 'warning')", name="ck_harvest_issues_severity"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{SCHEMA}.harvest_runs.run_id"],
            name="fk_harvest_issues_run_id_harvest_runs",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_harvest_issues_run_code", "harvest_issues", ["run_id", "code"], schema=SCHEMA)


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index("ix_harvest_issues_run_code", table_name="harvest_issues", schema=SCHEMA)
    op.drop_table("harvest_issues", schema=SCHEMA)
    op.drop_index("ix_compensation_sites_last_seen", table_name="compensation_sites", schema=SCHEMA)
    op.drop_index("ix_compensation_sites_status", table_name="compensation_sites", schema=SCHEMA)
    op.drop_index("ix_compensation_sites_municipality_key", table_name="compensation_sites", schema=SCHEMA)
    op.drop_index(
        "ix_compensation_sites_geom", table_name="compensation_sites", schema=SCHEMA, postgresql_using="gist"
    )
    op.drop_table("compensation_sites", schema=SCHEMA)
    op.drop_table("harvest_runs", schema=SCHEMA)
