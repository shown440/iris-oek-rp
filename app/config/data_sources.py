from dataclasses import dataclass


@dataclass(frozen=True)
class DataSource:
    """
    Configuration for an external data source.

    Mirrors app/config/data_sources.py in iris-sample so the source can be
    registered there unchanged.
    """

    name: str
    country_code: str
    urls: tuple[str, ...]
    enabled: bool = True


# ==========================================================
# Data Sources
# ==========================================================

DATA_SOURCES = {
    # ------------------------------------------------------
    # LUWG / Rhineland-Palatinate compensation register
    # ------------------------------------------------------
    "luwg_kompensation": DataSource(
        name="LUWG Rheinland-Pfalz Kompensationsverzeichnis (WFS rp_oekokonto)",
        country_code="DE",
        urls=("https://geodaten-wasser.rlp.de/wfs/kompensation",),
    ),
}
