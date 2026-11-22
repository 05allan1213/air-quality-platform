"""One bounded, non-following HTTP attempt against a registered origin."""

import asyncio
import logging
import re
import ssl
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx
from django.conf import settings

from .contract import CONTRACTS, PROFILES

ORIGIN = "http://s03-origin:8080"
HTTP_DEADLINE = 15


class WireError(ValueError):
    def __init__(self, code, *, responded=False, retry_after=None):
        self.code = code
        self.responded = responded
        self.retry_after = retry_after
        super().__init__(code)


def _retry_after(value, now):
    if not value or len(value) > 64:
        return None
    try:
        if re.fullmatch(r"[0-9]+", value):
            try:
                return now + timedelta(seconds=int(value))
            except OverflowError:
                return datetime.max.replace(tzinfo=UTC)
        at = parsedate_to_datetime(value)
        if at.tzinfo is not None:
            return max(at.astimezone(UTC), now)
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _response_size(response, limit):
    if response.headers.get("Content-Encoding", "identity").strip().lower() != "identity":
        raise WireError("unsupported_encoding", responded=True)
    media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
    if media_type != "application/json":
        raise WireError("invalid_content_type", responded=True)
    length = response.headers.get("Content-Length")
    if length is not None:
        if not re.fullmatch(r"[0-9]{1,10}", length):
            raise WireError("invalid_body", responded=True)
        length = int(length)
        if length > limit:
            raise WireError("response_too_large", responded=True)
    return length


async def _response_status(response):
    status = response.status_code
    now = datetime.now(UTC)
    if status == 429:
        raise WireError(
            "upstream_rate_limited",
            responded=True,
            retry_after=_retry_after(response.headers.get("Retry-After"), now),
        )
    if status in {401, 403}:
        raise WireError("upstream_denied", responded=True)
    if 500 <= status <= 599:
        raise WireError("upstream_transient", responded=True)
    if 300 <= status < 400:
        raise WireError("unexpected_redirect", responded=True)
    if status != 200:
        raise WireError("unexpected_status", responded=True)


def _tls_failure(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        if isinstance(exc, ssl.SSLError):
            return True
        seen.add(id(exc))
        # HTTPX chains explicitly; the underlying TLS backend can wrap the
        # certificate error as implicit context when it closes the stream.
        exc = exc.__cause__ or exc.__context__
    return False


async def _fetch(contract, origin, params, content, headers):
    received_headers = False
    transport = httpx.AsyncHTTPTransport(retries=0, verify=True, trust_env=False)
    try:
        async with (
            asyncio.timeout(HTTP_DEADLINE),
            httpx.AsyncClient(
                transport=transport,
                trust_env=False,
                follow_redirects=False,
                event_hooks={"response": [_response_status]},
                timeout=httpx.Timeout(connect=2, read=5, write=5, pool=1),
            ) as client,
            client.stream(
                contract.method,
                origin + contract.path,
                params=params,
                content=content,
                headers=headers,
            ) as response,
        ):
            received_headers = True
            length = _response_size(response, contract.max_bytes)
            body = bytearray()
            async for chunk in response.aiter_raw():
                if len(body) + len(chunk) > contract.max_bytes:
                    raise WireError("response_too_large", responded=True)
                body.extend(chunk)
            if not body or length is not None and len(body) != length:
                raise WireError("invalid_body", responded=True)
            return bytes(body), datetime.now(UTC)
    except (TimeoutError, httpx.TimeoutException):
        raise WireError("timeout", responded=received_headers) from None
    except httpx.RemoteProtocolError:
        code = "invalid_body" if received_headers else "upstream_transient"
        raise WireError(code, responded=received_headers) from None
    except httpx.TransportError as exc:
        code = "transport_security" if _tls_failure(exc) else "upstream_transient"
        raise WireError(code, responded=received_headers) from None


def fetch(shape, city_code, city_name, target, profile):
    """Perform an authorized attempt and return (body bytes, actual read-completion UTC)."""
    contract = CONTRACTS.get(shape)
    if (
        not getattr(settings, "S03_FIXTURE_HTTP", False)
        or contract is None
        or profile not in PROFILES
    ):
        raise WireError("unregistered_profile")
    params = {}
    if contract.parameter:
        value = city_name if shape == "S" else city_code
        if (
            not isinstance(value, str)
            or not value.strip()
            or not value.isprintable()
            or len(value) > 100
        ):
            raise WireError("invalid_target")
        params[contract.parameter] = value
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    if profile == "f-exact-v1":
        if not isinstance(target, datetime) or target.tzinfo is None:
            raise WireError("invalid_target")
        label = target.astimezone(UTC)
        if label.minute or label.second or label.microsecond:
            raise WireError("invalid_target")
        headers["X-S03-Target"] = label.isoformat()
    return request(contract, ORIGIN, params, b"", headers)


def request(contract, origin, params, content, headers):
    # HTTPX logs URLs and reason phrases, and httpcore can log response headers.
    # Collection diagnostics use fixed WireError codes instead.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.CRITICAL + 1)
    return asyncio.run(_fetch(contract, origin, params, content, headers))
