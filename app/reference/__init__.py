"""
Static reference data (BKG VG2500 / VG250, dl-de/by-2-0).

Built by scripts/build_reference_data.py; never downloaded at runtime.
"""

import csv
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import shapely
from shapely.geometry.base import BaseGeometry

REFERENCE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Municipality:
    ags: str
    name: str
    type: str
    district_key: str
    district_name: str


@lru_cache
def load_municipalities() -> dict[str, Municipality]:
    with (REFERENCE_DIR / "rp_municipalities.csv").open(encoding="utf-8", newline="") as handle:
        return {row["ags"]: Municipality(**row) for row in csv.DictReader(handle)}


@lru_cache
def load_state_boundary() -> BaseGeometry:
    """
    Rhineland-Palatinate boundary in EPSG:25832 (prepared for fast lookups).
    """

    data = json.loads((REFERENCE_DIR / "rp_boundary_vg2500.geojson").read_text(encoding="utf-8"))
    geom = shapely.from_geojson(json.dumps(data["features"][0]["geometry"]))
    shapely.prepare(geom)
    return geom
