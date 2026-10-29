"""
WFS GetCapabilities / DescribeFeatureType parsing.

XPath uses local-name() so the same code reads WFS 2.0.0 (OWS 1.1) and
WFS 1.1.0 (OWS 1.0) documents. The XML parser has entity resolution and
network access disabled (XXE-safe).
"""

from dataclasses import dataclass

from lxml import etree

from app.scrapers.wfs.errors import LayerNotAdvertisedError
from app.scrapers.wfs.errors import WfsServiceError

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, remove_comments=True)


def parse_xml(content: bytes) -> etree._Element:
    return etree.fromstring(content, parser=_PARSER)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node: etree._Element, path: str) -> str | None:
    values = node.xpath(path)

    if not values:
        return None

    value = values[0]
    text = value if isinstance(value, str) else value.text

    return text.strip() if text and text.strip() else None


def _texts(node: etree._Element, path: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in (v if isinstance(v, str) else v.text for v in node.xpath(path)) if t and t.strip())


def raise_for_exception_report(content: bytes) -> None:
    """
    WFS servers often report errors as HTTP 200 + ows:ExceptionReport.
    """

    if content.lstrip()[:1] != b"<":
        return

    try:
        root = parse_xml(content)
    except etree.XMLSyntaxError:
        return

    if _local(root.tag) not in ("ExceptionReport", "ServiceExceptionReport"):
        return

    exception = root.xpath("//*[local-name()='Exception' or local-name()='ServiceException']")
    code = None

    if exception:
        code = exception[0].get("exceptionCode") or exception[0].get("code")

    message = " ".join(_texts(root, "//*[local-name()='ExceptionText']")) or " ".join(
        t.strip() for t in root.itertext() if t.strip()
    )

    raise WfsServiceError(code, message)


# =====================================================
# CAPABILITIES
# =====================================================


@dataclass(frozen=True)
class FeatureTypeInfo:
    name: str
    title: str | None
    default_crs: str | None
    other_crs: tuple[str, ...]
    output_formats: tuple[str, ...]
    wgs84_bbox: tuple[float, float, float, float] | None


@dataclass(frozen=True)
class WfsCapabilities:
    version: str
    title: str | None
    provider: str | None
    fees: str | None
    access_constraints: str | None
    operation_urls: dict[str, str]
    get_feature_formats: tuple[str, ...]
    constraints: dict[str, str]
    implements_sorting: bool
    feature_types: tuple[FeatureTypeInfo, ...]

    # -------------------------------------------------
    # Paging (WFS 2.0 ResultPaging conformance class)
    # -------------------------------------------------

    @property
    def implements_paging(self) -> bool:
        return self.version.startswith("2.") and self.constraints.get("ImplementsResultPaging", "").upper() == "TRUE"

    @property
    def paging_transaction_safe(self) -> bool | None:
        value = self.constraints.get("PagingIsTransactionSafe")
        return None if value is None else value.upper() == "TRUE"

    @property
    def count_default(self) -> int | None:
        value = self.constraints.get("CountDefault")
        return int(value) if value and value.isdigit() else None

    def operation_url(self, operation: str, fallback: str) -> str:
        return self.operation_urls.get(operation) or fallback

    def find_feature_type(self, layer: str) -> FeatureTypeInfo:
        """
        Exact name, or unique match on the local part ("luwg:rp_oekokonto"
        for "rp_oekokonto"). Similar-looking names are never substituted.
        """

        for feature_type in self.feature_types:
            if feature_type.name == layer:
                return feature_type

        matches = [ft for ft in self.feature_types if ft.name.rsplit(":", 1)[-1] == layer.rsplit(":", 1)[-1]]

        if len(matches) == 1:
            return matches[0]

        raise LayerNotAdvertisedError(layer, [ft.name for ft in self.feature_types])

    def choose_output_format(self, feature_type: FeatureTypeInfo) -> str:
        """
        Prefer GeoJSON (no GDAL needed), then GML 3.2, then any GML.
        """

        formats = feature_type.output_formats or self.get_feature_formats

        for predicate in (
            lambda f: "json" in f.lower(),
            lambda f: "3.2" in f and "gml" in f.lower(),
            lambda f: "gml" in f.lower(),
        ):
            for output_format in formats:
                if predicate(output_format):
                    return output_format

        if formats:
            return formats[0]

        return "application/gml+xml; version=3.2"


def parse_capabilities(content: bytes) -> WfsCapabilities:
    raise_for_exception_report(content)
    root = parse_xml(content)

    if _local(root.tag) != "WFS_Capabilities":
        raise ValueError(f"Not a WFS capabilities document (root element {_local(root.tag)})")

    operation_urls: dict[str, str] = {}

    for operation in root.xpath("//*[local-name()='OperationsMetadata']/*[local-name()='Operation']"):
        href = operation.xpath(".//*[local-name()='Get']/@*[local-name()='href']")

        if href:
            operation_urls[operation.get("name")] = href[0]

    constraints: dict[str, str] = {}

    for constraint in root.xpath(
        "//*[local-name()='OperationsMetadata']/*[local-name()='Constraint']"
        " | //*[local-name()='Operation'][@name='GetFeature']/*[local-name()='Constraint']"
    ):
        value = _text(constraint, "./*[local-name()='DefaultValue']")

        if value is not None:
            constraints[constraint.get("name")] = value

    sorting = _text(
        root,
        "//*[local-name()='Filter_Capabilities']//*[local-name()='Constraint']"
        "[@name='ImplementsSorting']/*[local-name()='DefaultValue']",
    )

    feature_types = []

    for node in root.xpath("//*[local-name()='FeatureTypeList']/*[local-name()='FeatureType']"):
        lower = _texts(node, "./*[local-name()='WGS84BoundingBox']/*[local-name()='LowerCorner']")
        upper = _texts(node, "./*[local-name()='WGS84BoundingBox']/*[local-name()='UpperCorner']")
        bbox = None

        if lower and upper:
            bbox = tuple(float(v) for v in (*lower[0].split(), *upper[0].split()))

        feature_types.append(
            FeatureTypeInfo(
                name=_text(node, "./*[local-name()='Name']") or "",
                title=_text(node, "./*[local-name()='Title']"),
                default_crs=_text(
                    node,
                    "./*[local-name()='DefaultCRS' or local-name()='DefaultSRS' or local-name()='SRS']",
                ),
                other_crs=_texts(node, "./*[local-name()='OtherCRS' or local-name()='OtherSRS']"),
                output_formats=_texts(node, "./*[local-name()='OutputFormats']/*[local-name()='Format']"),
                wgs84_bbox=bbox,
            )
        )

    return WfsCapabilities(
        version=root.get("version", ""),
        title=_text(root, "//*[local-name()='ServiceIdentification']/*[local-name()='Title']"),
        provider=_text(root, "//*[local-name()='ServiceProvider']/*[local-name()='ProviderName']"),
        fees=_text(root, "//*[local-name()='ServiceIdentification']/*[local-name()='Fees']"),
        access_constraints=_text(root, "//*[local-name()='ServiceIdentification']/*[local-name()='AccessConstraints']"),
        operation_urls=operation_urls,
        get_feature_formats=_texts(
            root,
            "//*[local-name()='Operation'][@name='GetFeature']"
            "/*[local-name()='Parameter'][@name='outputFormat']//*[local-name()='Value']",
        ),
        constraints=constraints,
        implements_sorting=(sorting or "").upper() == "TRUE",
        feature_types=tuple(feature_types),
    )


# =====================================================
# DESCRIBE FEATURE TYPE
# =====================================================


@dataclass(frozen=True)
class FeatureSchema:
    properties: dict[str, str]
    geometry_property: str | None

    def missing(self, fields: set[str]) -> set[str]:
        return {f for f in fields if f not in self.properties}

    def unmapped(self, fields: set[str]) -> set[str]:
        return {p for p in self.properties if p not in fields and p != self.geometry_property}


def parse_feature_schema(content: bytes, layer: str) -> FeatureSchema:
    raise_for_exception_report(content)
    root = parse_xml(content)
    local_name = layer.rsplit(":", 1)[-1]

    type_name = None

    for element in root.xpath("/*/*[local-name()='element']"):
        if element.get("name") == local_name:
            type_name = (element.get("type") or "").rsplit(":", 1)[-1]

    complex_types = root.xpath("/*/*[local-name()='complexType']")
    selected = [ct for ct in complex_types if ct.get("name") in (type_name, f"{local_name}Type")]

    if not selected and len(complex_types) == 1:
        selected = complex_types

    if not selected:
        raise ValueError(f"No schema for feature type '{layer}' in DescribeFeatureType response")

    properties: dict[str, str] = {}
    geometry_property = None

    for element in selected[0].xpath(".//*[local-name()='element']"):
        name = element.get("name")

        if not name:
            continue

        xsd_type = element.get("type") or _text(element, ".//*[local-name()='restriction']/@base") or "?"
        properties[name] = xsd_type

        if xsd_type.startswith("gml:") and xsd_type.endswith("PropertyType"):
            geometry_property = name

    return FeatureSchema(properties=properties, geometry_property=geometry_property)


def sanitize_schema(content: bytes) -> bytes:
    """
    Make a server-supplied XSD safe to hand to GDAL.

    GDAL follows <xs:include>/<xs:redefine> and <xs:import schemaLocation>
    over HTTP, so a malicious or compromised WFS could make the harvester
    request arbitrary (also internal) URLs - verified with a local listener,
    see tests/unit/test_parser.py. Includes/redefines are removed and
    import locations stripped; the namespace imports themselves (e.g. GML)
    stay, which is all GDAL needs to type the feature properties.
    """

    root = parse_xml(content)

    for node in root.xpath("//*[local-name()='include' or local-name()='redefine']"):
        node.getparent().remove(node)

    for node in root.xpath("//*[local-name()='import'][@schemaLocation]"):
        del node.attrib["schemaLocation"]

    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def parse_hits(content: bytes) -> int | None:
    """
    numberMatched (WFS 2.0) / numberOfFeatures (WFS 1.1) from resultType=hits.
    """

    raise_for_exception_report(content)
    root = parse_xml(content)
    value = root.get("numberMatched") or root.get("numberOfFeatures")

    return int(value) if value and value.isdigit() else None
