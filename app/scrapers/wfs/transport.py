"""
How WFS requests reach a server.

    HttpTransport     live service over HTTPS (retries, rate limit)
    FixtureTransport  offline WFS 2.0 emulator over recorded fixtures

Both return the same WfsResponse, so discovery, paging, parsing and
loading run the identical code path in live and fixture mode.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

from app.scrapers.base.http_client import RateLimitedGetter


@dataclass(frozen=True)
class WfsResponse:
    url: str
    content: bytes
    content_type: str


class WfsTransport(Protocol):
    endpoint: str

    def get(self, params: dict[str, str], url: str | None = None) -> WfsResponse: ...


class HttpTransport:
    def __init__(self, endpoint: str, getter: RateLimitedGetter):
        self.endpoint = endpoint
        self.getter = getter

    def get(self, params: dict[str, str], url: str | None = None) -> WfsResponse:
        response = self.getter.get(url or self.endpoint, params)

        return WfsResponse(url=response.url, content=response.content, content_type=response.content_type)


# =====================================================
# FIXTURE
# =====================================================

_EXCEPTION = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">'
    '<ows:Exception exceptionCode="{code}" locator="{locator}">'
    "<ows:ExceptionText>{text}</ows:ExceptionText></ows:Exception></ows:ExceptionReport>"
)

_HITS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" '
    'numberMatched="{matched}" numberReturned="0" timeStamp="{timestamp}"/>'
)


class FixtureTransport:
    """
    Serves capabilities.xml, describe_feature_type.xsd and features.json
    like a WFS 2.0 server: honours TYPENAMES, RESULTTYPE=hits, SORTBY,
    STARTINDEX and COUNT, and answers bad requests with an OWS
    ExceptionReport (HTTP 200, as many real servers do).
    """

    endpoint = "fixture://luwg/wfs/kompensation"

    def __init__(self, fixture_dir: Path):
        self.capabilities = (fixture_dir / "capabilities.xml").read_bytes()
        self.schema = (fixture_dir / "describe_feature_type.xsd").read_bytes()
        self.collection = json.loads((fixture_dir / "features.json").read_text(encoding="utf-8"))
        self.type_name = self.collection["name"]
        self.timestamp = self.collection.get("timeStamp", "")
        self.requests: list[dict[str, str]] = []

    def _response(self, params: dict[str, str], content: bytes, content_type: str) -> WfsResponse:
        return WfsResponse(f"{self.endpoint}?{urlencode(params)}", content, content_type)

    def _exception(self, params: dict[str, str], code: str, locator: str, text: str) -> WfsResponse:
        body = _EXCEPTION.format(code=code, locator=locator, text=text).encode()
        return self._response(params, body, "application/xml")

    def get(self, params: dict[str, str], url: str | None = None) -> WfsResponse:
        query = {k.upper(): str(v) for k, v in params.items()}
        self.requests.append(query)
        request = query.get("REQUEST", "").lower()

        if request == "getcapabilities":
            return self._response(params, self.capabilities, "application/xml")

        type_names = query.get("TYPENAMES") or query.get("TYPENAME")

        if request in ("describefeaturetype", "getfeature") and type_names != self.type_name:
            return self._exception(params, "InvalidParameterValue", "typeNames", f"Unknown type {type_names}")

        if request == "describefeaturetype":
            return self._response(params, self.schema, "application/xml")

        if request != "getfeature":
            return self._exception(params, "OperationNotSupported", "request", request)

        features = list(self.collection["features"])

        if query.get("RESULTTYPE", "").lower() == "hits":
            body = _HITS.format(matched=len(features), timestamp=self.timestamp).encode()
            return self._response(params, body, "application/xml")

        if sort_by := query.get("SORTBY"):
            field, _, direction = sort_by.partition(" ")
            features.sort(
                key=lambda f: (f["properties"].get(field) is None, str(f["properties"].get(field) or "")),
                reverse=direction.upper() == "DESC",
            )

        start = int(query.get("STARTINDEX", 0))
        count = int(query.get("COUNT") or query.get("MAXFEATURES") or len(features))
        page = features[start : start + count]

        body = {
            "type": "FeatureCollection",
            "crs": self.collection["crs"],
            "numberMatched": len(features),
            "numberReturned": len(page),
            "timeStamp": self.timestamp,
            "features": page,
        }

        return self._response(params, json.dumps(body).encode(), "application/json; subtype=geojson")
