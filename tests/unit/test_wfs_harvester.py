import pytest
import responses

from app.scrapers.base.http_client import RateLimitedGetter
from app.scrapers.base.http_client import UrlPolicy
from app.scrapers.base.http_client import build_session
from app.scrapers.base.raw_archive import RawArchive
from app.scrapers.wfs.errors import AccessConstraintsNotAcknowledgedError
from app.scrapers.wfs.errors import AccessDeniedError
from app.scrapers.wfs.errors import LayerNotAdvertisedError
from app.scrapers.wfs.errors import OutboundUrlBlockedError
from app.scrapers.wfs.errors import ResponseTooLargeError
from app.scrapers.wfs.errors import SchemaMismatchError
from app.scrapers.wfs.transport import FixtureTransport
from app.scrapers.wfs.transport import HttpTransport
from app.scrapers.wfs.wfs_scraper import WfsHarvester
from tests.conftest import FIXTURE_DIR
from tests.conftest import harvest_features
from tests.conftest import make_settings


def test_paging_is_discovered_capped_and_sorted(settings, mapping):
    transport, discovery, pages = harvest_features(settings, mapping)

    # Configured 500, server CountDefault 10 -> 10.
    assert discovery.page_size == 10
    assert discovery.sort_by == "objekt_id ASC"
    assert discovery.number_matched == 55

    feature_requests = [r for r in transport.requests if r["REQUEST"] == "GetFeature" and "STARTINDEX" in r]
    assert [r["STARTINDEX"] for r in feature_requests] == ["0", "10", "20", "30", "40", "50"]
    assert all(r["COUNT"] == "10" and r["SORTBY"] == "objekt_id ASC" for r in feature_requests)
    assert all(r["SRSNAME"] == "urn:ogc:def:crs:EPSG::25832" for r in feature_requests)
    assert sum(p.number_returned for _, p in pages) == 55


def test_max_features_limits_requests(tmp_path, mapping):
    settings = make_settings(tmp_path, max_features=15)
    _, _, pages = harvest_features(settings, mapping)
    assert [p.number_returned for _, p in pages] == [10, 5]


def test_raw_responses_are_archived_with_hashes(settings, mapping):
    archive = RawArchive(settings.data_dir / "raw")
    harvester = WfsHarvester(FixtureTransport(FIXTURE_DIR), settings, mapping, archive)
    list(harvester.iter_pages(harvester.discover()))

    files = [entry["file"] for entry in archive.entries]
    assert files[:3] == ["capabilities.xml", "describe_feature_type.xsd", "hits.xml"]
    assert files[-1] == "page_0006.json"
    assert all(len(entry["sha256"]) == 64 for entry in archive.entries)


def test_unknown_layer_aborts_before_any_feature_request(tmp_path, mapping):
    settings = make_settings(tmp_path, wfs_layer="rp_kompensation")
    transport = FixtureTransport(FIXTURE_DIR)

    with pytest.raises(LayerNotAdvertisedError):
        harvest_features(settings, mapping, transport)

    assert [r["REQUEST"] for r in transport.requests] == ["GetCapabilities"]


# =====================================================
# HTTP (mocked)
# =====================================================

ENDPOINT = "https://wfs.example.test/kompensation"


def _http_transport(settings, max_bytes=10_000_000):
    getter = RateLimitedGetter(build_session(settings), 5, 0, UrlPolicy(ENDPOINT), max_bytes)
    return HttpTransport(ENDPOINT, getter)


def _serve_fixture(caps_text, base_url=ENDPOINT):
    schema = (FIXTURE_DIR / "describe_feature_type.xsd").read_text(encoding="utf-8")
    page = (FIXTURE_DIR / "features.json").read_text(encoding="utf-8")

    def callback(request):
        if request.params.get("RESULTTYPE") == "hits":
            return 200, {}, '<wfs:FeatureCollection xmlns:wfs="x" numberMatched="55" numberReturned="0"/>'
        body = {"GetCapabilities": caps_text, "DescribeFeatureType": schema, "GetFeature": page}
        return 200, {}, body[request.params["REQUEST"]]

    responses.add_callback(responses.GET, base_url, callback=callback)


def _caps(**replacements):
    text = (FIXTURE_DIR / "capabilities.xml").read_text(encoding="utf-8")
    text = text.replace("fixture://luwg/wfs/kompensation", replacements.pop("operation_url", ENDPOINT))

    for old, new in replacements.items():
        text = text.replace(old, new)

    return text


@responses.activate
def test_http_403_is_not_bypassed(settings, mapping):
    responses.get(ENDPOINT, status=403)

    with pytest.raises(AccessDeniedError):
        harvest_features(settings, mapping, _http_transport(settings))

    assert len(responses.calls) == 1


@responses.activate
def test_wfs_110_without_paging_makes_one_capped_request(settings, mapping):
    caps_110 = (
        (FIXTURE_DIR / "capabilities.xml")
        .read_text(encoding="utf-8")
        .replace('version="2.0.0"', 'version="1.1.0"', 1)
        .replace("fixture://luwg/wfs/kompensation", ENDPOINT)
    )
    schema = (FIXTURE_DIR / "describe_feature_type.xsd").read_text(encoding="utf-8")
    page = (FIXTURE_DIR / "features.json").read_text(encoding="utf-8")

    def callback(request):
        body = {"GetCapabilities": caps_110, "DescribeFeatureType": schema, "GetFeature": page}
        return 200, {}, body[request.params["REQUEST"]]

    responses.add_callback(responses.GET, ENDPOINT, callback=callback)

    _, discovery, pages = harvest_features(settings, mapping, _http_transport(settings))

    feature_calls = [c.request.params for c in responses.calls if c.request.params["REQUEST"] == "GetFeature"]
    assert discovery.version == "1.1.0"
    assert len(feature_calls) == 1
    assert feature_calls[0]["MAXFEATURES"] == str(settings.max_features)
    assert feature_calls[0]["TYPENAME"] == "luwg:rp_oekokonto"
    assert "STARTINDEX" not in feature_calls[0]
    assert pages[0][1].number_returned == 55


# =====================================================
# OUTBOUND GUARD (SSRF / size)
# =====================================================


@pytest.mark.parametrize(
    ("url", "allowed"),
    [
        ("https://www.geoportal.rlp.de/registry/wfs/588", True),  # the endpoint itself
        ("https://geo5.service24.rlp.de/wfs/umlegung_rp.fcgi", True),  # same domain (real RLP case)
        ("https://evil.example.com/wfs", False),  # foreign host
        ("http://geo5.service24.rlp.de/wfs", False),  # HTTPS downgrade
        ("file:///etc/passwd", False),
        ("https://partner.example.org/wfs", True),  # explicitly allow-listed
    ],
)
def test_url_policy(url, allowed):
    policy = UrlPolicy("https://www.geoportal.rlp.de/registry/wfs/588", ["partner.example.org"])

    if allowed:
        policy.check(url)
    else:
        with pytest.raises(OutboundUrlBlockedError):
            policy.check(url)


def test_url_policy_blocks_internal_addresses_on_allowed_domain(monkeypatch):
    monkeypatch.setattr(
        "app.scrapers.base.http_client.socket.getaddrinfo",
        lambda host, port: [(None, None, None, None, ("169.254.169.254", 0))],
    )

    with pytest.raises(OutboundUrlBlockedError, match="internal address"):
        UrlPolicy("https://www.geoportal.rlp.de/wfs").check("https://metadata.rlp.de/latest")


@responses.activate
def test_advertised_operation_url_on_foreign_host_is_never_called(settings, mapping):
    _serve_fixture(_caps(operation_url="https://evil.example.com/steal"))

    with pytest.raises(OutboundUrlBlockedError):
        harvest_features(settings, mapping, _http_transport(settings))

    assert all("evil.example.com" not in c.request.url for c in responses.calls)


@responses.activate
def test_redirect_to_foreign_host_is_blocked(settings, mapping):
    responses.get(ENDPOINT, status=302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

    with pytest.raises(OutboundUrlBlockedError):
        harvest_features(settings, mapping, _http_transport(settings))

    assert len(responses.calls) == 1


@responses.activate
def test_oversized_response_is_rejected(settings, mapping):
    responses.get(ENDPOINT, body=b"<x>" + b"a" * 5_000 + b"</x>")

    with pytest.raises(ResponseTooLargeError):
        harvest_features(settings, mapping, _http_transport(settings, max_bytes=1_000))


# =====================================================
# VERIFY BEFORE USE (permissions + schema)
# =====================================================


@responses.activate
def test_access_constraints_block_feature_requests_until_acknowledged(tmp_path, mapping):
    caps = _caps(
        **{
            "<ows:AccessConstraints>NONE</ows:AccessConstraints>": "<ows:AccessConstraints>"
            "Nur für den behördeninternen Gebrauch</ows:AccessConstraints>"
        }
    )
    _serve_fixture(caps)

    settings = make_settings(tmp_path)

    with pytest.raises(AccessConstraintsNotAcknowledgedError):
        harvest_features(settings, mapping, _http_transport(settings))

    assert not any(
        c.request.params.get("REQUEST") == "GetFeature" and c.request.params.get("RESULTTYPE") != "hits"
        for c in responses.calls
    )

    acknowledged = make_settings(tmp_path, access_constraints_acknowledged=True)
    _, _, pages = harvest_features(acknowledged, mapping, _http_transport(acknowledged))
    assert pages


def test_missing_identifier_in_schema_blocks_feature_requests(settings, mapping, fixture_copy):
    xsd = (fixture_copy / "describe_feature_type.xsd").read_text(encoding="utf-8")
    xsd = xsd.replace('<element name="objekt_id" type="string" minOccurs="0" maxOccurs="1"/>', "")
    (fixture_copy / "describe_feature_type.xsd").write_text(xsd, encoding="utf-8")
    transport = FixtureTransport(fixture_copy)

    with pytest.raises(SchemaMismatchError, match="objekt_id"):
        harvest_features(settings, mapping, transport)

    assert not any(r["REQUEST"] == "GetFeature" and "STARTINDEX" in r for r in transport.requests)


def test_discover_reports_blockers_instead_of_failing(settings, mapping, fixture_copy):
    xsd = (fixture_copy / "describe_feature_type.xsd").read_text(encoding="utf-8")
    xsd = xsd.replace('<element name="geom" type="gml:MultiSurfacePropertyType" minOccurs="0" maxOccurs="1"/>', "")
    (fixture_copy / "describe_feature_type.xsd").write_text(xsd, encoding="utf-8")

    harvester = WfsHarvester(FixtureTransport(fixture_copy), settings, mapping, RawArchive(settings.data_dir / "r"))
    summary = harvester.discover().summary(mapping)

    assert summary["harvest_blockers"] == ["the published schema has no geometry property"]


def test_retry_policy_is_configured(settings):
    session = build_session(settings)
    retry = session.adapters["https://"].max_retries

    assert retry.total == settings.http_retries
    assert set(retry.status_forcelist) == {429, 500, 502, 503, 504}
    assert retry.allowed_methods == frozenset({"GET"})
    assert retry.respect_retry_after_header is True
    assert "IRIS-OEK-RP" in session.headers["User-Agent"]
