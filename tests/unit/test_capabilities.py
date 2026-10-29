from pathlib import Path

import pytest

from app.scrapers.wfs.capabilities import parse_capabilities
from app.scrapers.wfs.capabilities import parse_feature_schema
from app.scrapers.wfs.capabilities import parse_hits
from app.scrapers.wfs.capabilities import raise_for_exception_report
from app.scrapers.wfs.errors import LayerNotAdvertisedError
from app.scrapers.wfs.errors import WfsServiceError
from tests.conftest import FIXTURE_DIR

REAL_RLP_CAPABILITIES = Path(__file__).resolve().parents[1] / "data" / "rlp_umlegung_wfs200_capabilities.xml"


@pytest.fixture
def caps():
    return parse_capabilities((FIXTURE_DIR / "capabilities.xml").read_bytes())


def test_fixture_capabilities(caps):
    assert caps.version == "2.0.0"
    assert caps.implements_paging is True
    assert caps.paging_transaction_safe is False
    assert caps.count_default == 10
    assert caps.implements_sorting is True
    assert "dl-de/by-2-0" in caps.fees or "Datenlizenz" in caps.fees

    feature_type = caps.find_feature_type("rp_oekokonto")
    assert feature_type.name == "luwg:rp_oekokonto"
    assert feature_type.default_crs == "urn:ogc:def:crs:EPSG::25832"
    assert caps.choose_output_format(feature_type) == "application/json; subtype=geojson"


def test_layer_must_be_advertised_exactly(caps):
    with pytest.raises(LayerNotAdvertisedError) as excinfo:
        caps.find_feature_type("rp_oekokonto_alt")

    assert excinfo.value.available == ["luwg:rp_oekokonto"]


def test_real_rlp_mapserver_capabilities_parse():
    """
    A genuine WFS 2.0 response from an RLP state service (LVermGeo).
    """

    caps = parse_capabilities(REAL_RLP_CAPABILITIES.read_bytes())

    assert caps.provider.startswith("Landesamt für Vermessung")
    assert caps.implements_paging is True
    assert caps.paging_transaction_safe is False
    assert caps.operation_urls["GetFeature"].startswith("https://geo5.service24.rlp.de/")

    feature_type = caps.find_feature_type("umlegung_rlp")
    assert feature_type.name == "vermkv:umlegung_rlp"
    assert feature_type.default_crs == "urn:ogc:def:crs:EPSG::25832"
    assert "json" in caps.choose_output_format(feature_type)


def test_feature_schema(mapping):
    schema = parse_feature_schema((FIXTURE_DIR / "describe_feature_type.xsd").read_bytes(), "luwg:rp_oekokonto")

    assert schema.geometry_property == "geom"
    assert schema.properties["objekt_id"] == "string"
    assert schema.missing(mapping.mapped_source_fields()) == set()
    assert schema.unmapped(mapping.mapped_source_fields()) == {"flurstuecke", "massnahme", "zielzustand"}


def test_exception_report_is_raised():
    body = (
        b'<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">'
        b'<ows:Exception exceptionCode="InvalidParameterValue" locator="typeNames">'
        b"<ows:ExceptionText>Unknown type</ows:ExceptionText></ows:Exception></ows:ExceptionReport>"
    )

    with pytest.raises(WfsServiceError) as excinfo:
        raise_for_exception_report(body)

    assert excinfo.value.code == "InvalidParameterValue"


def test_hits():
    assert parse_hits(b'<wfs:FeatureCollection xmlns:wfs="x" numberMatched="55" numberReturned="0"/>') == 55
    assert parse_hits(b'<wfs:FeatureCollection xmlns:wfs="x" numberMatched="unknown"/>') is None


def test_xml_entities_are_not_expanded():
    xxe = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]><r numberMatched="1">&x;</r>'
    assert parse_hits(xxe) == 1
