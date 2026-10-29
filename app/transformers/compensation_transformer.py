"""
RawFeature -> SiteRecord (accepted) or Issue (rejected / warning).

Order of checks per feature:

    1. site_id         stable source identifier (LKompVzVO Objektkennung)
    2. domain          object type must be a compensation site
    3. geometry        polygonal, valid or defensibly repaired, EPSG:25832
                       (computed for the whole batch with GeoPandas,
                       see app/transformers/geometry.py)
    4. state           municipality key prefix 07 + boundary plausibility
    5. attributes      names, status, area, eco credits, dates

Then, across the whole batch:

    6. duplicates      identical copies collapse to one; conflicting
                       copies of the same site_id are all rejected
"""

import hashlib
import json
from collections import Counter
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import field
from datetime import date
from typing import Any
from typing import Literal

import geopandas as gpd
import pandas as pd
import shapely
from shapely.geometry import MultiPolygon
from shapely.geometry.base import BaseGeometry

from app.config.mapping import AttributeMapping
from app.config.settings import Settings
from app.parsers.compensation_parser import RawFeature
from app.reference import load_municipalities
from app.reference import load_state_boundary
from app.scrapers.wfs.capabilities import FeatureSchema
from app.transformers.geometry import GeometryResult
from app.transformers.geometry import check_state
from app.transformers.geometry import prepare_geometries
from app.transformers.normalizers import clean_name
from app.transformers.normalizers import clean_text
from app.transformers.normalizers import map_vocabulary
from app.transformers.normalizers import normalize_ags
from app.transformers.normalizers import normalize_numeric_key
from app.transformers.normalizers import parse_date
from app.transformers.normalizers import parse_decimal
from app.transformers.normalizers import vocabulary_key

Severity = Literal["rejected", "warning"]


@dataclass(frozen=True)
class Issue:
    severity: Severity
    stage: str
    code: str
    message: str
    site_id: str | None = None
    feature_id: str | None = None
    raw_record: dict[str, Any] | None = None


@dataclass
class SiteRecord:
    state_code: str
    country_code: str
    site_id: str
    source_feature_id: str | None
    source_layer: str
    source_endpoint: str
    object_type: str | None
    object_type_source: str | None
    site_name: str | None
    file_reference: str | None
    municipality_key: str | None
    municipality_name: str | None
    municipality_name_source: str | None
    district_key: str | None
    district_name: str | None
    cadastral_district_key: str | None
    cadastral_district_name: str | None
    compensation_status: str | None
    compensation_status_source: str | None
    area_source_m2: float | None
    area_geom_m2: float
    eco_credits_total: float | None
    eco_credits_available: float | None
    registering_authority: str | None
    registered_on: date | None
    source_crs: str
    source_srid: int
    geom_source: BaseGeometry
    geom: MultiPolygon
    geometry_repaired: bool
    repair_note: str | None
    quality_flags: list[str]
    source_attributes: dict[str, Any]
    record_hash: str = ""


@dataclass
class TransformResult:
    records: list[SiteRecord] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)


def _jsonable(properties: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(properties, default=str, ensure_ascii=False))


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def compute_record_hash(record: SiteRecord) -> str:
    """
    Content hash of everything that comes from the source. Run ids and
    timestamps are excluded, so an unchanged feature hashes identically
    across runs (the basis of idempotent upserts).

    The server feature id (gml:id) is excluded too: servers such as
    MapServer number it by result position, so one new upstream site would
    otherwise mark every following site as "updated".
    """

    payload = {
        key: value
        for key, value in vars(record).items()
        if key not in ("geom", "geom_source", "record_hash", "source_endpoint", "source_feature_id")
    }
    payload["geom"] = shapely.to_wkt(shapely.normalize(record.geom), rounding_precision=3)
    canonical = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CompensationTransformer:
    def __init__(
        self,
        settings: Settings,
        mapping: AttributeMapping,
        schema: FeatureSchema | None,
        source_layer: str,
        source_endpoint: str,
    ):
        self.settings = settings
        self.mapping = mapping
        self.source_layer = source_layer
        self.source_endpoint = source_endpoint
        self.absent_fields = schema.missing(mapping.mapped_source_fields()) if schema else set()
        self.municipalities = load_municipalities()
        self.districts = {m.district_key: m.district_name for m in self.municipalities.values()}
        self.boundary = load_state_boundary()

    # =====================================================
    # PUBLIC
    # =====================================================

    def transform(self, features: Iterable[RawFeature]) -> TransformResult:
        result = TransformResult()
        accepted: list[SiteRecord] = []
        features = list(features)
        geometries = self._prepare_geometries(features)

        for feature, geometry in zip(features, geometries, strict=True):
            result.counts["fetched"] += 1
            record, issues = self._transform_one(feature, geometry)
            result.issues.extend(issues)

            if record is None:
                result.counts["rejected"] += 1
                continue

            accepted.append(record)

        result.records = self._deduplicate(accepted, result)

        for record in result.records:
            result.counts["accepted"] += 1

            if record.geometry_repaired:
                result.counts["accepted_geometry_repaired"] += 1

            if record.quality_flags:
                result.counts["accepted_with_warnings"] += 1

        return result

    def _prepare_geometries(self, features: list[RawFeature]) -> list[GeometryResult | None]:
        """
        Geometry stage for the whole batch with GeoPandas: one GeoSeries per
        source CRS. Features without a parseable geometry or CRS get None
        (they are rejected in _transform_one with their own code).
        """

        frame = gpd.GeoDataFrame(
            {
                "position": range(len(features)),
                "srid": pd.array([f.source_srid for f in features], dtype="Int64"),
            },
            geometry=[None if f.parse_error else f.geometry for f in features],
        )
        results: list[GeometryResult | None] = [None] * len(features)

        for srid, group in frame[frame["srid"].notna()].groupby("srid"):
            source = gpd.GeoSeries(group.geometry.values, crs=f"EPSG:{int(srid)}")
            prepared = prepare_geometries(source, self.settings.min_area_m2)

            for position, geometry in zip(group["position"], prepared, strict=True):
                results[position] = geometry

        return results

    # =====================================================
    # SINGLE FEATURE
    # =====================================================

    def _value(self, properties: dict[str, Any], target: str) -> Any:
        source = self.mapping.source_field(target)

        if source is None or source in self.absent_fields:
            return None

        return properties.get(source)

    def _transform_one(
        self, feature: RawFeature, geometry: GeometryResult | None
    ) -> tuple[SiteRecord | None, list[Issue]]:
        props = feature.properties
        raw = _jsonable(props)
        issues: list[Issue] = []
        flags: list[str] = []

        def reject(stage: str, code: str, message: str, site_id: str | None = None) -> tuple[None, list[Issue]]:
            issues.append(Issue("rejected", stage, code, message, site_id, feature.feature_id, raw))
            return None, issues

        def warn(code: str, message: str) -> None:
            flags.append(code)
            issues.append(Issue("warning", "validate", code, message, site_id, feature.feature_id))

        # -------------------------------------------------
        # 1. Identifier
        # -------------------------------------------------

        site_id = clean_text(props.get(self.mapping.id_field))

        if site_id is None and self.mapping.allow_feature_id_fallback and feature.feature_id:
            site_id = feature.feature_id
            warn("site_id_from_feature_id", "id property empty; server feature id used")

        if site_id is None:
            return reject("validate", "missing_site_id", f"'{self.mapping.id_field}' is empty")

        # -------------------------------------------------
        # 2. Domain (LKompVzVO object type)
        # -------------------------------------------------

        object_type = None
        object_type_source = None

        object_type_field = self.mapping.source_field("object_type")

        # Domain check only when the source publishes an object type; otherwise
        # the layer itself (rp_oekokonto) defines the domain.
        if object_type_field is not None and object_type_field not in self.absent_fields:
            object_type, object_type_source = map_vocabulary(
                self._value(props, "object_type"), self.mapping.object_types
            )

            if object_type_source is None:
                return reject("domain", "missing_object_type", "object type is empty", site_id)

            if vocabulary_key(object_type_source) in self.mapping.object_types_excluded:
                return reject("domain", "out_of_domain", f"'{object_type_source}' is not a compensation site", site_id)

            if object_type is None:
                return reject("domain", "unknown_object_type", f"object type '{object_type_source}'", site_id)

        # -------------------------------------------------
        # 3. Geometry
        # -------------------------------------------------

        if feature.parse_error:
            return reject("parse", "geometry_unparseable", feature.parse_error, site_id)

        if feature.source_srid is None:
            return reject("geometry", "unknown_source_crs", "no CRS advertised for this geometry", site_id)

        if geometry is None:  # defensive: every parseable feature with a CRS has a result
            return reject("geometry", "missing_geometry", "geometry stage produced no result", site_id)

        if geometry.issue:
            return reject("geometry", geometry.issue, geometry.repair_note or geometry.issue, site_id)

        for flag in geometry.flags:
            warn(flag, geometry.repair_note or flag)

        # -------------------------------------------------
        # 4. State
        # -------------------------------------------------

        municipality_key, key_issue = normalize_ags(self._value(props, "municipality_key"))

        if key_issue:
            warn(key_issue, f"municipality key {self._value(props, 'municipality_key')!r}")

        state_issue, state_flag = check_state(
            municipality_key,
            geometry.geom,
            self.boundary,
            self.settings.state_ags_prefix,
            self.settings.state_border_tolerance_m,
        )

        if state_issue == "outside_state":
            return reject(
                "domain",
                state_issue,
                f"municipality key {municipality_key} is not in state {self.settings.state_code}",
                site_id,
            )

        if state_issue:
            return reject(
                "domain", state_issue, "geometry outside the state boundary (CRS / axis-order error?)", site_id
            )

        if state_flag:
            warn(state_flag, "geometry outside the generalised boundary but within tolerance")

        # -------------------------------------------------
        # 5. Attributes
        # -------------------------------------------------

        municipality_name_source, repaired = clean_name(self._value(props, "municipality_name"))

        if repaired:
            warn("name_encoding_repaired", f"municipality name re-decoded to '{municipality_name_source}'")

        municipality_name = municipality_name_source
        district_key = None
        district_name = None

        if municipality_key:
            reference = self.municipalities.get(municipality_key)

            if reference:
                municipality_name = reference.name
                district_key, district_name = reference.district_key, reference.district_name

                if municipality_name_source and vocabulary_key(municipality_name_source) != vocabulary_key(
                    reference.name
                ):
                    warn(
                        "municipality_name_mismatch",
                        f"source '{municipality_name_source}' vs official '{reference.name}' for {municipality_key}",
                    )
            else:
                warn("municipality_key_unknown", f"{municipality_key} not in BKG VG250 reference")

                if municipality_key[:5] in self.districts:
                    district_key = municipality_key[:5]
                    district_name = self.districts[district_key]

        cadastral_name, cadastral_repaired = clean_name(self._value(props, "cadastral_district_name"))

        if cadastral_repaired:
            warn("name_encoding_repaired", f"cadastral district re-decoded to '{cadastral_name}'")

        cadastral_key, cadastral_issue = normalize_numeric_key(self._value(props, "cadastral_district_key"))

        if cadastral_issue:
            warn(cadastral_issue, f"cadastral key {self._value(props, 'cadastral_district_key')!r}")

        status, status_source = map_vocabulary(self._value(props, "status"), self.mapping.status)

        if status_source is not None and status is None:
            warn("unmapped_status", f"status '{status_source}' not in vocabulary")

        area_ha, area_issue = parse_decimal(self._value(props, "area_ha"))

        if area_issue:
            warn(area_issue, f"area {self._value(props, 'area_ha')!r}")

        area_source_m2 = None if area_ha is None else area_ha * 10_000
        area_geom_m2 = geometry.geom.area

        if area_source_m2 is not None:
            if area_source_m2 <= 0:
                warn("invalid_area", f"declared area {area_ha} ha")
                area_source_m2 = None
            elif abs(area_source_m2 - area_geom_m2) / area_geom_m2 > self.settings.area_mismatch_tolerance:
                warn(
                    "area_mismatch",
                    f"declared {area_source_m2:,.0f} m2 vs geometry {area_geom_m2:,.0f} m2",
                )

        credits: dict[str, float | None] = {}

        for target in ("eco_credits_total", "eco_credits_available"):
            value, issue = parse_decimal(self._value(props, target))

            if issue:
                warn(issue, f"{target} {self._value(props, target)!r}")

            if value is not None and value < 0:
                warn("invalid_eco_credits", f"{target} is negative ({value})")
                value = None

            credits[target] = value

        if (
            credits["eco_credits_total"] is not None
            and credits["eco_credits_available"] is not None
            and credits["eco_credits_available"] > credits["eco_credits_total"]
        ):
            warn("eco_credits_inconsistent", "available credits exceed total credits")

        registered_on, date_issue = parse_date(self._value(props, "registered_on"))

        if date_issue:
            warn(date_issue, f"registered_on {self._value(props, 'registered_on')!r}")

        record = SiteRecord(
            state_code=self.settings.state_code,
            country_code=self.settings.country_code,
            site_id=site_id,
            source_feature_id=feature.feature_id,
            source_layer=self.source_layer,
            source_endpoint=self.source_endpoint,
            object_type=object_type,
            object_type_source=object_type_source,
            site_name=clean_text(self._value(props, "site_name")),
            file_reference=clean_text(self._value(props, "file_reference")),
            municipality_key=municipality_key,
            municipality_name=municipality_name,
            municipality_name_source=municipality_name_source,
            district_key=district_key,
            district_name=district_name,
            cadastral_district_key=cadastral_key,
            cadastral_district_name=cadastral_name,
            compensation_status=status,
            compensation_status_source=status_source,
            area_source_m2=_round(area_source_m2),
            area_geom_m2=round(area_geom_m2, 2),
            eco_credits_total=_round(credits["eco_credits_total"]),
            eco_credits_available=_round(credits["eco_credits_available"]),
            registering_authority=clean_text(self._value(props, "registering_authority")),
            registered_on=registered_on,
            source_crs=f"EPSG:{feature.source_srid}",
            source_srid=feature.source_srid,
            geom_source=feature.geometry,
            geom=geometry.geom,
            geometry_repaired="geometry_repaired" in geometry.flags,
            repair_note=geometry.repair_note,
            quality_flags=sorted(set(flags)),
            source_attributes=raw,
        )
        record.record_hash = compute_record_hash(record)

        return record, issues

    # =====================================================
    # BATCH
    # =====================================================

    def _deduplicate(self, records: list[SiteRecord], result: TransformResult) -> list[SiteRecord]:
        groups: dict[str, list[SiteRecord]] = defaultdict(list)

        for record in records:
            groups[record.site_id].append(record)

        unique: list[SiteRecord] = []

        for site_id, group in groups.items():
            if len(group) == 1:
                unique.append(group[0])
                continue

            if len({r.record_hash for r in group}) == 1:
                unique.append(group[0])
                result.counts["duplicates_collapsed"] += len(group) - 1
                result.issues.append(
                    Issue(
                        "warning",
                        "dedup",
                        "duplicate_in_source",
                        f"{len(group)} identical copies delivered (non transaction-safe paging?); loaded once",
                        site_id,
                        group[0].source_feature_id,
                    )
                )
                continue

            result.counts["rejected"] += len(group)

            for record in group:
                result.issues.append(
                    Issue(
                        "rejected",
                        "dedup",
                        "conflicting_duplicate",
                        f"{len(group)} different versions of the same site_id; none loaded",
                        site_id,
                        record.source_feature_id,
                        record.source_attributes,
                    )
                )

        return unique
