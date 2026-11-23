"""Salesforce via the REST API and Bulk API 2.0.

Sign-in (server to server, no user interaction):
- client_credentials: a connected app / external client app with "Enable Client Credentials Flow" and a
  Run-As user. Token from the org's My Domain URL.
- jwt: the JWT bearer flow with a certificate uploaded to the app and its private key stored here.

Objects: every queryable object, all fields except compound/binary ones. Small result sets use the REST
query API; large ones (>= bulk_threshold rows) use Bulk API 2.0. Incremental syncs filter on SystemModstamp
(or the chosen column) and use queryAll so records deleted at the source are removed from the copy.
"""

import csv
import io
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import jwt
import pyarrow as pa

from app.connectors import http
from app.connectors.base import DELETED_COLUMN, ColumnInfo, Connector, ConnectorError, ObjectInfo
from app.core.config import get_settings

EXCLUDED_TYPES = {"address", "location", "base64", "complexvalue"}
ARROW_TYPES = {
    "boolean": pa.bool_(),
    "int": pa.int64(),
    "long": pa.int64(),
    "double": pa.float64(),
    "currency": pa.float64(),
    "percent": pa.float64(),
    "date": pa.date32(),
    "datetime": pa.timestamp("ms", tz="UTC"),
}
VERSION = re.compile(r"^\d{2,3}\.0$")
IDENT = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
BULK_POLL_SECONDS = 2.0
BULK_TIMEOUT = timedelta(hours=2)


# ------------------------------------------------------------------ value conversion
def _parse_datetime(value: str) -> datetime:
    v = value.strip().replace("Z", "+00:00")
    v = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", v)  # +0000 -> +00:00
    return datetime.fromisoformat(v).astimezone(UTC)


def convert(value: Any, sf_type: str | None) -> Any:
    """Turn a JSON or CSV value into a Python value matching the Salesforce field type."""
    if value is None or value == "":
        return None
    if sf_type == "boolean":
        return value if isinstance(value, bool) else str(value).lower() == "true"
    if sf_type in {"int", "long"}:
        return int(float(value))
    if sf_type in {"double", "currency", "percent"}:
        return float(value)
    if sf_type == "date":
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    if sf_type == "datetime":
        return _parse_datetime(str(value))
    return value if isinstance(value, str) else str(value)


def soql_literal(value: Any) -> str:
    if isinstance(value, datetime):
        value = value if value.tzinfo else value.replace(tzinfo=UTC)
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def flatten_record(record: dict, prefix: str = "") -> dict:
    """REST records nest relationships ({"Account": {"Name": ...}}); flatten to "Account.Name"."""
    out: dict[str, Any] = {}
    for key, value in record.items():
        if key == "attributes":
            continue
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            if "records" in value:  # child subquery: keep the count, not the rows
                out[name] = value.get("totalSize")
            else:
                out.update(flatten_record(value, f"{name}."))
        else:
            out[name] = value
    return out


def check_soql(soql: str) -> str:
    q = soql.strip().rstrip(";").strip()
    if not re.match(r"^select\b", q, re.I):
        raise ConnectorError("A Salesforce query must start with SELECT.")
    if ";" in q:
        raise ConnectorError("Use a single SOQL query (no semicolons).")
    return q


def add_condition(soql: str, condition: str) -> str:
    if re.search(r"\b(group\s+by|order\s+by|limit|offset)\b", soql, re.I):
        raise ConnectorError(
            "Incremental sync cannot be used with a query that has GROUP BY, ORDER BY, LIMIT or OFFSET."
        )
    match = re.search(r"\bwhere\b", soql, re.I)
    if match:
        return f"{soql[: match.start()]}WHERE ({condition}) AND ({soql[match.end() :].strip()})"
    return f"{soql} WHERE {condition}"


def from_object(soql: str) -> str | None:
    match = re.search(r"\bfrom\s+([A-Za-z0-9_]+)", soql, re.I)
    return match.group(1) if match else None


# ------------------------------------------------------------------ connector
class SalesforceConnector(Connector):
    type = "salesforce"
    label = "Salesforce"
    supports_incremental = True
    supports_sql = True  # custom SOQL
    secret_fields = ("client_secret", "private_key")

    def __init__(self, config: dict[str, Any], secrets: dict[str, str]):
        super().__init__(config, secrets)
        self._token: str | None = None
        self._instance: str | None = None
        self._version: str | None = None
        self._describe: dict[str, list[dict]] = {}

    @classmethod
    def validate_config(cls, config: dict[str, Any]) -> dict[str, Any]:
        auth = config.get("auth") or "client_credentials"
        if auth not in {"client_credentials", "jwt"}:
            raise ConnectorError("Sign-in method must be client credentials or JWT bearer.")
        domain = str(config.get("domain_url", "")).strip().rstrip("/")
        if domain and "://" not in domain:
            domain = f"https://{domain}"
        parsed = urlparse(domain)
        local = get_settings().allow_local_api_urls and parsed.hostname in {"localhost", "127.0.0.1"}
        if not parsed.hostname or (parsed.scheme != "https" and not local):
            raise ConnectorError("Enter your My Domain URL, e.g. https://yourcompany.my.salesforce.com")
        client_id = str(config.get("client_id", "")).strip()
        if not client_id:
            raise ConnectorError("The consumer key (client ID) of the connected app is required.")
        username = str(config.get("username", "")).strip()
        if auth == "jwt" and not username:
            raise ConnectorError("JWT sign-in needs the username of the integration user.")
        version = str(config.get("api_version") or "").strip().lstrip("v")
        if version and not VERSION.match(version):
            raise ConnectorError("API version looks like 66.0 (leave empty to use the newest).")
        try:
            threshold = int(config.get("bulk_threshold") or 50_000)
        except (TypeError, ValueError) as exc:
            raise ConnectorError("Bulk threshold must be a number.") from exc
        return {
            "auth": auth,
            "domain_url": domain,
            "client_id": client_id,
            "username": username,
            "sandbox": bool(config.get("sandbox")),
            "api_version": version,
            "bulk_threshold": max(threshold, 1),
        }

    # ---- authentication
    def _authenticate(self) -> None:
        c = self.config
        token_url = f"{c['domain_url']}/services/oauth2/token"
        if c.get("auth") == "jwt":
            key = self.secrets.get("private_key", "")
            if not key.strip():
                raise ConnectorError("The private key for JWT sign-in is missing.")
            audience = "https://test.salesforce.com" if c.get("sandbox") else "https://login.salesforce.com"
            now = datetime.now(UTC)
            try:
                assertion = jwt.encode(
                    {
                        "iss": c["client_id"],
                        "sub": c["username"],
                        "aud": audience,
                        "exp": now + timedelta(minutes=3),
                    },
                    key,
                    algorithm="RS256",
                )
            except (ValueError, TypeError, jwt.PyJWTError) as exc:
                raise ConnectorError("The private key could not be read (PEM format expected).") from exc
            data = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
        else:
            data = {
                "grant_type": "client_credentials",
                "client_id": c["client_id"],
                "client_secret": self.secrets.get("client_secret", ""),
            }
        with http.client() as h:
            r = http.request(h, "POST", token_url, data=data)
        if r.status_code != 200:
            detail = http.error_text(r)
            hint = {
                "invalid_client_id": "The consumer key is not recognised.",
                "invalid_client": "The consumer key or secret is wrong.",
                "invalid_grant": "Salesforce refused the sign-in. Check that the flow is enabled for the app "
                "and a Run-As user (client credentials) or pre-authorised user (JWT) is set.",
            }
            try:
                code = r.json().get("error", "")
            except ValueError:
                code = ""
            raise ConnectorError(hint.get(code) or f"Salesforce sign-in failed: {detail}")
        body = r.json()
        self._token = body["access_token"]
        self._instance = (body.get("instance_url") or c["domain_url"]).rstrip("/")
        self._version = c.get("api_version") or self._latest_version()

    def _latest_version(self) -> str:
        with http.client() as h:
            r = http.request(h, "GET", f"{self._instance}/services/data/")
        try:
            versions = [v["version"] for v in r.json()]
            return max(versions, key=float)
        except (ValueError, KeyError, TypeError) as exc:
            raise ConnectorError("Could not read the available Salesforce API versions.") from exc

    def _call(
        self, method: str, path: str, *, params: dict | None = None, json: Any = None, raw: bool = False
    ):
        if self._token is None:
            self._authenticate()
        url = path if path.startswith("http") else f"{self._instance}{path}"
        url = url.replace("{v}", f"v{self._version}")
        for attempt in range(2):
            with http.client(timeout=120) as h:
                r = http.request(
                    h,
                    method,
                    url,
                    params=params,
                    json=json,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
            if r.status_code == 401 and attempt == 0:  # token expired: sign in again once
                self._authenticate()
                continue
            break
        if r.status_code >= 400:
            text = http.error_text(r)
            if "REQUEST_LIMIT_EXCEEDED" in r.text:
                raise ConnectorError("Salesforce API request limit reached for today. Try again later.")
            if "INVALID_FIELD" in r.text or "MALFORMED_QUERY" in r.text or "INVALID_TYPE" in r.text:
                raise ConnectorError(f"Salesforce rejected the query: {text}")
            raise ConnectorError(f"Salesforce error ({r.status_code}): {text}")
        if raw:
            return r
        return r.json() if r.content else None

    # ---- interface
    def test_connection(self) -> None:
        self._authenticate()
        self._call("GET", "/services/data/{v}/limits")

    def list_objects(self) -> list[ObjectInfo]:
        body = self._call("GET", "/services/data/{v}/sobjects")
        return [
            ObjectInfo(name=o["name"], kind="object", label=o.get("label"))
            for o in body.get("sobjects", [])
            if o.get("queryable") and o.get("retrieveable", True)
        ]

    def describe(self, obj: str) -> list[dict]:
        if not IDENT.match(obj):
            raise ConnectorError(f"Invalid object name: {obj!r}")
        if obj not in self._describe:
            body = self._call("GET", f"/services/data/{{v}}/sobjects/{obj}/describe")
            self._describe[obj] = [f for f in body.get("fields", []) if f.get("type") not in EXCLUDED_TYPES]
        return self._describe[obj]

    def get_schema(self, source: dict[str, Any]) -> list[ColumnInfo]:
        if source.get("object"):
            from app.connectors.base import lumen_type

            return [
                ColumnInfo(f["name"], lumen_type(ARROW_TYPES.get(f["type"], pa.string())))
                for f in self.describe(source["object"])
            ]
        return super().get_schema(source)

    def extract(
        self,
        source: dict[str, Any],
        since: Any = None,
        incremental_column: str | None = None,
        batch_size: int = 50_000,
        inclusive: bool = False,
    ) -> Iterator[pa.RecordBatch]:
        types: dict[str, str] = {}
        if source.get("object"):
            obj = source["object"]
            fields = self.describe(obj)
            types = {f["name"]: f["type"] for f in fields}
            soql = f"SELECT {', '.join(types)} FROM {obj}"
        elif source.get("sql"):
            soql = check_soql(source["sql"])
            obj = from_object(soql)
            if obj and IDENT.match(obj):
                try:
                    types = {f["name"]: f["type"] for f in self.describe(obj)}
                except ConnectorError:
                    types = {}
        else:
            raise ConnectorError("Choose an object or enter a SOQL query.")

        include_deleted = False
        if since is not None and incremental_column:
            if not IDENT.match(incremental_column):
                raise ConnectorError(f"Invalid field name: {incremental_column!r}")
            op = ">=" if inclusive else ">"
            soql = add_condition(soql, f"{incremental_column} {op} {soql_literal(since)}")
            # queryAll returns deleted records too (IsDeleted = true), so the copy can drop them
            include_deleted = source.get("object") is not None and "IsDeleted" in types

        names = list(types) if source.get("object") else None
        if source.get("object") and self._use_bulk(soql):
            yield from self._bulk(soql, types, names, include_deleted, batch_size)
        else:
            yield from self._rest(soql, types, names, include_deleted, batch_size)

    # ---- REST query API
    def _use_bulk(self, soql: str) -> bool:
        threshold = int(self.config.get("bulk_threshold") or 50_000)
        count_soql = re.sub(
            r"^select\s+.+?\s+from\s", "SELECT COUNT() FROM ", soql, count=1, flags=re.I | re.S
        )
        body = self._call("GET", "/services/data/{v}/query", params={"q": count_soql})
        return int(body.get("totalSize", 0)) >= threshold

    def _rest(self, soql, types, names, include_deleted, batch_size) -> Iterator[pa.RecordBatch]:
        endpoint = "queryAll" if include_deleted else "query"
        body = self._call("GET", f"/services/data/{{v}}/{endpoint}", params={"q": soql})
        rows: list[dict] = []
        yielded = False
        while True:
            rows.extend(flatten_record(r) for r in body.get("records", []))
            while len(rows) >= batch_size:
                yield self._batch(rows[:batch_size], types, names, include_deleted)
                rows, yielded = rows[batch_size:], True
            next_url = body.get("nextRecordsUrl")
            if body.get("done", True) or not next_url:
                break
            body = self._call("GET", next_url)
        if rows or not yielded:
            yield self._batch(rows, types, names, include_deleted)

    # ---- Bulk API 2.0
    def _bulk(self, soql, types, names, include_deleted, batch_size) -> Iterator[pa.RecordBatch]:
        job = self._call(
            "POST",
            "/services/data/{v}/jobs/query",
            json={
                "operation": "queryAll" if include_deleted else "query",
                "query": soql,
                "contentType": "CSV",
                "columnDelimiter": "COMMA",
                "lineEnding": "LF",
            },
        )
        job_id = job["id"]
        deadline = datetime.now(UTC) + BULK_TIMEOUT
        while True:
            status = self._call("GET", f"/services/data/{{v}}/jobs/query/{job_id}")
            state = status.get("state")
            if state == "JobComplete":
                break
            if state in {"Failed", "Aborted"}:
                raise ConnectorError(
                    f"The Salesforce bulk job {state.lower()}: {status.get('errorMessage', '')}"
                )
            if datetime.now(UTC) > deadline:
                raise ConnectorError("The Salesforce bulk job took too long.")
            http.sleep(BULK_POLL_SECONDS)

        locator: str | None = None
        yielded = False
        while True:
            params = {"maxRecords": batch_size}
            if locator:
                params["locator"] = locator
            r = self._call(
                "GET", f"/services/data/{{v}}/jobs/query/{job_id}/results", params=params, raw=True
            )
            reader = csv.DictReader(io.StringIO(r.text))
            rows = list(reader)
            if rows or not yielded:
                yield self._batch(rows, types, names, include_deleted)
                yielded = True
            locator = r.headers.get("Sforce-Locator")
            if not locator or locator == "null":
                break

    # ---- rows -> Arrow
    def _batch(
        self, rows: list[dict], types: dict[str, str], names: list[str] | None, include_deleted: bool
    ) -> pa.RecordBatch:
        if names is None:  # custom SOQL: column order from the first row
            names = list(rows[0].keys()) if rows else list(types) or ["Id"]
        arrays, out_names = [], []
        for name in names:
            sf_type = types.get(name)
            values = [convert(r.get(name), sf_type) for r in rows]
            arrow_type = ARROW_TYPES.get(sf_type or "")
            try:
                arrays.append(pa.array(values, type=arrow_type) if arrow_type else pa.array(values))
            except (pa.ArrowInvalid, pa.ArrowTypeError):
                arrays.append(pa.array([None if v is None else str(v) for v in values], type=pa.string()))
            out_names.append(name)
        if include_deleted:
            arrays.append(
                pa.array([convert(r.get("IsDeleted"), "boolean") or False for r in rows], pa.bool_())
            )
            out_names.append(DELETED_COLUMN)
        return pa.RecordBatch.from_arrays(arrays, names=out_names)
