class WfsError(RuntimeError):
    """
    Base class for WFS harvesting errors.
    """


class AccessDeniedError(WfsError):
    """
    The service answered 401/403. Never retried or bypassed.
    """


class WfsServiceError(WfsError):
    """
    The service returned an OWS ExceptionReport.
    """

    def __init__(self, code: str | None, message: str):
        self.code = code
        super().__init__(f"OWS exception {code or '?'}: {message}")


class OutboundUrlBlockedError(WfsError):
    """
    A server-advertised URL or redirect points outside the allowed hosts
    or to a private / internal address (SSRF guard).
    """

    def __init__(self, url: str, reason: str):
        self.url = url
        super().__init__(f"Blocked outbound request to {url}: {reason}")


class ResponseTooLargeError(WfsError):
    """
    A response exceeded IRIS_MAX_RESPONSE_MB.
    """


class AccessConstraintsNotAcknowledgedError(WfsError):
    """
    The service declares AccessConstraints and no human has acknowledged
    them (IRIS_ACCESS_CONSTRAINTS_ACKNOWLEDGED). Checked before any feature
    data is requested.
    """


class SchemaMismatchError(WfsError):
    """
    The published schema lacks a field the harvest cannot work without
    (stable identifier or geometry). Checked before any feature data is
    requested.
    """


class LayerNotAdvertisedError(WfsError):
    """
    The configured feature type is not listed in the capabilities.
    """

    def __init__(self, layer: str, available: list[str]):
        self.layer = layer
        self.available = available
        super().__init__(f"Feature type '{layer}' is not advertised. Available: {available}")
