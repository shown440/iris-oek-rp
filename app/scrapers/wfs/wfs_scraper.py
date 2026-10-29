"""
OGC WFS harvester: discovery + controlled, paginated GetFeature.

Discovery (no assumptions about the server):

    1. GetCapabilities      -> version, feature types, formats, CRS,
                               ImplementsResultPaging, CountDefault,
                               PagingIsTransactionSafe, ImplementsSorting,
                               fees / access constraints (licence)
    2. DescribeFeatureType  -> actual property names and types; the
                               attribute mapping is checked against it
    3. GetFeature hits      -> numberMatched (when advertised)
    4. Verify before use    -> no feature data is requested if the stable
                               id / geometry field is missing from the
                               published schema, or if AccessConstraints
                               are declared and not acknowledged

Paging: WFS 2.0 STARTINDEX/COUNT with SORTBY on the stable id when the
server supports sorting (paging is only deterministic with an order;
servers advertising PagingIsTransactionSafe=FALSE may still shift rows,
which the transformer's duplicate handling absorbs). Without paging
support a single capped request is made and possible truncation is
reported.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.config.mapping import AttributeMapping
from app.config.settings import Settings
from app.parsers.compensation_parser import ParsedPage
from app.parsers.compensation_parser import parse_page
from app.scrapers.base.raw_archive import RawArchive
from app.scrapers.wfs.capabilities import FeatureSchema
from app.scrapers.wfs.capabilities import FeatureTypeInfo
from app.scrapers.wfs.capabilities import WfsCapabilities
from app.scrapers.wfs.capabilities import parse_capabilities
from app.scrapers.wfs.capabilities import parse_feature_schema
from app.scrapers.wfs.capabilities import parse_hits
from app.scrapers.wfs.errors import AccessConstraintsNotAcknowledgedError
from app.scrapers.wfs.errors import SchemaMismatchError
from app.scrapers.wfs.errors import WfsServiceError
from app.scrapers.wfs.transport import WfsResponse
from app.scrapers.wfs.transport import WfsTransport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Discovery:
    capabilities: WfsCapabilities
    feature_type: FeatureTypeInfo
    schema: FeatureSchema
    output_format: str
    srs_name: str | None
    number_matched: int | None
    page_size: int
    sort_by: str | None
    get_feature_url: str
    schema_path: Path | None
    blockers: tuple[tuple[str, str], ...] = ()

    @property
    def version(self) -> str:
        return self.capabilities.version

    def summary(self, mapping: AttributeMapping) -> dict[str, object]:
        caps = self.capabilities
        mapped = mapping.mapped_source_fields()

        return {
            "service_title": caps.title,
            "provider": caps.provider,
            "wfs_version": caps.version,
            "fees_licence": caps.fees,
            "access_constraints": caps.access_constraints,
            "feature_type": self.feature_type.name,
            "feature_type_title": self.feature_type.title,
            "default_crs": self.feature_type.default_crs,
            "other_crs": list(self.feature_type.other_crs),
            "output_format": self.output_format,
            "implements_result_paging": caps.implements_paging,
            "paging_is_transaction_safe": caps.paging_transaction_safe,
            "count_default": caps.count_default,
            "implements_sorting": caps.implements_sorting,
            "sort_by": self.sort_by,
            "page_size": self.page_size,
            "number_matched": self.number_matched,
            "get_feature_url": self.get_feature_url,
            "schema_properties": self.schema.properties,
            "geometry_property": self.schema.geometry_property,
            "mapped_fields_missing_in_schema": sorted(self.schema.missing(mapped)),
            "schema_fields_not_mapped": sorted(self.schema.unmapped(mapped)),
            "harvest_blockers": [message for _, message in self.blockers],
        }


# AccessConstraints values that mean "no restriction".
OPEN_ACCESS_VALUES = frozenset({"", "none", "keine", "no constraints"})


def access_is_open(access_constraints: str | None) -> bool:
    return access_constraints is None or access_constraints.strip().rstrip(".").casefold() in OPEN_ACCESS_VALUES


class WfsHarvester:
    def __init__(
        self,
        transport: WfsTransport,
        settings: Settings,
        mapping: AttributeMapping,
        archive: RawArchive,
    ):
        self.transport = transport
        self.settings = settings
        self.mapping = mapping
        self.archive = archive

    def _get(self, name: str, params: dict[str, str], url: str | None = None) -> tuple[WfsResponse, Path]:
        response = self.transport.get(params, url=url)
        path = self.archive.save(name, response.url, response.content, response.content_type)
        return response, path

    # =====================================================
    # DISCOVERY
    # =====================================================

    def discover(self) -> Discovery:
        endpoint = self.transport.endpoint

        response, _ = self._get(
            "capabilities.xml",
            {
                "SERVICE": "WFS",
                "REQUEST": "GetCapabilities",
                "ACCEPTVERSIONS": "2.0.0,1.1.0" if self.settings.wfs_version == "2.0.0" else "1.1.0",
            },
        )
        caps = parse_capabilities(response.content)
        feature_type = caps.find_feature_type(self.settings.wfs_layer)
        version = caps.version
        type_param = "TYPENAMES" if version.startswith("2.") else "TYPENAME"

        response, schema_path = self._get(
            "describe_feature_type.xsd",
            {"SERVICE": "WFS", "VERSION": version, "REQUEST": "DescribeFeatureType", type_param: feature_type.name},
            url=caps.operation_url("DescribeFeatureType", endpoint),
        )
        schema = parse_feature_schema(response.content, feature_type.name)

        missing = schema.missing(self.mapping.mapped_source_fields())

        if missing:
            logger.warning("Mapped source fields not in published schema (loaded as NULL): %s", sorted(missing))

        page_size = self.settings.page_size

        if caps.count_default:
            page_size = min(page_size, caps.count_default)

        sort_field = self.mapping.sort_field
        sort_by = None

        if caps.implements_sorting and sort_field and sort_field in schema.properties:
            sort_by = f"{sort_field} ASC"
        elif caps.implements_paging:
            logger.warning("Paging without SORTBY - page boundaries may not be stable")

        get_feature_url = caps.operation_url("GetFeature", endpoint)
        number_matched = None

        if version.startswith("2."):
            try:
                response, _ = self._get(
                    "hits.xml",
                    {
                        "SERVICE": "WFS",
                        "VERSION": version,
                        "REQUEST": "GetFeature",
                        "TYPENAMES": feature_type.name,
                        "RESULTTYPE": "hits",
                    },
                    url=get_feature_url,
                )
                number_matched = parse_hits(response.content)
            except WfsServiceError as exc:
                logger.warning("resultType=hits not supported: %s", exc)

        return Discovery(
            capabilities=caps,
            feature_type=feature_type,
            schema=schema,
            output_format=caps.choose_output_format(feature_type),
            srs_name=feature_type.default_crs,
            number_matched=number_matched,
            page_size=page_size,
            sort_by=sort_by,
            get_feature_url=get_feature_url,
            schema_path=schema_path,
            blockers=self._blockers(caps, schema),
        )

    # =====================================================
    # VERIFY BEFORE USE (permissions + actual schema)
    # =====================================================

    def _blockers(self, caps: WfsCapabilities, schema: FeatureSchema) -> tuple[tuple[str, str], ...]:
        blockers = []

        if self.mapping.id_field not in schema.properties:
            blockers.append(
                ("schema", f"stable identifier field '{self.mapping.id_field}' is not in the published schema")
            )

        if schema.geometry_property is None:
            blockers.append(("schema", "the published schema has no geometry property"))

        if not access_is_open(caps.access_constraints) and not self.settings.access_constraints_acknowledged:
            blockers.append(
                (
                    "access",
                    f"service declares AccessConstraints '{caps.access_constraints}' - read the terms, then set "
                    "IRIS_ACCESS_CONSTRAINTS_ACKNOWLEDGED=true",
                )
            )

        return tuple(blockers)

    def verify_before_use(self, discovery: Discovery) -> None:
        """
        Raised before any feature data is requested.
        """

        for kind, message in discovery.blockers:
            if kind == "schema":
                raise SchemaMismatchError(message)

            raise AccessConstraintsNotAcknowledgedError(message)

        if discovery.capabilities.access_constraints and not access_is_open(discovery.capabilities.access_constraints):
            logger.warning("AccessConstraints acknowledged by operator: %s", discovery.capabilities.access_constraints)

    # =====================================================
    # PAGING
    # =====================================================

    def _feature_params(self, discovery: Discovery) -> dict[str, str]:
        params = {
            "SERVICE": "WFS",
            "VERSION": discovery.version,
            "REQUEST": "GetFeature",
            "OUTPUTFORMAT": discovery.output_format,
        }

        if discovery.version.startswith("2."):
            params["TYPENAMES"] = discovery.feature_type.name
        else:
            params["TYPENAME"] = discovery.feature_type.name

        if discovery.srs_name:
            params["SRSNAME"] = discovery.srs_name

        if discovery.sort_by:
            params["SORTBY"] = discovery.sort_by

        return params

    def _extension(self, discovery: Discovery) -> str:
        return "json" if "json" in discovery.output_format.lower() else "gml"

    def iter_pages(self, discovery: Discovery) -> Iterator[tuple[WfsResponse, ParsedPage]]:
        self.verify_before_use(discovery)

        max_features = self.settings.max_features
        extension = self._extension(discovery)
        base = self._feature_params(discovery)

        if not discovery.capabilities.implements_paging:
            count_key = "COUNT" if discovery.version.startswith("2.") else "MAXFEATURES"
            response, _ = self._get(
                f"page_0001.{extension}", {**base, count_key: str(max_features)}, url=discovery.get_feature_url
            )
            parsed = parse_page(response.content, discovery.srs_name, 1, discovery.schema_path)

            if parsed.number_returned >= max_features:
                logger.warning("Server has no paging; result may be truncated at %d features", max_features)

            yield response, parsed
            return

        start = 0
        page = 1

        while True:
            if page > self.settings.max_pages:
                logger.warning("Stopped at max_pages=%d", self.settings.max_pages)
                return

            remaining = max_features - start

            if remaining <= 0:
                logger.warning("Stopped at max_features=%d (more may exist)", max_features)
                return

            count = min(discovery.page_size, remaining)
            response, _ = self._get(
                f"page_{page:04d}.{extension}",
                {**base, "STARTINDEX": str(start), "COUNT": str(count)},
                url=discovery.get_feature_url,
            )
            parsed = parse_page(response.content, discovery.srs_name, page, discovery.schema_path)

            yield response, parsed

            returned = parsed.number_returned
            start += returned
            page += 1

            if returned == 0 or returned < count:
                return

            if discovery.number_matched is not None and start >= discovery.number_matched:
                return
