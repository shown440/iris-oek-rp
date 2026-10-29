import ipaddress
import logging
import socket
import time
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urljoin
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.config.settings import Settings
from app.scrapers.wfs.errors import AccessDeniedError
from app.scrapers.wfs.errors import OutboundUrlBlockedError
from app.scrapers.wfs.errors import ResponseTooLargeError

logger = logging.getLogger(__name__)

RETRY_STATUS = (429, 500, 502, 503, 504)

MAX_REDIRECTS = 5

CHUNK_SIZE = 64 * 1024


def build_session(settings: Settings) -> requests.Session:
    """
    Shared HTTP session for external data sources.

    Same intent as iris-sample's HttpClient (persistent session, project
    User-Agent, retries with exponential backoff, Retry-After support),
    implemented with urllib3's Retry so the policy is declarative.

    Only idempotent GET requests are retried. 401/403 are never retried.
    """

    retry = Retry(
        total=settings.http_retries,
        connect=settings.http_retries,
        read=settings.http_retries,
        status=settings.http_retries,
        backoff_factor=settings.http_backoff_s,
        status_forcelist=RETRY_STATUS,
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )

    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    session.headers.update(
        {
            "User-Agent": settings.user_agent,
            "Accept": "application/json, application/xml, text/xml, */*;q=0.5",
        }
    )

    return session


# =====================================================
# OUTBOUND URL POLICY (SSRF guard)
# =====================================================


def _is_internal_address(host: str) -> bool:
    """
    True if the host is, or resolves to, a private / loopback / link-local /
    reserved address (e.g. 127.0.0.1, 10.x, 169.254.169.254).
    """

    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(info[4][0]) for info in socket.getaddrinfo(host, None)]
        except socket.gaierror:
            return False  # unresolvable: the request itself will fail

    return any(
        a.is_private or a.is_loopback or a.is_link_local or a.is_reserved or a.is_multicast or a.is_unspecified
        for a in addresses
    )


class UrlPolicy:
    """
    Decides which URLs the harvester may request.

    The configured endpoint is trusted (operator choice). Every other URL -
    operation URLs advertised in the capabilities and redirect targets -
    must:

        - keep HTTPS if the endpoint uses HTTPS
        - be on the endpoint's domain (e.g. *.rlp.de) or in
          IRIS_WFS_ALLOWED_HOSTS
        - not resolve to an internal address, unless explicitly allowed
    """

    def __init__(self, endpoint: str, allowed_hosts: Iterable[str] = ()):
        parsed = urlsplit(endpoint)
        self.endpoint_host = (parsed.hostname or "").lower()
        self.require_https = parsed.scheme == "https"
        self.allowed_hosts = {h.strip().lower() for h in allowed_hosts if h.strip()}

        labels = self.endpoint_host.split(".")
        self.parent_domain = ".".join(labels[-2:]) if len(labels) >= 2 else self.endpoint_host

    @classmethod
    def from_settings(cls, settings: Settings) -> UrlPolicy:
        return cls(settings.wfs_url, settings.wfs_allowed_hosts.split(","))

    def check(self, url: str) -> None:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()

        if parsed.scheme not in ("http", "https"):
            raise OutboundUrlBlockedError(url, f"scheme '{parsed.scheme}' not allowed")

        if self.require_https and parsed.scheme != "https":
            raise OutboundUrlBlockedError(url, "downgrade from HTTPS")

        if host == self.endpoint_host or host in self.allowed_hosts:
            return

        if host != self.parent_domain and not host.endswith(f".{self.parent_domain}"):
            raise OutboundUrlBlockedError(url, f"host not on {self.parent_domain} and not in IRIS_WFS_ALLOWED_HOSTS")

        if _is_internal_address(host):
            raise OutboundUrlBlockedError(url, "resolves to an internal address")


# =====================================================
# GETTER
# =====================================================


@dataclass(frozen=True)
class FetchedResponse:
    url: str
    status_code: int
    content: bytes
    content_type: str


class RateLimitedGetter:
    """
    Controlled GET requests:

        - minimum delay between consecutive requests (politeness)
        - every URL and redirect target checked by UrlPolicy
        - response body capped at max_bytes (streamed)
        - 401/403 stop the harvest; never bypassed
    """

    def __init__(
        self,
        session: requests.Session,
        timeout_s: float,
        delay_s: float,
        policy: UrlPolicy,
        max_bytes: int,
    ):
        self.session = session
        self.timeout_s = timeout_s
        self.delay_s = delay_s
        self.policy = policy
        self.max_bytes = max_bytes
        self._last_request = 0.0

    @classmethod
    def from_settings(cls, settings: Settings) -> RateLimitedGetter:
        return cls(
            build_session(settings),
            settings.http_timeout_s,
            settings.request_delay_s,
            UrlPolicy.from_settings(settings),
            int(settings.max_response_mb * 1024 * 1024),
        )

    def _wait(self) -> None:
        wait = self.delay_s - (time.monotonic() - self._last_request)

        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, params: dict[str, str] | None) -> FetchedResponse:
        self.policy.check(url)

        for _ in range(MAX_REDIRECTS + 1):
            self._wait()
            logger.info("GET %s %s", url, params)

            try:
                response = self.session.get(
                    url, params=params, timeout=self.timeout_s, allow_redirects=False, stream=True
                )
            finally:
                self._last_request = time.monotonic()

            if not response.is_redirect:
                break

            target = urljoin(response.url, response.headers["Location"])
            response.close()
            self.policy.check(target)
            url, params = target, None
        else:
            raise OutboundUrlBlockedError(url, f"more than {MAX_REDIRECTS} redirects")

        with response:
            if response.status_code in (401, 403):
                raise AccessDeniedError(
                    f"HTTP {response.status_code} from {response.url} - access is restricted; "
                    "the harvester does not attempt to bypass it."
                )

            response.raise_for_status()

            return FetchedResponse(
                url=response.url,
                status_code=response.status_code,
                content=self._read_capped(response),
                content_type=response.headers.get("Content-Type", ""),
            )

    def _read_capped(self, response: requests.Response) -> bytes:
        declared = response.headers.get("Content-Length")

        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            raise ResponseTooLargeError(f"{response.url} declares {int(declared):,} bytes > limit {self.max_bytes:,}")

        body = bytearray()

        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
            body.extend(chunk)

            if len(body) > self.max_bytes:
                raise ResponseTooLargeError(f"{response.url} exceeded the {self.max_bytes:,}-byte limit")

        return bytes(body)
