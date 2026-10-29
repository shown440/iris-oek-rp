import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint
from sqlalchemy import DateTime
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.entity import BaseEntity


class HarvestRun(BaseEntity):
    """
    One execution of the harvester: provenance, outcome counts, status.

    Written in its own transaction before and after the load, so a failed
    load still leaves an auditable record.
    """

    __tablename__ = "harvest_runs"

    __table_args__ = (CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status"),)

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)

    state_code: Mapped[str] = mapped_column(String(2), nullable=False)

    source_mode: Mapped[str] = mapped_column(String(10), nullable=False)

    source_endpoint: Mapped[str] = mapped_column(Text, nullable=False)

    source_layer: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    provenance: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    error: Mapped[str | None] = mapped_column(Text)
