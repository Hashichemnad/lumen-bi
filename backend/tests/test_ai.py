# ruff: noqa: F811 - pytest fixtures imported from test_dashboards are used as parameters
"""Milestone 13 AI: providers (admins only, keys never returned), routing (sensitive data only to
self-hosted models, fail closed), Ask Data (fields checked, row-level security in the prompt and the
result), explain, formula helper, auto-dashboard, model comparison, request limits, the daily digest."""

import json

import httpx
import pytest

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import AiRequest, AuditLog, Chart, Dashboard, Dataset, User
from app.services.ai import digest, features, providers
from app.services.ai.providers import Reply, parse_json
from app.services.email import OUTBOX
from tests.test_dashboards import world  # noqa: F401 - fixture

API = "/api/v1"
CLOUD = {"name": "Claude", "kind": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-secret-1"}
LOCAL = {
    "name": "In-house",
    "kind": "openai_compatible",
    "base_url": "http://ollama.internal:11434/v1",
    "model": "llama3.3",
    "local": True,
}
BAR = {
    "title": "Sales by region",
    "chart": "bar",
    "dimensions": [{"field": "region"}],
    "measures": [{"field": "amount", "agg": "sum"}],
    "sort": {"by": "amount", "dir": "desc"},
    "note": "Total amount by region",
}


class FakeModel:
    """Stands in for every model call; answers in order and records what was sent."""

    def __init__(self):
        self.replies: list[str] = []
        self.calls: list[dict] = []

    def __call__(self, p, system, prompt, max_tokens=2000):
        self.calls.append({"provider": p.name, "system": system, "prompt": prompt})
        if not self.replies:
            return Reply("The answer.", 10, 5)
        return Reply(self.replies.pop(0), 10, 5)


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(providers, "call_model", fake)
    monkeypatch.setattr(features, "call_model", fake)
    return fake


@pytest.fixture
def ai(world, model):
    admin = world["admin@test.com"]
    r = admin.post(f"{API}/admin/ai/providers", json=CLOUD)
    assert r.status_code == 201, r.text
    # Ask Data needs access to the dataset itself (the world dataset is only reachable through dashboards)
    r = world["owner"].put(f"{API}/datasets/{world['ds']['id']}/access", json={"everyone": True})
    assert r.status_code == 200, r.text
    return {**world, "model": model}


# ------------------------------------------------------------------ providers
def test_providers_are_admin_only_and_keys_never_returned(world):
    admin = world["admin@test.com"]
    for c in (world["owner"], world["viewer@test.com"]):
        assert c.get(f"{API}/admin/ai/providers").status_code == 403
        assert c.post(f"{API}/admin/ai/providers", json=CLOUD).status_code == 403
    r = admin.post(f"{API}/admin/ai/providers", json=CLOUD)
    assert r.status_code == 201
    p = r.json()
    assert p["has_key"] is True and "api_key" not in p and "sk-ant" not in json.dumps(p)
    listed = admin.get(f"{API}/admin/ai/providers").json()
    assert "sk-ant" not in json.dumps(listed)
    with SessionLocal() as s:
        row = s.query(AuditLog).filter_by(action="ai.provider_create").one()
        assert "sk-ant" not in json.dumps(row.details)
    # Saving without a key keeps it; an empty key removes it
    body = {**CLOUD, "api_key": None, "name": "Claude 2"}
    assert admin.put(f"{API}/admin/ai/providers/{p['id']}", json=body).json()["has_key"] is True
    assert (
        admin.put(f"{API}/admin/ai/providers/{p['id']}", json={**body, "api_key": ""}).json()["has_key"]
        is False
    )
    assert world["owner"].delete(f"{API}/admin/ai/providers/{p['id']}").status_code == 403
    assert admin.delete(f"{API}/admin/ai/providers/{p['id']}").status_code == 204


def test_only_self_hosted_models_can_be_marked_local(world):
    admin = world["admin@test.com"]
    r = admin.post(f"{API}/admin/ai/providers", json={**CLOUD, "local": True})
    assert r.status_code == 422
    r = admin.post(f"{API}/admin/ai/providers", json={**LOCAL, "base_url": ""})
    assert r.status_code == 422
    r = admin.post(f"{API}/admin/ai/providers", json={**LOCAL, "base_url": "ftp://x"})
    assert r.status_code == 422
    assert admin.post(f"{API}/admin/ai/providers", json=LOCAL).status_code == 201


def test_one_default_provider(world):
    admin = world["admin@test.com"]
    a = admin.post(f"{API}/admin/ai/providers", json={**CLOUD, "is_default": True}).json()
    b = admin.post(f"{API}/admin/ai/providers", json={**LOCAL, "is_default": True}).json()
    listed = {p["id"]: p for p in admin.get(f"{API}/admin/ai/providers").json()["providers"]}
    assert listed[b["id"]]["is_default"] and not listed[a["id"]]["is_default"]


def test_status_shows_features(world, model):
    viewer = world["viewer@test.com"]
    assert viewer.get(f"{API}/ai/status").json()["available"] is False
    world["admin@test.com"].post(f"{API}/admin/ai/providers", json={**CLOUD, "features": ["ask"]})
    st = viewer.get(f"{API}/ai/status").json()
    assert st["available"] and st["features"]["ask"] and not st["features"]["formula"]


# ------------------------------------------------------------------ Ask Data
def test_ask_builds_a_chart_with_the_askers_rows_only(ai):
    ex, model = ai["ex@test.com"], ai["model"]  # sees region KSA only
    model.replies = [json.dumps(BAR), "KSA sold 300."]
    r = ex.post(f"{API}/ai/ask", json={"question": "Sales by region", "dataset_id": ai["ds"]["id"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["result"]["rows"] == [["KSA", 300.0]]
    assert out["answer"] == "KSA sold 300." and out["chart"] == "bar" and out["provider"] == "Claude"
    assert out["config"]["shelves"]["columns"][0]["field"] == "region"
    assert out["config"]["shelves"]["rows"][0] == {
        "id": "m0",
        "field": "amount",
        "role": "measure",
        "agg": "sum",
    }
    assert out["query"]["sort"] == [{"key": "m0", "dir": "desc"}]
    # The model only ever saw this person's values, and aggregated results
    sent = "\n".join(c["prompt"] for c in model.calls)
    assert '"KSA"' in sent and "UAE" not in sent and "QAT" not in sent and "Dubai" not in sent
    with SessionLocal() as s:
        assert s.query(AiRequest).filter_by(feature="ask", status="ok").count() == 2
        assert s.query(AuditLog).filter_by(action="ai.ask").count() == 1


def test_ask_in_arabic_answers_in_arabic(ai):
    model = ai["model"]
    model.replies = [json.dumps({**BAR, "title": "المبيعات حسب المنطقة"}), "باعت الإمارات 300."]
    r = ai["ex@test.com"].post(
        f"{API}/ai/ask", json={"question": "المبيعات حسب المنطقة", "dataset_id": ai["ds"]["id"]}
    )
    assert r.status_code == 200
    assert "Arabic" in model.calls[0]["system"] and "Arabic" in model.calls[1]["system"]


def test_ask_retries_once_when_the_model_invents_a_field(ai):
    model = ai["model"]
    bad = {**BAR, "measures": [{"field": "Revenue"}]}
    model.replies = [json.dumps(bad), json.dumps(BAR), "ok"]
    r = ai["owner"].post(
        f"{API}/ai/ask", json={"question": "revenue by region", "dataset_id": ai["ds"]["id"]}
    )
    assert r.status_code == 200, r.text
    assert 'field that does not exist: "Revenue"' in model.calls[1]["prompt"]
    model.replies = [json.dumps(bad), json.dumps(bad)]
    r = ai["owner"].post(f"{API}/ai/ask", json={"question": "revenue", "dataset_id": ai["ds"]["id"]})
    assert r.status_code == 400 and "Revenue" in r.json()["detail"]


def test_ask_when_the_data_cannot_answer(ai):
    model = ai["model"]
    model.replies = ['{"error": "There is no profit field."}']
    r = ai["owner"].post(f"{API}/ai/ask", json={"question": "profit", "dataset_id": ai["ds"]["id"]})
    assert r.status_code == 422 and r.json()["detail"] == "There is no profit field."
    assert len(model.calls) == 1  # no retry


def test_ask_filters_and_kpi(ai):
    model = ai["model"]
    intent = {
        "title": "Gulf sales",
        "chart": "kpi",
        "measures": [{"field": "amount"}],
        "filters": [
            {"field": "region", "op": "in", "values": ["UAE", "KSA"]},
            {"field": "amount", "op": "gt", "value": 150},
        ],
    }
    model.replies = [json.dumps(intent), "500"]
    admin = ai["admin@test.com"]  # sees every region
    r = admin.post(f"{API}/ai/ask", json={"question": "gulf sales over 150", "dataset_id": ai["ds"]["id"]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["result"]["rows"] == [[500.0]]
    kinds = [f["kind"] for f in out["config"]["filters"]]
    assert kinds == ["values", "range"] and out["config"]["filters"][1]["min"] == 150


def test_ask_picks_the_dataset_when_none_is_given(ai):
    model = ai["model"]
    model.replies = [json.dumps({"dataset": ai["ds"]["id"]}), json.dumps(BAR), "ok"]
    r = ai["owner"].post(f"{API}/ai/ask", json={"question": "sales by region"})
    assert r.status_code == 200, r.text
    assert r.json()["dataset_id"] == ai["ds"]["id"]


def test_ask_needs_explorer_and_access_to_the_dataset(ai):
    body = {"question": "x", "dataset_id": ai["ds"]["id"]}
    assert ai["viewer@test.com"].post(f"{API}/ai/ask", json=body).status_code == 403
    assert (
        ai["viewer@test.com"]
        .post(f"{API}/ai/explain", json={"query": {"dataset": ai["ds"]["id"]}})
        .status_code
        == 403
    )
    r = ai["owner"].put(f"{API}/datasets/{ai['ds']['id']}/access", json={"everyone": False})
    assert r.status_code == 200
    r = ai["ex@test.com"].post(f"{API}/ai/ask", json=body)
    assert r.status_code == 404
    assert ai["model"].calls == []


# ------------------------------------------------------------------ sensitive data
def test_sensitive_data_only_goes_to_self_hosted_models(ai):
    owner, admin, model = ai["owner"], ai["admin@test.com"], ai["model"]
    ds_id = ai["ds"]["id"]
    assert (
        ai["viewer@test.com"].put(f"{API}/datasets/{ds_id}/sensitive", json={"sensitive": True}).status_code
        == 403
    )
    assert (
        ai["ex@test.com"].put(f"{API}/datasets/{ds_id}/sensitive", json={"sensitive": True}).status_code
        == 403
    )
    r = owner.put(f"{API}/datasets/{ds_id}/sensitive", json={"sensitive": True})
    assert r.status_code == 200 and r.json()["sensitive"] is True
    r = owner.post(f"{API}/ai/ask", json={"question": "sales", "dataset_id": ds_id})
    assert r.status_code == 409 and "sensitive" in r.json()["detail"]
    assert model.calls == []  # nothing was sent anywhere
    local = admin.post(f"{API}/admin/ai/providers", json=LOCAL).json()
    model.replies = [json.dumps(BAR), "ok"]
    r = owner.post(f"{API}/ai/ask", json={"question": "sales", "dataset_id": ds_id})
    assert r.status_code == 200 and r.json()["provider"] == "In-house"
    assert {c["provider"] for c in model.calls} == {"In-house"}
    # A self-hosted model that is not allowed this feature does not count
    admin.put(f"{API}/admin/ai/providers/{local['id']}", json={**LOCAL, "features": ["formula"]})
    assert owner.post(f"{API}/ai/ask", json={"question": "sales", "dataset_id": ds_id}).status_code == 409
    with SessionLocal() as s:
        assert s.query(AuditLog).filter_by(action="dataset.sensitive").count() == 1


def test_datasets_built_from_sensitive_data_are_sensitive(db):
    base = Dataset(name="Patients", sensitive=True, columns=[{"name": "a", "type": "string"}])
    db.add(base)
    db.flush()
    union = Dataset(name="All", kind="union", source={"union": {"datasets": [base.id]}})
    joined = Dataset(name="J", kind="join", source={"join": {"base": base.id, "joins": []}})
    other = Dataset(name="Other")
    db.add_all([union, joined, other])
    db.flush()
    assert providers.is_sensitive(db, union) and providers.is_sensitive(db, joined)
    assert not providers.is_sensitive(db, other)


def test_no_provider_fails_closed(world, model):
    world["owner"].put(f"{API}/datasets/{world['ds']['id']}/access", json={"everyone": True})
    r = world["owner"].post(f"{API}/ai/ask", json={"question": "x", "dataset_id": world["ds"]["id"]})
    assert r.status_code == 409 and "not set up" in r.json()["detail"]


def test_hourly_limit(ai, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_hourly_limit", 2)
    ai["model"].replies = [json.dumps(BAR), "ok"]
    body = {"question": "sales", "dataset_id": ai["ds"]["id"]}
    assert ai["owner"].post(f"{API}/ai/ask", json=body).status_code == 200
    assert ai["owner"].post(f"{API}/ai/ask", json=body).status_code == 429


# ------------------------------------------------------------------ explain, formula, dashboard
def test_explain_reruns_the_query_as_the_viewer(ai):
    model = ai["model"]
    model.replies = ["- KSA is the only region."]
    q = {
        "dataset": ai["ds"]["id"],
        "dimensions": [{"field": "region", "key": "d"}],
        "measures": [{"field": "amount", "key": "m"}],
    }
    r = ai["ex@test.com"].post(f"{API}/ai/explain", json={"query": q, "title": "By region"})
    assert r.status_code == 200, r.text
    assert r.json()["text"].startswith("- KSA")
    assert "KSA | 300" in model.calls[0]["prompt"] and "UAE" not in model.calls[0]["prompt"]
    # Someone whose rows are all filtered out gets nothing explained (and nothing is sent)
    r = ai["owner"].post(f"{API}/ai/explain", json={"query": q})
    assert r.status_code == 400 and len(model.calls) == 1


def test_formula_helper_checks_the_formula(ai):
    model = ai["model"]
    model.replies = [
        json.dumps(
            {"name": "Big", "formula": "IF [amount] > 250 THEN 'Big' ELSE 'Small' EN", "explanation": "x"}
        ),
        json.dumps(
            {"name": "Big", "formula": "IF [amount] > 250 THEN 'Big' ELSE 'Small' END", "explanation": "x"}
        ),
    ]
    r = ai["admin@test.com"].post(
        f"{API}/ai/formula", json={"dataset_id": ai["ds"]["id"], "description": "big orders over 250"}
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["formula"].endswith("END") and out["type"] == "string" and "Big" in out["sample"]
    assert "does not work" in model.calls[1]["prompt"]
    for c in (ai["viewer@test.com"], ai["ex@test.com"]):
        assert (
            c.post(f"{API}/ai/formula", json={"dataset_id": ai["ds"]["id"], "description": "xyz"}).status_code
            == 403
        )


def test_auto_dashboard(ai):
    model = ai["model"]
    model.replies = [
        json.dumps(
            {
                "name": "Sales overview",
                "description": "Sales at a glance",
                "charts": [
                    {"title": "Total sales", "chart": "kpi", "measures": [{"field": "amount"}]},
                    {"title": "Orders", "chart": "kpi", "measures": [{"field": "*", "agg": "count"}]},
                    {**BAR},
                    {
                        "title": "Broken",
                        "chart": "bar",
                        "dimensions": [{"field": "nope"}],
                        "measures": [{"field": "amount"}],
                    },
                    {
                        "title": "Trend",
                        "chart": "line",
                        "dimensions": [{"field": "order_date", "grain": "month"}],
                        "measures": [{"field": "amount"}],
                    },
                ],
            }
        )
    ]
    ex = ai["ex@test.com"]
    r = ex.post(f"{API}/ai/dashboard", json={"dataset_id": ai["ds"]["id"], "goal": "sales"})
    assert r.status_code == 201, r.text
    d = ex.get(f"{API}/dashboards/{r.json()['id']}").json()
    tiles = d["config"]["tabs"][0]["items"]
    assert [t["title"] for t in tiles] == ["Total sales", "Orders", "Sales by region", "Trend"]
    assert tiles[0]["desktop"] == {"x": 0, "y": 0, "w": 6, "h": 4}
    assert tiles[2]["desktop"]["y"] == 4 and tiles[3]["desktop"]["x"] == 6
    with SessionLocal() as s:
        ex_id = s.query(User).filter_by(email="ex@test.com").one().id
        assert s.query(Chart).filter_by(owner_id=ex_id).count() == 4
        assert s.get(Dashboard, d["id"]).owner_id == ex_id
    assert (
        ai["viewer@test.com"].post(f"{API}/ai/dashboard", json={"dataset_id": ai["ds"]["id"]}).status_code
        == 403
    )


def test_compare_providers(ai):
    admin, model = ai["admin@test.com"], ai["model"]
    local = admin.post(f"{API}/admin/ai/providers", json=LOCAL).json()
    cloud = admin.get(f"{API}/admin/ai/providers").json()["providers"]
    cloud_id = next(p["id"] for p in cloud if p["name"] == "Claude")
    body = {
        "dataset_id": ai["ds"]["id"],
        "questions": [
            {"question": "sales by region", "expect_fields": ["region", "amount"], "expect_chart": "bar"}
        ],
        "provider_ids": [cloud_id, local["id"]],
    }
    assert admin.put(f"{API}/admin/ai/test-set", json=body).status_code == 200
    assert admin.get(f"{API}/admin/ai/test-set").json()["questions"][0]["expect_chart"] == "bar"
    model.replies = [json.dumps(BAR), json.dumps({**BAR, "chart": "line"})]
    res = {r["provider"]: r for r in admin.post(f"{API}/admin/ai/compare", json=body).json()["results"]}
    assert res["Claude"]["score"] == 100 and res["In-house"]["score"] == 0
    # Sensitive data: the cloud model is skipped, not called
    ai["owner"].put(f"{API}/datasets/{ai['ds']['id']}/sensitive", json={"sensitive": True})
    model.calls.clear()
    model.replies = [json.dumps(BAR)]
    res = {r["provider"]: r for r in admin.post(f"{API}/admin/ai/compare", json=body).json()["results"]}
    assert res["Claude"]["skipped"] and res["In-house"]["score"] == 100
    assert {c["provider"] for c in model.calls} == {"In-house"}
    assert ai["owner"].post(f"{API}/admin/ai/compare", json=body).status_code == 403


def test_usage_is_admin_only(ai):
    ai["model"].replies = [json.dumps(BAR), "ok"]
    ai["admin@test.com"].post(f"{API}/ai/ask", json={"question": "sales", "dataset_id": ai["ds"]["id"]})
    assert ai["owner"].get(f"{API}/admin/ai/usage").status_code == 403
    u = ai["admin@test.com"].get(f"{API}/admin/ai/usage").json()
    assert u["summary"][0]["requests"] == 2 and u["recent"][0]["user"] == "admin@test.com"


# ------------------------------------------------------------------ talking to models
def _transport(monkeypatch, handler):
    monkeypatch.setattr(providers, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def test_anthropic_request(monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    _transport(monkeypatch, handler)
    from app.core.crypto import encrypt
    from app.models import AiProvider

    p = AiProvider(name="c", kind="anthropic", model="claude-sonnet-5-5", api_key_enc=encrypt("k1"))
    reply = providers.call_model(p, "sys", "hello", 50)
    assert reply.text == "hi" and reply.input_tokens == 3
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "k1" and seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"] == {
        "model": "claude-sonnet-5-5",
        "max_tokens": 50,
        "system": "sys",
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_openai_compatible_request_and_errors(monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        if seen["body"]["model"] == "bad":
            return httpx.Response(401, json={"error": {"message": "invalid key"}})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    _transport(monkeypatch, handler)
    from app.models import AiProvider

    p = AiProvider(
        name="l", kind="openai_compatible", base_url="http://llm.local/v1", model="llama", local=True
    )
    assert providers.call_model(p, "s", "q", 10).text == "ok"
    assert seen["url"] == "http://llm.local/v1/chat/completions" and seen["body"]["max_tokens"] == 10
    assert seen["body"]["messages"][0] == {"role": "system", "content": "s"}
    p.model = "bad"
    with pytest.raises(providers.AiError, match="invalid key"):
        providers.call_model(p, "s", "q")


def test_parse_json():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": {"b": "}"}} thanks') == {"a": {"b": "}"}}
    with pytest.raises(providers.AiError):
        parse_json("no json here")


# ------------------------------------------------------------------ digest
def test_daily_digest(ai):
    owner, dash = ai["owner"], ai["dash"]
    viewer = ai["viewer@test.com"]
    owner.put(f"{API}/dashboards/{dash['id']}/sharing", json={"everyone": "view", "users": [], "groups": []})
    assert viewer.put(f"{API}/dashboards/{dash['id']}/favorite").status_code == 204
    r = viewer.put(f"{API}/me/preferences", json={"ai_digest": True})
    assert r.status_code == 200, r.text
    OUTBOX.clear()
    ai["model"].replies = ["Sales are 300 in UAE."]
    from datetime import datetime
    from zoneinfo import ZoneInfo

    morning = datetime.now(ZoneInfo(get_settings().timezone)).replace(hour=8)
    assert digest.run_digests(morning) == 1
    assert len(OUTBOX) == 1 and OUTBOX[0]["To"] == "viewer@test.com"
    body = OUTBOX[0].get_body(("plain",)).get_content()
    assert "Sales are 300 in UAE." in body and "300" in body
    assert "KSA" not in ai["model"].calls[0]["prompt"] and "700" not in ai["model"].calls[0]["prompt"]
    assert digest.run_digests(morning) == 0  # once a day
    assert digest.run_digests(morning.replace(hour=5)) == 0


def test_explain_a_dashboard_tile(ai):
    owner, dash = ai["owner"], ai["dash"]
    owner.put(f"{API}/dashboards/{dash['id']}/sharing", json={"everyone": "view", "users": [], "groups": []})
    owner.put(f"{API}/datasets/{ai['ds']['id']}/access", json={"everyone": False})  # the dashboard is enough
    ai["model"].replies = ["- KSA only."]
    body = {"dashboard_id": dash["id"], "item": "c1", "title": "By region"}
    r = ai["ex@test.com"].post(f"{API}/ai/explain-tile", json=body)
    assert r.status_code == 200, r.text
    assert "KSA | 300" in ai["model"].calls[0]["prompt"] and "UAE" not in ai["model"].calls[0]["prompt"]
    assert ai["viewer@test.com"].post(f"{API}/ai/explain-tile", json=body).status_code == 403
    owner.put(f"{API}/dashboards/{dash['id']}/sharing", json={"everyone": "none", "users": [], "groups": []})
    assert ai["ex@test.com"].post(f"{API}/ai/explain-tile", json=body).status_code == 404
