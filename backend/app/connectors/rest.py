"""Generic REST/JSON APIs.

A connection holds what is shared by an API: base URL, sign-in, headers, where the records are in a
response, and how pages work. Each dataset picks one endpoint path (e.g. /v1/orders?status=open).

config:
  base_url        https://api.example.com
  endpoints       ["/v1/orders", "/v1/customers"]  (offered when creating datasets)
  auth            none | api_key | bearer | basic | oauth2
    api_key_name, api_key_in (header | query)      secret: api_key
    bearer                                          secret: token
    basic: username                                 secret: password
    oauth2 (client credentials): token_url, client_id, scope   secret: client_secret
  headers         {"Accept": "application/json"}  (non-secret extra headers)
  records_path    dotted path to the list of records, e.g. "data.items"; empty = the response itself
  pagination      none | page | offset | cursor | next_link | link_header
    page:        page_param (page), size_param (per_page), page_size, first_page (1)
    offset:      offset_param (offset), size_param (limit), page_size
    cursor:      cursor_param (cursor), cursor_path (e.g. "meta.next_cursor"), size_param, page_size
    next_link:   next_path (e.g. "links.next")
    link_header: (RFC 5988 Link: <...>; rel="next")
  max_pages       safety limit (default 1000)
  since_param     query parameter for incremental syncs, e.g. "updated_since"
  since_format    iso | date | epoch | epoch_ms
"""

import json
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import unquote_plus, urljoin, urlparse

import httpx
import pyarrow as pa

from app.connectors import http
from app.connectors.base import Connector, ConnectorError, ObjectInfo, unique_column_names

AUTH_TYPES = {"none", "api_key", "bearer", "basic", "oauth2"}
PAGINATION_TYPES = {"none", "page", "offset", "cursor", "next_link", "link_header"}
SINCE_FORMATS = {"iso", "date", "epoch", "epoch_ms"}
ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
LINK_NEXT = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?', re.I)
HEADER_NAME = re.compile(r"^[A-Za-z0-9-]{1,100}$")
FORBIDDEN_HEADERS = {"host", "content-length", "transfer-encoding", "connection"}


def dig(data: Any, path: str) -> Any:
    """Follow a dotted path ("data.items", "results.0.rows") through dicts and lists."""
    if not path:
        return data
    for part in path.split("."):
        if isinstance(data, dict):
            data = data.get(part)
        elif isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        else:
            return None
    return data


def flatten(record: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """Nested objects become dotted columns (customer.name); lists are kept as JSON text."""
    if not isinstance(record, dict):
        return {prefix.rstrip(".") or "value": record}
    out: dict[str, Any] = {}
    for key, value in record.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict) and depth < 5:
            out.update(flatten(value, f"{name}.", depth + 1))
        elif isinstance(value, list | dict):
            out[name] = json.dumps(value, ensure_ascii=False, default=str)
        else:
            out[name] = value
    return out


def _to_datetime(value: str) -> datetime:
    v = value.replace("Z", "+00:00").replace(" ", "T", 1)
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)
    dt = datetime.fromisoformat(v)
    return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)


def rows_to_batch(rows: list[dict], names: list[str]) -> pa.RecordBatch:
    arrays = []
    for name in names:
        values = [r.get(name) for r in rows]
        present = [v for v in values if v is not None]
        # Columns whose every value is an ISO date-time string become real timestamps
        if present and all(isinstance(v, str) and ISO_DATETIME.match(v) for v in present):
            try:
                arrays.append(
                    pa.array(
                        [_to_datetime(v) if v else None for v in values], type=pa.timestamp("ms", tz="UTC")
                    )
                )
                continue
            except ValueError:
                pass
        kinds = {type(v) for v in present}
        if len(kinds) > 1 and not kinds <= {int, float}:
            values = [None if v is None else str(v) for v in values]
        try:
            arrays.append(pa.array(values))
        except (pa.ArrowInvalid, pa.ArrowTypeError, OverflowError):
            arrays.append(pa.array([None if v is None else str(v) for v in values], type=pa.string()))
    return pa.RecordBatch.from_arrays(arrays, names=names) if names else pa.RecordBatch.from_pylist([])


class RestConnector(Connector):
    type = "rest"
    label = "REST / JSON API"
    supports_incremental = True
    supports_sql = False
    secret_fields = ("api_key", "token", "password", "client_secret")

    def __init__(self, config: dict[str, Any], secrets: dict[str, str]):
        super().__init__(config, secrets)
        self._oauth_token: str | None = None

    @classmethod
    def incremental_ready(cls, config: dict[str, Any]) -> str | None:
        if not config.get("since_param"):
            return "Set the 'changed since' parameter on the connection to use incremental syncs"
        return None

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        base = str(config.get("base_url", "")).strip().rstrip("/")
        parsed = urlparse(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConnectorError("Enter the API's base URL, e.g. https://api.example.com")
        auth = config.get("auth") or "none"
        if auth not in AUTH_TYPES:
            raise ConnectorError("Unknown sign-in method.")
        pagination = config.get("pagination") or "none"
        if pagination not in PAGINATION_TYPES:
            raise ConnectorError("Unknown pagination style.")
        endpoints = config.get("endpoints") or []
        if isinstance(endpoints, str):
            endpoints = endpoints.splitlines()
        endpoints = [
            e.strip() if e.strip().startswith("/") else f"/{e.strip()}" for e in endpoints if e.strip()
        ]
        if not endpoints:
            raise ConnectorError("Add at least one endpoint path, e.g. /v1/orders")
        headers = {str(k).strip(): str(v) for k, v in (config.get("headers") or {}).items() if str(k).strip()}
        for name in headers:
            if not HEADER_NAME.match(name) or name.lower() in FORBIDDEN_HEADERS:
                raise ConnectorError(f"Header name not allowed: {name}")
            if name.lower() == "authorization":
                raise ConnectorError(
                    "Put credentials in the sign-in settings, not in headers (they are encrypted)."
                )
        out: dict[str, Any] = {
            "base_url": base,
            "endpoints": endpoints,
            "auth": auth,
            "headers": headers,
            "records_path": str(config.get("records_path") or "").strip(),
            "pagination": pagination,
        }
        if auth == "api_key":
            out["api_key_name"] = str(config.get("api_key_name") or "X-API-Key").strip()
            out["api_key_in"] = (
                config.get("api_key_in") if config.get("api_key_in") in {"header", "query"} else "header"
            )
        if auth == "basic":
            out["username"] = str(config.get("username") or "").strip()
            if not out["username"]:
                raise ConnectorError("Basic sign-in needs a username.")
        if auth == "oauth2":
            out["token_url"] = str(config.get("token_url") or "").strip()
            out["client_id"] = str(config.get("client_id") or "").strip()
            out["scope"] = str(config.get("scope") or "").strip()
            if not out["token_url"].startswith(("https://", "http://")) or not out["client_id"]:
                raise ConnectorError("OAuth 2.0 needs a token URL and a client ID.")
        try:
            out["page_size"] = int(config.get("page_size") or 100)
            out["max_pages"] = int(config.get("max_pages") or 1000)
            out["first_page"] = int(
                config.get("first_page") if config.get("first_page") not in (None, "") else 1
            )
        except (TypeError, ValueError) as exc:
            raise ConnectorError("Page size, first page and max pages must be numbers.") from exc
        for key, default in {
            "page_param": "page",
            "offset_param": "offset",
            "size_param": "",
            "cursor_param": "cursor",
            "cursor_path": "",
            "next_path": "",
        }.items():
            out[key] = str(config.get(key) if config.get(key) is not None else default).strip()
        if pagination == "page" and not config.get("size_param"):
            out["size_param"] = "per_page"
        if pagination == "offset" and not config.get("size_param"):
            out["size_param"] = "limit"
        if pagination == "cursor" and not out["cursor_path"]:
            raise ConnectorError("Cursor pagination needs the path of the next cursor in the response.")
        if pagination == "next_link" and not out["next_path"]:
            raise ConnectorError("Next-link pagination needs the path of the next URL in the response.")
        out["since_param"] = str(config.get("since_param") or "").strip()
        out["since_format"] = (
            config.get("since_format") if config.get("since_format") in SINCE_FORMATS else "iso"
        )
        return out

    # ---- requests
    def _auth(self) -> tuple[dict[str, str], dict[str, str], tuple[str, str] | None]:
        c, s = self.config, self.secrets
        headers, params, basic = {}, {}, None
        auth = c.get("auth", "none")
        if auth == "api_key":
            if c.get("api_key_in") == "query":
                params[c["api_key_name"]] = s.get("api_key", "")
            else:
                headers[c["api_key_name"]] = s.get("api_key", "")
        elif auth == "bearer":
            headers["Authorization"] = f"Bearer {s.get('token', '')}"
        elif auth == "basic":
            basic = (c.get("username", ""), s.get("password", ""))
        elif auth == "oauth2":
            headers["Authorization"] = f"Bearer {self._get_oauth_token()}"
        return headers, params, basic

    def _get_oauth_token(self, refresh: bool = False) -> str:
        if self._oauth_token and not refresh:
            return self._oauth_token
        c = self.config
        data = {
            "grant_type": "client_credentials",
            "client_id": c["client_id"],
            "client_secret": self.secrets.get("client_secret", ""),
        }
        if c.get("scope"):
            data["scope"] = c["scope"]
        with http.client() as h:
            r = http.request(h, "POST", c["token_url"], data=data)
        if r.status_code != 200 or "access_token" not in r.text:
            raise ConnectorError(f"The API's OAuth sign-in failed: {http.error_text(r)}")
        self._oauth_token = r.json()["access_token"]
        return self._oauth_token

    def _get(self, h: httpx.Client, url: str, params: dict[str, Any]) -> httpx.Response:
        # Keep any query string already in the URL (next-page links): httpx would replace it with `params`
        parsed = urlparse(url)
        if parsed.query:
            existing = dict(p.split("=", 1) if "=" in p else (p, "") for p in parsed.query.split("&") if p)
            params = {**{k: unquote_plus(v) for k, v in existing.items()}, **params}
            url = parsed._replace(query="").geturl()
        headers, auth_params, basic = self._auth()
        merged_headers = {"Accept": "application/json", **self.config.get("headers", {}), **headers}
        r = http.request(h, "GET", url, params={**params, **auth_params}, headers=merged_headers, auth=basic)
        if r.status_code == 401 and self.config.get("auth") == "oauth2":
            self._get_oauth_token(refresh=True)
            headers, auth_params, basic = self._auth()
            r = http.request(
                h,
                "GET",
                url,
                params={**params, **auth_params},
                headers={**merged_headers, **headers},
                auth=basic,
            )
        if r.status_code in {401, 403}:
            raise ConnectorError(f"The API refused access ({r.status_code}). Check the sign-in settings.")
        if r.status_code == 404:
            raise ConnectorError(f"Endpoint not found: {urlparse(url).path}")
        if r.status_code >= 400:
            raise ConnectorError(f"The API returned an error ({r.status_code}): {http.error_text(r)}")
        return r

    def _url(self, endpoint: str) -> tuple[str, dict[str, str]]:
        """Endpoint path may carry its own query string; returns (url without query, params)."""
        parsed = urlparse(endpoint)
        if parsed.scheme or parsed.netloc:
            raise ConnectorError("Endpoints are paths under the base URL, e.g. /v1/orders")
        params = dict(p.split("=", 1) if "=" in p else (p, "") for p in parsed.query.split("&") if p)
        return f"{self.config['base_url']}{parsed.path}", params

    def _since_value(self, since: Any) -> str:
        fmt = self.config.get("since_format", "iso")
        if isinstance(since, str):
            try:
                since = _to_datetime(since)
            except ValueError:
                return since
        if isinstance(since, datetime):
            since = since if since.tzinfo else since.replace(tzinfo=UTC)
            if fmt == "date":
                return since.date().isoformat()
            if fmt == "epoch":
                return str(int(since.timestamp()))
            if fmt == "epoch_ms":
                return str(int(since.timestamp() * 1000))
            return since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(since, date):
            return since.isoformat()
        return str(since)

    # ---- interface
    def test_connection(self) -> None:
        endpoint = (self.config.get("endpoints") or ["/"])[0]
        url, params = self._url(endpoint)
        with http.client() as h:
            r = self._get(h, url, params)
        try:
            r.json()
        except ValueError as exc:
            raise ConnectorError("The API answered, but not with JSON.") from exc

    def list_objects(self) -> list[ObjectInfo]:
        return [ObjectInfo(name=e, kind="endpoint") for e in self.config.get("endpoints", [])]

    def pages(self, endpoint: str, since: Any = None) -> Iterator[list[Any]]:
        """Yield the records of each page."""
        c = self.config
        url, params = self._url(endpoint)
        if since is not None and c.get("since_param"):
            params[c["since_param"]] = self._since_value(since)
        style, size = c.get("pagination", "none"), int(c.get("page_size") or 100)
        page, offset, cursor, next_url = int(c.get("first_page", 1)), 0, None, None
        with http.client() as h:
            for _ in range(int(c.get("max_pages") or 1000)):
                p = dict(params)
                if style == "page":
                    p[c["page_param"]] = page
                    if c.get("size_param"):
                        p[c["size_param"]] = size
                elif style == "offset":
                    p[c["offset_param"]] = offset
                    if c.get("size_param"):
                        p[c["size_param"]] = size
                elif style == "cursor":
                    if cursor:
                        p[c["cursor_param"]] = cursor
                    if c.get("size_param"):
                        p[c["size_param"]] = size
                target = next_url or url
                r = self._get(h, target, {} if next_url else p)
                try:
                    body = r.json()
                except ValueError as exc:
                    raise ConnectorError("The API did not return JSON.") from exc
                records = dig(body, c.get("records_path", ""))
                if records is None:
                    raise ConnectorError(f"No records found at '{c.get('records_path')}' in the response.")
                if isinstance(records, dict):
                    records = [records]
                if not isinstance(records, list):
                    raise ConnectorError("The records path does not point to a list.")
                yield records

                if style == "none" or not records:
                    return
                if style == "page":
                    if c.get("size_param") and len(records) < size:
                        return
                    page += 1
                elif style == "offset":
                    if len(records) < size:
                        return
                    offset += len(records)
                elif style == "cursor":
                    cursor = dig(body, c["cursor_path"])
                    if not cursor:
                        return
                elif style == "next_link":
                    link = dig(body, c["next_path"])
                    if not link:
                        return
                    next_url = urljoin(target, str(link))
                elif style == "link_header":
                    match = LINK_NEXT.search(r.headers.get("link", ""))
                    if not match:
                        return
                    next_url = urljoin(target, match.group(1))
            raise ConnectorError(
                f"Stopped after {c.get('max_pages')} pages. "
                "Raise 'max pages' on the connection if that is expected."
            )

    def extract(
        self,
        source: dict[str, Any],
        since: Any = None,
        incremental_column: str | None = None,
        batch_size: int = 50_000,
        inclusive: bool = False,
    ) -> Iterator[pa.RecordBatch]:
        endpoint = source.get("object")
        if not endpoint:
            raise ConnectorError("Choose an endpoint.")
        buffer: list[dict] = []
        names: list[str] | None = None
        yielded = False
        for records in self.pages(endpoint, since):
            for record in records:
                buffer.append(flatten(record))
            if len(buffer) >= batch_size:
                names = unique_names(buffer)
                yield rows_to_batch(buffer, names)
                buffer, yielded = [], True
        if buffer or not yielded:
            yield rows_to_batch(buffer, unique_names(buffer) if buffer else (names or []))


def unique_names(rows: list[dict]) -> list[str]:
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    names = list(seen)
    fixed = unique_column_names(names)
    if fixed != names:  # rename keys in place so values follow their new names
        mapping = dict(zip(names, fixed, strict=True))
        for row in rows:
            for old in list(row):
                if mapping[old] != old:
                    row[mapping[old]] = row.pop(old)
    return fixed
