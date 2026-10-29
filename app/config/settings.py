from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices
from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    """
    Runtime configuration, read from environment variables (prefix IRIS_)
    or a local .env file. No secrets have defaults.

    The target schema is NOT configurable: every model is bound to
    iris_staging (see app/database/base.py) so a mis-set variable can
    never redirect writes to iris_core.
    """

    model_config = SettingsConfigDict(
        env_prefix="IRIS_",
        env_file=".env",
        extra="ignore",
    )

    # =====================================================
    # DATABASE
    # =====================================================

    database_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("IRIS_DATABASE_URL", "DATABASE_URL"),
    )

    # =====================================================
    # SOURCE
    # =====================================================

    source_mode: Literal["live", "fixture"] = "fixture"

    wfs_url: str = "https://geodaten-wasser.rlp.de/wfs/kompensation"

    wfs_layer: str = "rp_oekokonto"

    wfs_version: Literal["2.0.0", "1.1.0"] = "2.0.0"

    fixture_dir: Path = Path("fixtures/luwg")

    mapping_file: Path = Path("config/attribute_mapping.toml")

    # =====================================================
    # CONTROLLED REQUESTS
    # =====================================================

    page_size: int = Field(default=500, ge=1, le=10_000)

    max_features: int = Field(default=50_000, ge=1)

    max_pages: int = Field(default=1_000, ge=1)

    request_delay_s: float = Field(default=1.0, ge=0)

    http_timeout_s: float = Field(default=60, gt=0)

    http_retries: int = Field(default=3, ge=0, le=10)

    http_backoff_s: float = Field(default=2.0, ge=0)

    user_agent: str = "IRIS-OEK-RP-Harvester/1.0 (staging ETL; IRIS engineering)"

    # Hosts besides the endpoint's own domain that server-advertised
    # operation URLs / redirects may point to (comma-separated).
    wfs_allowed_hosts: str = ""

    max_response_mb: float = Field(default=200, gt=0)

    # =====================================================
    # PERMISSIONS
    # =====================================================

    # A service whose AccessConstraints are not NONE is only harvested after a
    # human has read the terms and set this to true. Never bypassed in code.
    access_constraints_acknowledged: bool = False

    # =====================================================
    # DOMAIN
    # =====================================================

    state_code: str = Field(default="RP", pattern=r"^[A-Z]{2}$")

    country_code: str = Field(default="DE", pattern=r"^[A-Z]{2}$")

    state_ags_prefix: str = Field(default="07", pattern=r"^\d{2}$")

    state_border_tolerance_m: float = Field(default=2_000, ge=0)

    min_area_m2: float = Field(default=1.0, ge=0)

    area_mismatch_tolerance: float = Field(default=0.05, ge=0)

    # =====================================================
    # LOAD / OUTPUT
    # =====================================================

    batch_size: int = Field(default=500, ge=1)

    data_dir: Path = Path("data")


@lru_cache
def get_settings() -> Settings:
    return Settings()
