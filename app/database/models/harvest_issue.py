import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger
from sqlalchemy import CheckConstraint
from sqlalchemy import DateTime
from sqlalchemy import ForeignKey
from sqlalchemy import Index
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class HarvestIssue(Base):
    """
    Error log: every rejected record (with its raw attributes) and every
    quality warning on an accepted record, per run.
    """

    __tablename__ = "harvest_issues"

    __table_args__ = (
        CheckConstraint("severity IN ('rejected', 'warning')", name="severity"),
        Index("ix_harvest_issues_run_code", "run_id", "code"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("harvest_runs.run_id", ondelete="CASCADE"),
        nullable=False,
    )

    severity: Mapped[str] = mapped_column(String(10), nullable=False)

    stage: Mapped[str] = mapped_column(String(20), nullable=False)

    code: Mapped[str] = mapped_column(String(64), nullable=False)

    site_id: Mapped[str | None] = mapped_column(String(128))

    source_feature_id: Mapped[str | None] = mapped_column(String(255))

    message: Mapped[str] = mapped_column(Text, nullable=False)

    raw_record: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
