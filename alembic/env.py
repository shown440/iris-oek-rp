from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from sqlalchemy import text

import app.database.models  # noqa: F401  (registers tables on Base.metadata)
from app.config.settings import get_settings
from app.database.base import STAGING_SCHEMA
from app.database.base import Base

# ==========================================================
# Alembic Configuration
# ==========================================================

config = context.config

# An explicitly configured URL (e.g. set by the test-suite) wins over the environment.
database_url = config.get_main_option("sqlalchemy.url") or get_settings().database_url

if not database_url:
    raise RuntimeError("IRIS_DATABASE_URL (or DATABASE_URL) is not set")

config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_name(name, type_, parent_names):
    """
    Only the staging schema is managed here. iris_core (owned by the
    iris-sample migrations), public, topology, tiger ... are ignored.
    """

    if type_ == "schema":
        return name == STAGING_SCHEMA

    return True


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_schemas=True,
        include_name=include_name,
        version_table_schema=STAGING_SCHEMA,
        compare_type=True,
        **kwargs,
    )


# ==========================================================
# Offline / Online
# ==========================================================


def run_migrations_offline() -> None:
    _configure(url=config.get_main_option("sqlalchemy.url"), literal_binds=True)

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # Checked first: CREATE SCHEMA IF NOT EXISTS still needs CREATE on the
        # database, which the least-privilege application role does not have.
        exists = connection.execute(
            text("SELECT 1 FROM pg_namespace WHERE nspname = :name"), {"name": STAGING_SCHEMA}
        ).scalar()

        if not exists:
            connection.exec_driver_sql(f"CREATE SCHEMA {STAGING_SCHEMA}")

        # End the autobegun transaction so Alembic owns (and commits) its own.
        connection.commit()

        _configure(connection=connection)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
