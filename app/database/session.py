from functools import lru_cache

from sqlalchemy import Engine
from sqlalchemy import create_engine
from sqlalchemy import event

from app.config.settings import get_settings
from app.database.base import STAGING_SCHEMA


@lru_cache
def get_engine(database_url: str | None = None) -> Engine:
    """
    Engine factory (lazy, so unit tests and `discover` need no database).

    Every connection gets search_path = iris_staging, public so any
    unqualified SQL also resolves to the staging schema.
    """

    url = database_url or get_settings().database_url

    if not url:
        raise RuntimeError("IRIS_DATABASE_URL (or DATABASE_URL) is not set - see .env.example")

    engine = create_engine(url, pool_pre_ping=True, future=True)

    @event.listens_for(engine, "connect")
    def _set_search_path(dbapi_connection, _record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {STAGING_SCHEMA}, public")
        dbapi_connection.commit()

    return engine
