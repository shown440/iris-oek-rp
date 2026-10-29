import http.server
import json
import threading

import pytest

from app.parsers.compensation_parser import parse_page
from app.parsers.compensation_parser import srid_from_crs
from app.scrapers.wfs.capabilities import sanitize_schema
from tests.conftest import FIXTURE_DIR


def test_srid_from_crs_variants():
    for crs in (
        "urn:ogc:def:crs:EPSG::25832",
        "EPSG:25832",
        "http://www.opengis.net/def/crs/EPSG/0/25832",
    ):
        assert srid_from_crs(crs) == 25832

    assert srid_from_crs("not-a-crs") is None
    assert srid_from_crs(None) is None


def test_geojson_page_crs_from_collection_member():
    body = json.dumps(
        {
            "type": "FeatureCollection",
            "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::31467"}},
            "numberMatched": 1,
            "features": [
                {
                    "type": "Feature",
                    "id": "f.1",
                    "properties": {"objekt_id": "A"},
                    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                }
            ],
        }
    ).encode()

    page = parse_page(body, "urn:ogc:def:crs:EPSG::25832", 1)

    assert page.number_matched == 1
    assert page.features[0].source_srid == 31467
    assert page.features[0].feature_id == "f.1"


def test_malformed_geometry_is_isolated_per_feature():
    body = json.dumps(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"objekt_id": "bad"},
                    "geometry": {"type": "Polygon", "coordinates": [[[1, 2]]]},
                },
                {
                    "type": "Feature",
                    "properties": {"objekt_id": "ok"},
                    "geometry": {"type": "Point", "coordinates": [1, 2]},
                },
            ],
        }
    ).encode()

    page = parse_page(body, "EPSG:25832", 1)

    assert page.features[0].parse_error is not None
    assert page.features[0].geometry is None
    assert page.features[1].parse_error is None


def test_gml_page_uses_published_schema_types():
    """
    GML path (servers without GeoJSON). With the DescribeFeatureType XSD,
    GDAL keeps zero-padded keys as strings.
    """

    page = parse_page(
        (FIXTURE_DIR / "sample_page.gml").read_bytes(),
        "urn:ogc:def:crs:EPSG::25832",
        1,
        schema_path=FIXTURE_DIR / "describe_feature_type.xsd",
    )

    assert page.number_matched == 2
    assert len(page.features) == 2

    first = page.features[0]
    assert first.feature_id == "rp_oekokonto.1001"
    assert first.source_srid == 25832
    assert first.properties["gemeinde_schluessel"] == "07315000"
    assert first.geometry.geom_type in ("Polygon", "MultiPolygon")
    assert len(page.features[1].geometry.geoms[0].interiors) == 1


@pytest.fixture
def listener():
    """
    Local HTTP server that records every request it receives.
    """

    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


def test_server_schema_cannot_make_gdal_fetch_urls(listener, tmp_path):
    """
    Regression (SSRF): a hostile DescribeFeatureType with remote
    include/import locations, plus a GML pointing at a remote schema, must
    not cause a single outbound request - and parsing must still work.
    """

    base, hits = listener
    xsd = (FIXTURE_DIR / "describe_feature_type.xsd").read_text(encoding="utf-8")
    xsd = xsd.replace(
        '<element name="rp_oekokonto"',
        f'<include schemaLocation="{base}/include.xsd"/>\n'
        f'  <import namespace="urn:evil" schemaLocation="{base}/import.xsd"/>\n'
        '  <element name="rp_oekokonto"',
        1,
    )
    gml = (FIXTURE_DIR / "sample_page.gml").read_text(encoding="utf-8")
    gml = gml.replace(
        'numberMatched="2"',
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'xsi:schemaLocation="https://geodaten-wasser.rlp.de/luwg {base}/gml-schema.xsd" numberMatched="2"',
        1,
    )
    schema_path = tmp_path / "hostile.xsd"
    schema_path.write_text(xsd, encoding="utf-8")

    page = parse_page(gml.encode(), "urn:ogc:def:crs:EPSG::25832", 1, schema_path=schema_path)

    assert hits == []
    assert len(page.features) == 2
    assert page.features[0].properties["gemeinde_schluessel"] == "07315000"


def test_sanitize_schema_keeps_namespace_imports():
    xsd = (
        b'<schema xmlns="http://www.w3.org/2001/XMLSchema">'
        b'<import namespace="http://www.opengis.net/gml/3.2" schemaLocation="http://x/gml.xsd"/>'
        b'<include schemaLocation="http://x/a.xsd"/><redefine schemaLocation="http://x/b.xsd"/></schema>'
    )
    cleaned = sanitize_schema(xsd)

    assert b"schemaLocation" not in cleaned
    assert b"include" not in cleaned and b"redefine" not in cleaned
    assert b'namespace="http://www.opengis.net/gml/3.2"' in cleaned
