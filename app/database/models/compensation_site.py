import uuid
from datetime import date
from datetime import datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import BigInteger
from sqlalchemy import Boolean
from sqlalchemy import CheckConstraint
from sqlalchemy import Date
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import Numeric
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import UniqueConstraint
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.entity import BaseEntity

COMPENSATION_STATUSES = ("planned", "in_progress", "implemented", "maintenance", "revoked")

OBJECT_TYPES = ("compensation_area", "eco_account", "replacement_payment_measure")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IS NULL OR {column} IN ({', '.join(repr(v) for v in values)})"


class CompensationSite(BaseEntity):
    """
    Registered compensation / eco-account site (Rhineland-Palatinate,
    LUWG WFS layer rp_oekokonto).

    Natural key: (state_code, site_id). geom is the working geometry in
    EPSG:25832, guaranteed valid by a CHECK constraint; geom_source keeps
    the geometry exactly as delivered, in its original CRS.
    """

    __tablename__ = "compensation_sites"

    __table_args__ = (
        UniqueConstraint("state_code", "site_id", name="uq_compensation_sites_state_site"),
        CheckConstraint("ST_IsValid(geom) AND NOT ST_IsEmpty(geom)", name="geom_valid"),
        CheckConstraint("state_code ~ '^[A-Z]{2}$'", name="state_code_format"),
        CheckConstraint(_in("compensation_status", COMPENSATION_STATUSES), name="status_vocabulary"),
        CheckConstraint(_in("object_type", OBJECT_TYPES), name="object_type_vocabulary"),
        CheckConstraint("area_geom_m2 > 0", name="area_positive"),
        Index("ix_compensation_sites_geom", "geom", postgresql_using="gist"),
        Index("ix_compensation_sites_municipality_key", "municipality_key"),
        Index("ix_compensation_sites_status", "compensation_status"),
        Index("ix_compensation_sites_last_seen", "last_seen_run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ---------------------------------------------------
    # Keys
    # ---------------------------------------------------

    state_code: Mapped[str] = mapped_column(String(2), nullable=False)

    country_code: Mapped[str] = mapped_column(String(2), nullable=False)

    site_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # ---------------------------------------------------
    # Provenance
    # ---------------------------------------------------

    source_feature_id: Mapped[str | None] = mapped_column(String(255))

    source_layer: Mapped[str] = mapped_column(String(255), nullable=False)

    source_endpoint: Mapped[str] = mapped_column(Text, nullable=False)

    source_crs: Mapped[str] = mapped_column(String(32), nullable=False)

    source_attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    # ---------------------------------------------------
    # Domain
    # ---------------------------------------------------

    object_type: Mapped[str | None] = mapped_column(String(40))

    object_type_source: Mapped[str | None] = mapped_column(String(100))

    site_name: Mapped[str | None] = mapped_column(Text)

    file_reference: Mapped[str | None] = mapped_column(String(255))

    municipality_key: Mapped[str | None] = mapped_column(String(8))

    municipality_name: Mapped[str | None] = mapped_column(String(150))

    municipality_name_source: Mapped[str | None] = mapped_column(String(150))

    district_key: Mapped[str | None] = mapped_column(String(5))

    district_name: Mapped[str | None] = mapped_column(String(150))

    cadastral_district_key: Mapped[str | None] = mapped_column(String(20))

    cadastral_district_name: Mapped[str | None] = mapped_column(String(150))

    compensation_status: Mapped[str | None] = mapped_column(String(20))

    compensation_status_source: Mapped[str | None] = mapped_column(String(100))

    area_source_m2: Mapped[float | None] = mapped_column(Numeric(14, 2, asdecimal=False))

    area_geom_m2: Mapped[float] = mapped_column(Numeric(14, 2, asdecimal=False), nullable=False)

    eco_credits_total: Mapped[float | None] = mapped_column(Numeric(16, 2, asdecimal=False))

    eco_credits_available: Mapped[float | None] = mapped_column(Numeric(16, 2, asdecimal=False))

    registering_authority: Mapped[str | None] = mapped_column(String(255))

    registered_on: Mapped[date | None] = mapped_column(Date)

    # ---------------------------------------------------
    # Geometry
    # ---------------------------------------------------

    geom_source: Mapped[Any] = mapped_column(
        Geometry(geometry_type=None, spatial_index=False),
        nullable=False,
    )

    geom: Mapped[Any] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=25832, spatial_index=False),
        nullable=False,
    )

    geometry_repaired: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    repair_note: Mapped[str | None] = mapped_column(Text)

    # ---------------------------------------------------
    # Quality / change tracking
    # ---------------------------------------------------

    quality_flags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'"))

    record_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    first_seen_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("harvest_runs.run_id"), nullable=False
    )

    last_seen_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("harvest_runs.run_id"), nullable=False
    )

    last_changed_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("harvest_runs.run_id"), nullable=False
    )

    harvested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
