"""
Geometry validation, repair and projection - vectorised with GeoPandas.

A whole batch of source geometries is processed as one GeoSeries per
source CRS (GeoSeries.is_valid / is_valid_reason / make_valid / force_2d /
to_crs / area). The per-record outcome is then read back by position.

Repair policy ("where defensible"):

    - Self-intersections, bad ring orientation, nested shells etc. are
      repaired with GEOS make_valid(method="structure"), which rebuilds
      rings without inventing vertices.
    - Only the polygonal part of the repaired result is kept; if nothing
      polygonal remains (collapsed sliver, line, point) the record is
      rejected rather than buffered into a shape the source never had.
    - Z values are dropped (2D target), and the record is flagged.

Projection uses GeoSeries.to_crs (pyproj, always_xy: easting/lon first).
A source that delivers lat/lon despite this lands outside the state and is
caught by check_state() - see README "Limitations".

The source geometry itself is never modified - the loader stores it
untouched in geom_source with its original SRID.
"""

from dataclasses import dataclass
from dataclasses import field

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

TARGET_SRID = 25832

POLYGONAL_TYPES = frozenset({"Polygon", "MultiPolygon"})


@dataclass(frozen=True)
class GeometryResult:
    geom: MultiPolygon | None
    issue: str | None = None
    flags: tuple[str, ...] = field(default_factory=tuple)
    repair_note: str | None = None


# =====================================================
# HELPERS
# =====================================================


def polygonal_part(geom: BaseGeometry | None) -> MultiPolygon | None:
    """
    Keep only polygon members (e.g. of a GeometryCollection produced by
    make_valid) and return them as a MultiPolygon.
    """

    if geom is None:
        return None

    parts: list[Polygon] = []

    for part in shapely.get_parts(geom):
        if isinstance(part, Polygon):
            parts.append(part)
        elif isinstance(part, MultiPolygon):
            parts.extend(part.geoms)

    parts = [p for p in parts if not p.is_empty]

    return MultiPolygon(parts) if parts else None


def repair_series(series: gpd.GeoSeries) -> tuple[gpd.GeoSeries, pd.Series]:
    """
    Vectorised repair. Returns (valid MultiPolygon or None per row,
    validity reason per repaired row / None).
    """

    invalid = ~series.is_valid
    reasons = pd.Series(None, index=series.index, dtype=object)
    repaired = series.copy()

    if invalid.any():
        reasons[invalid] = series[invalid].is_valid_reason()
        repaired[invalid] = series[invalid].make_valid(method="structure", keep_collapsed=False)

    polygonal = gpd.GeoSeries(repaired.apply(polygonal_part), index=series.index, crs=series.crs)
    still_invalid = polygonal.notna() & ~polygonal.is_valid
    polygonal[still_invalid] = None

    return polygonal, reasons


# =====================================================
# PIPELINE
# =====================================================


def prepare_geometries(geoms: gpd.GeoSeries, min_area_m2: float) -> list[GeometryResult]:
    """
    Source geometries (GeoSeries with its source CRS set) -> one result per
    row: a valid MultiPolygon in EPSG:25832, or a rejection code.
    """

    if geoms.crs is None:
        raise ValueError("source GeoSeries must carry its CRS")

    geoms = geoms.reset_index(drop=True)
    results: list[GeometryResult | None] = [None] * len(geoms)

    missing = geoms.isna()
    empty = ~missing & geoms.is_empty
    polygonal = ~missing & ~empty & geoms.geom_type.isin(POLYGONAL_TYPES)

    for mask, issue in (
        (missing, "missing_geometry"),
        (empty, "empty_geometry"),
        (~missing & ~empty & ~polygonal, "unsupported_geometry_type"),
    ):
        for i in mask[mask].index:
            results[i] = GeometryResult(None, issue)

    work = geoms[polygonal]
    flags: dict[int, list[str]] = {i: [] for i in work.index}
    notes: dict[int, list[str]] = {i: [] for i in work.index}

    def apply_repair(series: gpd.GeoSeries, stage: str) -> gpd.GeoSeries:
        repaired, reasons = repair_series(series)

        for i, reason in reasons.dropna().items():
            flags[i].append("geometry_repaired")
            notes[i].append(f"{stage}: {reason}")

        for i in repaired[repaired.isna()].index:
            results[i] = GeometryResult(None, "unrepairable_geometry", tuple(flags[i]), "; ".join(notes[i]) or None)

        return repaired[repaired.notna()]

    if not work.empty:
        has_z = work.has_z

        for i in has_z[has_z].index:
            flags[i].append("z_dropped")

        work = apply_repair(work.force_2d(), "source")

    if not work.empty and work.crs.to_epsg() != TARGET_SRID:
        work = apply_repair(work.to_crs(TARGET_SRID), "after projection")

    areas = work.area

    for i, geom in work.items():
        note = "; ".join(notes[i]) or None

        if areas[i] < min_area_m2:
            results[i] = GeometryResult(None, "degenerate_geometry", tuple(flags[i]), note)
        else:
            results[i] = GeometryResult(geom, None, tuple(dict.fromkeys(flags[i])), note)

    return results


def prepare_geometry(
    geom: BaseGeometry | None,
    source_srid: int,
    min_area_m2: float,
) -> GeometryResult:
    """
    Single-geometry convenience wrapper around prepare_geometries().
    """

    return prepare_geometries(gpd.GeoSeries([geom], crs=f"EPSG:{source_srid}"), min_area_m2)[0]


def check_state(
    municipality_key: str | None,
    geom: MultiPolygon,
    boundary: BaseGeometry,
    ags_prefix: str,
    tolerance_m: float,
) -> tuple[str | None, str | None]:
    """
    Returns (rejection_code, warning_flag).

    The municipality key is authoritative for the state; the boundary
    polygon (VG2500, generalised) is a plausibility check that catches CRS
    and axis-order errors. Without a key, the geometry must lie inside.
    """

    point = geom.point_on_surface()
    inside = boundary.contains(point)

    if municipality_key is not None:
        if not municipality_key.startswith(ags_prefix):
            return "outside_state", None

        if inside:
            return None, None

        if boundary.distance(point) <= tolerance_m:
            return None, "near_state_border"

        return "geometry_outside_state", None

    return (None, None) if inside else ("geometry_outside_state", None)
