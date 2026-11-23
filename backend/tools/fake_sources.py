"""Fake Salesforce org and fake REST API, for tests and for trying the connectors locally. NOT for production.

Run both servers:   ALLOW_LOCAL_API_URLS=true is needed in the API's environment, then
    python tools/fake_sources.py
  Salesforce  http://localhost:9100   auth client credentials, client id "lumen-dev", secret "dev-secret"
  REST API    http://localhost:9200   API key header X-API-Key: dev-key
              endpoints /v1/orders (page), /v2/orders (offset), /v3/orders (cursor),
                        /v4/orders (next link), /v5/orders (Link header)
              changed-since parameter: updated_since

Tests use the same classes through httpx.MockTransport(fake.handler).
"""

import base64
import csv
import io
import json
import re
import secrets
import threading
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode

import httpx
import jwt

API_VERSIONS = ["64.0", "65.0", "66.0"]


def _iso_sf(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


# ======================================================================= Salesforce
FIELDS: dict[str, list[tuple[str, str]]] = {
    "Account": [
        ("Id", "id"),
        ("IsDeleted", "boolean"),
        ("Name", "string"),
        ("Industry", "picklist"),
        ("AnnualRevenue", "currency"),
        ("NumberOfEmployees", "int"),
        ("BillingAddress", "address"),
        ("BillingCountry", "string"),
        ("CreatedDate", "datetime"),
        ("SystemModstamp", "datetime"),
    ],
    "Opportunity": [
        ("Id", "id"),
        ("IsDeleted", "boolean"),
        ("Name", "string"),
        ("AccountId", "reference"),
        ("Amount", "currency"),
        ("StageName", "picklist"),
        ("CloseDate", "date"),
        ("Probability", "percent"),
        ("IsWon", "boolean"),
        ("SystemModstamp", "datetime"),
    ],
}
LABELS = {"Account": "Account", "Opportunity": "Opportunity"}


class FakeSalesforce:
    def __init__(
        self,
        base_url: str = "https://acme.my.salesforce.com",
        client_id: str = "lumen-client",
        client_secret: str = "sf-secret",
        opportunities: int = 300,
        page_size: int = 200,
        jwt_public_key: Any = None,
        jwt_username: str = "integration@acme.com",
    ):
        self.base_url = base_url.rstrip("/")
        self.client_id, self.client_secret = client_id, client_secret
        self.jwt_public_key, self.jwt_username = jwt_public_key, jwt_username
        self.page_size = page_size
        self.tokens: set[str] = set()
        self.cursors: dict[str, list[dict]] = {}
        self.jobs: dict[str, dict] = {}
        self.calls: list[str] = []
        self.api_calls_left = 100_000
        self.clock = datetime(2026, 1, 1, tzinfo=UTC)
        self.data: dict[str, list[dict]] = {"Account": [], "Opportunity": []}
        countries = ["United Arab Emirates", "Saudi Arabia", "Qatar"]
        for i in range(1, 21):
            self.data["Account"].append(
                {
                    "Id": f"001{i:015d}",
                    "IsDeleted": False,
                    "Name": f"Account {i}",
                    "Industry": ["Banking", "Retail", "Energy"][i % 3],
                    "AnnualRevenue": i * 100000.0,
                    "NumberOfEmployees": i * 10,
                    "BillingAddress": {"city": "Dubai"},
                    "BillingCountry": countries[i % 3],
                    "CreatedDate": self._tick(),
                    "SystemModstamp": self.clock,
                }
            )
        for i in range(1, opportunities + 1):
            self.add_opportunity(i, amount=float(i * 1000))

    def _tick(self) -> datetime:
        self.clock += timedelta(minutes=1)
        return self.clock

    # ---- helpers for tests
    def add_opportunity(self, i: int, amount: float, stage: str = "Prospecting") -> dict:
        rec = {
            "Id": f"006{i:015d}",
            "IsDeleted": False,
            "Name": f"Deal {i}",
            "AccountId": f"001{1 + i % 20:015d}",
            "Amount": amount,
            "StageName": stage,
            "CloseDate": date(2026, 1 + i % 12, 1 + i % 28),
            "Probability": 10.0 + i % 90,
            "IsWon": i % 5 == 0,
            "SystemModstamp": self._tick(),
        }
        self.data["Opportunity"].append(rec)
        return rec

    def update(self, obj: str, record_id: str, **values) -> None:
        for rec in self.data[obj]:
            if rec["Id"] == record_id:
                rec.update(values, SystemModstamp=self._tick())

    def delete(self, obj: str, record_id: str) -> None:
        self.update(obj, record_id, IsDeleted=True)

    # ---- SOQL (just enough for the connector)
    def _value(self, literal: str) -> Any:
        literal = literal.strip()
        if literal.startswith("'"):
            return literal[1:-1].replace("\\'", "'")
        if literal in {"true", "false"}:
            return literal == "true"
        if re.match(r"^\d{4}-\d{2}-\d{2}T", literal):
            return datetime.fromisoformat(literal.replace("Z", "+00:00"))
        if re.match(r"^\d{4}-\d{2}-\d{2}$", literal):
            return date.fromisoformat(literal)
        return float(literal)

    def _matches(self, rec: dict, where: str) -> bool:
        for cond in re.split(r"\s+AND\s+", where, flags=re.I):
            cond = cond.strip().strip("()").strip()
            m = re.match(r"^(\w+)\s*(>=|<=|!=|=|>|<)\s*(.+)$", cond)
            if not m:
                raise ValueError(f"unsupported condition: {cond}")
            field, op, lit = m.groups()
            left, right = rec.get(field), self._value(lit)
            if left is None:
                return False
            ok = {
                ">": left > right,
                ">=": left >= right,
                "<": left < right,
                "<=": left <= right,
                "=": left == right,
                "!=": left != right,
            }[op]
            if not ok:
                return False
        return True

    def _query(self, soql: str, include_deleted: bool) -> tuple[list[str], list[dict]] | int:
        m = re.match(r"^SELECT\s+(.+?)\s+FROM\s+(\w+)(?:\s+WHERE\s+(.+))?$", soql.strip(), re.I | re.S)
        if not m:
            raise ValueError("MALFORMED_QUERY")
        fields_txt, obj, where = m.groups()
        if obj not in self.data:
            raise ValueError(f"INVALID_TYPE: sObject type '{obj}' is not supported.")
        rows = [r for r in self.data[obj] if include_deleted or not r["IsDeleted"]]
        if where:
            rows = [r for r in rows if self._matches(r, where)]
        if fields_txt.strip().upper() == "COUNT()":
            return len(rows)
        fields = [f.strip() for f in fields_txt.split(",")]
        known = {n for n, _ in FIELDS[obj]} | {"Account.Name", "Account.Industry"}
        for f in fields:
            if f not in known:
                raise ValueError(f"INVALID_FIELD: No such column '{f}' on entity '{obj}'")
        return fields, rows

    def _related(self, rec: dict, field: str) -> Any:
        if "." in field:
            acct = next((a for a in self.data["Account"] if a["Id"] == rec.get("AccountId")), None)
            return acct.get(field.split(".", 1)[1]) if acct else None
        return rec.get(field)

    @staticmethod
    def _json_value(v: Any) -> Any:
        if isinstance(v, datetime):
            return _iso_sf(v)
        if isinstance(v, date):
            return v.isoformat()
        return v

    def _json_record(self, obj: str, rec: dict, fields: list[str]) -> dict:
        out: dict[str, Any] = {
            "attributes": {"type": obj, "url": f"/services/data/v66.0/sobjects/{obj}/{rec['Id']}"}
        }
        for f in fields:
            if "." in f:
                rel, sub = f.split(".", 1)
                out.setdefault(rel, {"attributes": {"type": "Account"}})[sub] = self._json_value(
                    self._related(rec, f)
                )
            else:
                out[f] = self._json_value(rec.get(f))
        return out

    # ---- HTTP
    def _error(self, status: int, code: str, message: str) -> httpx.Response:
        return httpx.Response(status, json=[{"errorCode": code, "message": message}])

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append(f"{request.method} {path}")
        if path == "/services/oauth2/token":
            return self._token(request)
        if path == "/services/data/" or path == "/services/data":
            return httpx.Response(
                200, json=[{"version": v, "url": f"/services/data/v{v}"} for v in API_VERSIONS]
            )
        auth = request.headers.get("authorization", "").removeprefix("Bearer ")
        if auth not in self.tokens:
            return self._error(401, "INVALID_SESSION_ID", "Session expired or invalid")
        self.api_calls_left -= 1
        if self.api_calls_left < 0:
            return self._error(403, "REQUEST_LIMIT_EXCEEDED", "TotalRequests Limit exceeded.")
        m = re.match(r"^/services/data/v(\d+\.\d)/(.*)$", path)
        if not m:
            return self._error(404, "NOT_FOUND", "The requested resource does not exist")
        rest = m.group(2)
        q = parse_qs(request.url.query.decode())
        try:
            if rest == "limits":
                return httpx.Response(
                    200, json={"DailyApiRequests": {"Max": 100000, "Remaining": self.api_calls_left}}
                )
            if rest == "sobjects":
                return httpx.Response(
                    200,
                    json={
                        "sobjects": [
                            {"name": n, "label": LABELS[n], "queryable": True, "retrieveable": True}
                            for n in FIELDS
                        ]
                        + [{"name": "LoginHistory", "label": "Login History", "queryable": False}]
                    },
                )
            dm = re.match(r"^sobjects/(\w+)/describe$", rest)
            if dm:
                obj = dm.group(1)
                if obj not in FIELDS:
                    return self._error(404, "NOT_FOUND", "The requested resource does not exist")
                return httpx.Response(
                    200,
                    json={
                        "name": obj,
                        "fields": [{"name": n, "type": t, "label": n} for n, t in FIELDS[obj]],
                    },
                )
            if rest in {"query", "queryAll"}:
                result = self._query(q["q"][0], include_deleted=rest == "queryAll")
                if isinstance(result, int):
                    return httpx.Response(200, json={"totalSize": result, "done": True, "records": []})
                fields, rows = result
                obj = re.search(r"FROM\s+(\w+)", q["q"][0], re.I).group(1)
                records = [self._json_record(obj, r, fields) for r in rows]
                return self._page(records, m.group(1))
            cm = re.match(r"^(?:query|queryAll)/(\w+)$", rest)
            if cm:
                return self._page(self.cursors.pop(cm.group(1), []), m.group(1), key=cm.group(1))
            if rest == "jobs/query" and request.method == "POST":
                body = json.loads(request.content)
                result = self._query(body["query"], include_deleted=body["operation"] == "queryAll")
                job_id = f"750{secrets.token_hex(6)}"
                self.jobs[job_id] = {"result": result, "query": body["query"], "polls": 0}
                return httpx.Response(200, json={"id": job_id, "state": "UploadComplete"})
            jm = re.match(r"^jobs/query/(\w+)$", rest)
            if jm:
                job = self.jobs[jm.group(1)]
                job["polls"] += 1
                return httpx.Response(
                    200,
                    json={"id": jm.group(1), "state": "InProgress" if job["polls"] == 1 else "JobComplete"},
                )
            rm = re.match(r"^jobs/query/(\w+)/results$", rest)
            if rm:
                return self._bulk_results(rm.group(1), q)
        except ValueError as exc:
            code = str(exc).split(":")[0]
            return self._error(400, code if code.isupper() else "MALFORMED_QUERY", str(exc))
        return self._error(404, "NOT_FOUND", "The requested resource does not exist")

    def _page(self, records: list[dict], version: str, key: str | None = None) -> httpx.Response:
        page, rest = records[: self.page_size], records[self.page_size :]
        body: dict[str, Any] = {"totalSize": len(records), "done": not rest, "records": page}
        if rest:
            cursor = f"01g{secrets.token_hex(5)}"
            self.cursors[cursor] = rest
            body["nextRecordsUrl"] = f"/services/data/v{version}/query/{cursor}"
        return httpx.Response(200, json=body)

    def _bulk_results(self, job_id: str, q: dict) -> httpx.Response:
        fields, rows = self.jobs[job_id]["result"]
        start = int(q.get("locator", ["0"])[0])
        size = int(q.get("maxRecords", ["50000"])[0])
        chunk = rows[start : start + size]
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(fields)
        for r in chunk:
            row = []
            for f in fields:
                v = self._related(r, f)
                v = self._json_value(v)
                row.append("" if v is None else ("true" if v is True else "false" if v is False else v))
            w.writerow(row)
        nxt = start + size
        headers = {
            "Sforce-Locator": str(nxt) if nxt < len(rows) else "null",
            "Sforce-NumberOfRecords": str(len(chunk)),
            "Content-Type": "text/csv",
        }
        return httpx.Response(200, text=out.getvalue(), headers=headers)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        grant = form.get("grant_type")
        if grant == "client_credentials":
            if form.get("client_id") != self.client_id:
                return httpx.Response(
                    400, json={"error": "invalid_client_id", "error_description": "client identifier invalid"}
                )
            if form.get("client_secret") != self.client_secret:
                return httpx.Response(
                    400, json={"error": "invalid_client", "error_description": "invalid client credentials"}
                )
        elif grant == "urn:ietf:params:oauth:grant-type:jwt-bearer":
            try:
                claims = jwt.decode(
                    form.get("assertion", ""),
                    self.jwt_public_key,
                    algorithms=["RS256"],
                    audience=["https://login.salesforce.com", "https://test.salesforce.com"],
                )
            except Exception:
                return httpx.Response(
                    400, json={"error": "invalid_grant", "error_description": "invalid assertion"}
                )
            if claims.get("iss") != self.client_id or claims.get("sub") != self.jwt_username:
                return httpx.Response(
                    400,
                    json={
                        "error": "invalid_grant",
                        "error_description": "user hasn't approved this consumer",
                    },
                )
        else:
            return httpx.Response(
                400, json={"error": "unsupported_grant_type", "error_description": "grant type not supported"}
            )
        token = secrets.token_hex(16)
        self.tokens.add(token)
        return httpx.Response(
            200, json={"access_token": token, "instance_url": self.base_url, "token_type": "Bearer"}
        )


# ======================================================================= REST API
class FakeRestApi:
    def __init__(
        self,
        orders: int = 95,
        auth: str = "api_key",
        api_key: str = "dev-key",
        token: str = "tok",
        username: str = "svc",
        password: str = "pw",
        client_id: str = "rest-client",
        client_secret: str = "rest-secret",
    ):
        self.auth, self.api_key, self.token = auth, api_key, token
        self.username, self.password = username, password
        self.client_id, self.client_secret = client_id, client_secret
        self.oauth_tokens: set[str] = set()
        self.rate_limit_next = 0  # answer 429 to this many requests
        self.requests: list[httpx.Request] = []
        self.clock = datetime(2026, 3, 1, tzinfo=UTC)
        self.orders: list[dict] = []
        for i in range(1, orders + 1):
            self.add_order(i)

    def add_order(self, i: int, total: float | None = None, status: str = "paid") -> dict:
        self.clock += timedelta(minutes=5)
        order = {
            "id": i,
            "number": f"SO-{i:05d}",
            "customer": {"name": f"Customer {i % 7}", "country": ["AE", "SA", "QA"][i % 3]},
            "items": [{"sku": f"SKU-{i % 4}", "qty": 1 + i % 3}],
            "total": total if total is not None else round(i * 12.5, 2),
            "status": status,
            "updated_at": self.clock.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.orders.append(order)
        return order

    def update_order(self, i: int, **values) -> None:
        self.clock += timedelta(minutes=5)
        for o in self.orders:
            if o["id"] == i:
                o.update(values, updated_at=self.clock.strftime("%Y-%m-%dT%H:%M:%SZ"))

    def _authorized(self, request: httpx.Request, q: dict) -> bool:
        if self.auth == "none":
            return True
        if self.auth == "api_key":
            return request.headers.get("x-api-key") == self.api_key or q.get("api_key") == self.api_key
        if self.auth == "bearer":
            return request.headers.get("authorization") == f"Bearer {self.token}"
        if self.auth == "basic":
            expected = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            return request.headers.get("authorization") == f"Basic {expected}"
        if self.auth == "oauth2":
            return request.headers.get("authorization", "").removeprefix("Bearer ") in self.oauth_tokens
        return False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        q = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        if path == "/oauth/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form.get("client_id") == self.client_id and form.get("client_secret") == self.client_secret:
                tok = secrets.token_hex(8)
                self.oauth_tokens.add(tok)
                return httpx.Response(
                    200, json={"access_token": tok, "token_type": "bearer", "expires_in": 3600}
                )
            return httpx.Response(401, json={"error": "invalid_client"})
        if self.rate_limit_next > 0:
            self.rate_limit_next -= 1
            return httpx.Response(429, headers={"Retry-After": "1"}, json={"message": "Too many requests"})
        if not self._authorized(request, q):
            return httpx.Response(401, json={"message": "Unauthorized"})
        rows = self.orders
        if q.get("updated_since"):
            since = q["updated_since"]
            rows = [o for o in rows if o["updated_at"] > since]
        if q.get("status"):
            rows = [o for o in rows if o["status"] == q["status"]]
        if path == "/v1/orders":  # page + per_page, records under data.items
            page, per = int(q.get("page", 1)), int(q.get("per_page", 20))
            chunk = rows[(page - 1) * per : page * per]
            return httpx.Response(
                200, json={"data": {"items": chunk}, "meta": {"page": page, "total": len(rows)}}
            )
        if path == "/v2/orders":  # offset + limit, records under results
            off, lim = int(q.get("offset", 0)), int(q.get("limit", 20))
            return httpx.Response(200, json={"results": rows[off : off + lim], "count": len(rows)})
        if path == "/v3/orders":  # cursor
            start, lim = int(q.get("cursor", 0)), int(q.get("limit", 20))
            chunk = rows[start : start + lim]
            nxt = start + lim if start + lim < len(rows) else None
            return httpx.Response(200, json={"items": chunk, "meta": {"next_cursor": nxt}})
        if path == "/v4/orders":  # next link in the body
            after, lim = int(q.get("after", 0)), 25
            chunk = rows[after : after + lim]
            extra = {"updated_since": q["updated_since"]} if q.get("updated_since") else {}
            nxt = (
                f"/v4/orders?{urlencode({'after': after + lim, **extra})}"
                if after + lim < len(rows)
                else None
            )
            return httpx.Response(200, json={"orders": chunk, "links": {"next": nxt}})
        if path == "/v5/orders":  # Link header, the response is a plain list
            after, lim = int(q.get("after", 0)), 30
            headers = {}
            if after + lim < len(rows):
                port = f":{request.url.port}" if request.url.port else ""
                origin = f"{request.url.scheme}://{request.url.host}{port}"
                headers["Link"] = f'<{origin}/v5/orders?after={after + lim}>; rel="next"'
            return httpx.Response(200, json=rows[after : after + lim], headers=headers)
        return httpx.Response(404, json={"message": "Not found"})


# ======================================================================= run as real servers
def _serve(handler, port: int) -> None:
    import uvicorn
    from fastapi import FastAPI, Request
    from fastapi.responses import Response

    app = FastAPI()

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def catch_all(path: str, request: Request):
        req = httpx.Request(
            request.method, str(request.url), headers=request.headers.raw, content=await request.body()
        )
        res = handler(req)
        hop = {"content-length", "content-encoding", "transfer-encoding"}
        headers = {k: v for k, v in res.headers.items() if k.lower() not in hop}
        return Response(content=res.content, status_code=res.status_code, headers=headers)

    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    sf = FakeSalesforce(
        base_url="http://localhost:9100",
        client_id="lumen-dev",
        client_secret="dev-secret",
        opportunities=1200,
    )
    api = FakeRestApi(orders=240, auth="api_key", api_key="dev-key")
    threading.Thread(target=_serve, args=(sf.handler, 9100), daemon=True).start()
    print("Fake Salesforce on http://localhost:9100 (client id lumen-dev, secret dev-secret)")
    print("Fake REST API on http://localhost:9200 (header X-API-Key: dev-key)")
    _serve(api.handler, 9200)
