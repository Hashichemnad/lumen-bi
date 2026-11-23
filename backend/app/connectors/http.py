"""HTTP for API connectors: retries with back-off, rate limits, and a guard against unsafe addresses.

The guard stops a connection from being pointed at this server itself or at cloud metadata services
(e.g. http://169.254.169.254), which could leak secrets. Company-internal addresses (10.x, 192.168.x)
are allowed, since internal APIs are a normal data source. ALLOW_LOCAL_API_URLS=true lifts the guard for
development (the local fake servers in tools/).
"""

import ipaddress
import logging
import socket
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlparse

import httpx

from app.connectors.base import ConnectorError
from app.core.config import get_settings

log = logging.getLogger("lumen.http")

# Tests replace this with httpx.MockTransport
transport: httpx.BaseTransport | None = None
sleep: Callable[[float], None] = time.sleep  # tests make waiting instant

RETRY_STATUSES = {429, 502, 503, 504}
MAX_ATTEMPTS = 5
MAX_WAIT_SECONDS = 60
MAX_REDIRECTS = 3
METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}


def client(timeout: float = 60) -> httpx.Client:
    return httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved


def check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConnectorError(f"Not a valid web address: {url}")
    if get_settings().allow_local_api_urls:
        return
    host = parsed.hostname.lower()
    if host in METADATA_HOSTS or host == "localhost" or host.endswith(".localhost"):
        raise ConnectorError(f"Requests to {host} are not allowed.")
    try:
        candidates = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or 443, proto=socket.IPPROTO_TCP)
        except OSError:
            return  # cannot resolve: the request itself will fail with a clear error
        candidates = [ipaddress.ip_address(info[4][0]) for info in infos]
    if any(_blocked_ip(ip) for ip in candidates):
        raise ConnectorError(f"Requests to {host} are not allowed (local or metadata address).")


def _wait_seconds(response: httpx.Response | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after", "")
        if retry_after.isdigit():
            return min(float(retry_after), MAX_WAIT_SECONDS)
    return min(2.0**attempt, MAX_WAIT_SECONDS)


def request(http: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
    """Send with retries on rate limits / temporary errors, following redirects safely."""
    for attempt in range(MAX_ATTEMPTS):
        target, response = url, None
        try:
            for _ in range(MAX_REDIRECTS + 1):
                check_url(target)
                response = http.request(method, target, **kwargs)
                if response.status_code in {301, 302, 303, 307, 308} and "location" in response.headers:
                    target = urljoin(target, response.headers["location"])
                    if response.status_code == 303:
                        method, kwargs = (
                            "GET",
                            {k: v for k, v in kwargs.items() if k not in {"json", "data", "content"}},
                        )
                    continue
                break
            else:
                raise ConnectorError("Too many redirects.")
        except httpx.TimeoutException as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise ConnectorError(
                    f"The server did not answer in time ({urlparse(url).hostname})."
                ) from exc
        except httpx.HTTPError as exc:
            if attempt == MAX_ATTEMPTS - 1:
                raise ConnectorError(f"Could not reach {urlparse(url).hostname}: {exc}") from exc
        else:
            if response.status_code not in RETRY_STATUSES:
                return response
            if attempt == MAX_ATTEMPTS - 1:
                return response
        wait = _wait_seconds(response, attempt)
        log.info("Retrying %s %s in %.0fs (attempt %d)", method, urlparse(url).hostname, wait, attempt + 1)
        sleep(wait)
    raise ConnectorError("Request failed after several attempts.")  # pragma: no cover


def error_text(response: httpx.Response, limit: int = 300) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:limit]
    if isinstance(body, list) and body and isinstance(body[0], dict):  # Salesforce error list
        body = body[0]
    if isinstance(body, dict):
        for key in ("message", "error_description", "error", "detail", "errorCode"):
            if body.get(key):
                return str(body[key])[:limit]
    return str(body)[:limit]
