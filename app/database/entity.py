from datetime import datetime

from sqlalchemy import DateTime
from sqlalchemy import func
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column

from app.database.base import Base


class BaseEntity(Base):
    """
    Base class for all application entities.

    Provides common audit fields shared by all models (same contract as
    iris-sample's BaseEntity).
    """

    __abstract__ = True

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
