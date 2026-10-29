"""
GetFeature response -> RawFeature list.

GeoJSON is parsed natively. GML (WFS servers without a JSON output
format) is read through GDAL/pyogrio, with remote schema downloads
disabled and the harvested DescribeFeatureType passed as a sanitised XSD
(no remote includes) so field types (e.g. zero-padded keys) follow the
published schema.

Geometry errors are captured per feature, so one malformed record never
discards a whole page.
"""

import json
import math
import tempfile
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pyogrio
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from app.scrapers.wfs.capabilities import parse_xml
from app.scrapers.wfs.capabilities import raise_for_exception_report
from app.scrapers.wfs.capabilities import sanitize_schema

pyogrio.set_gdal_config_options({"GML_DOWNLOAD_WFS_SCHEMA": "NO"})


@dataclass(frozen=True)
class RawFeature:
    feature_id: str | None
    properties: dict[str, Any]
    geometry: BaseGeometry | None
    source_srid: int | None
    page_index: int
    parse_error: str | None = None


@dataclass(frozen=True)
class ParsedPage:
    features: list[RawFeature]
    number_matched: int | None

    @property
    def number_returned(self) -> int:
        return len(self.features)


def srid_from_crs(crs: str | None) -> int | None:
    """
    "urn:ogc:def:crs:EPSG::25832", "EPSG:25832",
    "http://www.opengis.net/def/crs/EPSG/0/25832" -> 25832
    """

    if not crs:
        return None

    try:
        return CRS.from_user_input(crs).to_epsg()
    except CRSError:
        return None


def parse_page(
    content: bytes,
    default_crs: str | None,
    page_index: int,
    schema_path: Path | None = None,
) -> ParsedPage:
    if content.lstrip()[:1] == b"{":
        return _parse_geojson(content, default_crs, page_index)

    raise_for_exception_report(content)

    return _parse_gml(content, default_crs, page_index, schema_path)


# =====================================================
# GEOJSON
# =====================================================


def _parse_geojson(content: bytes, default_crs: str | None, page_index: int) -> ParsedPage:
    data = json.loads(content)

    if data.get("type") != "FeatureCollection":
        raise ValueError(f"Expected a GeoJSON FeatureCollection, got {data.get('type')!r}")

    crs_name = ((data.get("crs") or {}).get("properties") or {}).get("name") or default_crs
    srid = srid_from_crs(crs_name)
    features = []

    for item in data.get("features") or []:
        geometry = None
        error = None

        if item.get("geometry") is not None:
            try:
                geometry = shape(item["geometry"])
            except Exception as exc:  # malformed coordinates: isolate per feature
                error = f"{type(exc).__name__}: {exc}"

        feature_id = item.get("id")

        features.append(
            RawFeature(
                feature_id=None if feature_id is None else str(feature_id),
                properties=dict(item.get("properties") or {}),
                geometry=geometry,
                source_srid=srid,
                page_index=page_index,
                parse_error=error,
            )
        )

    matched = data.get("numberMatched", data.get("totalFeatures"))

    return ParsedPage(features, matched if isinstance(matched, int) else None)


# =====================================================
# GML
# =====================================================


def _python_value(value: Any) -> Any:
    if value is None:
        return None

    if isinstance(value, float) and math.isnan(value):
        return None

    if isinstance(value, np.generic):
        return _python_value(value.item())

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if hasattr(value, "isoformat"):  # pandas.Timestamp
        return value.isoformat()

    return value


def _parse_gml(
    content: bytes,
    default_crs: str | None,
    page_index: int,
    schema_path: Path | None,
) -> ParsedPage:
    root = parse_xml(content)
    matched = root.get("numberMatched") or root.get("numberOfFeatures")

    with tempfile.TemporaryDirectory(prefix="iris-xsd-") as workdir:
        open_options = {}

        if schema_path:
            # Never let GDAL see the raw server schema (remote includes -> SSRF).
            safe_schema = Path(workdir) / "schema.xsd"
            safe_schema.write_bytes(sanitize_schema(schema_path.read_bytes()))
            open_options["XSD"] = str(safe_schema)

        try:
            gdf = pyogrio.read_dataframe(BytesIO(content), **open_options)
        except pyogrio.errors.DataSourceError:
            # Empty FeatureCollection: GDAL finds no layer.
            if not root.xpath("//*[local-name()='member' or local-name()='featureMember']"):
                return ParsedPage([], int(matched) if matched and matched.isdigit() else None)
            raise

    srid = gdf.crs.to_epsg() if gdf.crs is not None else srid_from_crs(default_crs)
    features = []

    for row in gdf.to_dict("records"):
        geometry = row.pop("geometry", None)
        feature_id = row.pop("gml_id", None)

        features.append(
            RawFeature(
                feature_id=None if feature_id is None else str(feature_id),
                properties={k: _python_value(v) for k, v in row.items()},
                geometry=geometry,
                source_srid=srid,
                page_index=page_index,
            )
        )

    return ParsedPage(features, int(matched) if matched and matched.isdigit() else None)
