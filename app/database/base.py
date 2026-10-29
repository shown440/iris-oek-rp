from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# The only schema this project may write to. Hard-coded on purpose:
# iris_core and production schemas are out of scope (assignment brief).
STAGING_SCHEMA = "iris_staging"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=STAGING_SCHEMA, naming_convention=NAMING_CONVENTION)
