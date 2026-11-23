# ruff: noqa: F811 - pytest fixtures imported from test_dashboards are used as parameters
"""Subscriptions (each recipient with their own data), alerts (each recipient checked with their own data,
once per change), Teams and WhatsApp channels (secrets never returned), embed keys and display links
(read-only, one dashboard, revocable)."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.db import SessionLocal
from app.models import AuditLog, NotificationLog, Subscription, User
from app.services import delivery, render
from app.services.email import OUTBOX
from tests.conftest import Client
from tests.test_dashboards import config, world  # noqa: F401 - fixture
from tests.test_governance import _kpi_config

API = "/api/v1"


@pytest.fixture
def shared(world, monkeypatch):
    owner, dash = world["owner"], world["dash"]
    owner.put(f"{API}/dashboards/{dash['id']}/sharing", json={"everyone": "view", "users": [], "groups": []})
    rendered = []

    def fake_render(user_id, dashboard_id, fmt, state=None):
        rendered.append(user_id)
        return b"%PDF-fake"

    monkeypatch.setattr(render, "render_dashboard", fake_render)
    OUTBOX.clear()
    with SessionLocal() as s:
        ids = {u.email: u.id for u in s.query(User)}
    return {**world, "ids": ids, "rendered": rendered}


class FakeHttp:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers or {}})

        class R:
            def raise_for_status(self):
                return None

        return R()


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    import app.services.notify as notify

    monkeypatch.setattr(notify.httpx, "post", fake.post)
    return fake


def _channels(admin, teams=True, whatsapp=True):
    body = {
        "teams": [{"name": "Ops", "url": "https://example.webhook.office.com/abc"}] if teams else [],
        "whatsapp": {
            "enabled": whatsapp,
            "phone_number_id": "123",
            "token": "secret-token",
            "template": "lumen_alert",
        },
    }
    r = admin.put(f"{API}/admin/settings/notifications", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ notification settings
def test_notification_settings_are_admin_only_and_hide_secrets(shared):
    admin = shared["admin@test.com"]
    for email in ("viewer@test.com", "ex@test.com"):
        assert shared[email].get(f"{API}/admin/settings/notifications").status_code == 403
        assert shared[email].put(f"{API}/admin/settings/notifications", json={}).status_code == 403
    out = _channels(admin)
    text = str(out) + admin.get(f"{API}/admin/settings/notifications").text
    assert "secret-token" not in text and "webhook.office.com" not in text
    assert out["teams"][0]["has_url"] and out["whatsapp"]["has_token"]
    # Saving again without the secrets keeps them
    keep = {
        "teams": [{"id": out["teams"][0]["id"], "name": "Ops"}],
        "whatsapp": {**out["whatsapp"], "token": ""},
    }
    again = admin.put(f"{API}/admin/settings/notifications", json=keep).json()
    assert again["teams"][0]["has_url"] and again["whatsapp"]["has_token"]
    bad = {"teams": [{"name": "X", "url": "http://insecure"}], "whatsapp": {}}
    assert admin.put(f"{API}/admin/settings/notifications", json=bad).status_code == 422


def test_test_message(shared, http):
    admin = shared["admin@test.com"]
    out = _channels(admin)
    r = admin.post(
        f"{API}/admin/settings/notifications/test", json={"channel": f"teams:{out['teams'][0]['id']}"}
    )
    assert r.json()["sent"] == 1
    assert http.calls[0]["url"] == "https://example.webhook.office.com/abc"
    assert (
        admin.post(f"{API}/admin/settings/notifications/test", json={"channel": "email"}).json()["sent"] == 1
    )


# ------------------------------------------------------------------ subscriptions
def test_subscription_each_recipient_gets_their_own(shared):
    owner, dash, ids = shared["owner"], shared["dash"], shared["ids"]
    body = {
        "recipients": {"users": [ids["viewer@test.com"], ids["ex@test.com"], ids["nobody@test.com"]]},
        "channels": ["email"],
        "format": "pdf",
        "schedule": "0 7 * * 1",
    }
    r = owner.post(f"{API}/dashboards/{dash['id']}/subscriptions", json=body)
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["next_run_at"]
    sent = owner.post(f"{API}/subscriptions/{sub['id']}/send").json()
    assert sent["sent"] == 3
    # Rendered once per person, as that person (their own row-level security)
    assert sorted(shared["rendered"]) == sorted(
        [ids["viewer@test.com"], ids["ex@test.com"], ids["nobody@test.com"]]
    )
    to = sorted(m["To"] for m in OUTBOX)
    assert to == ["ex@test.com", "nobody@test.com", "viewer@test.com"]
    assert all(any(p.get_filename() == "Sales.pdf" for p in m.iter_attachments()) for m in OUTBOX)
    # Someone who can no longer open the dashboard is skipped
    owner.put(
        f"{API}/dashboards/{dash['id']}/sharing",
        json={"everyone": "none", "users": [{"id": ids["viewer@test.com"], "level": "view"}], "groups": []},
    )
    OUTBOX.clear()
    owner.post(f"{API}/subscriptions/{sub['id']}/send")
    assert [m["To"] for m in OUTBOX] == ["viewer@test.com"]


def test_subscription_permissions(shared):
    owner, dash, ids = shared["owner"], shared["dash"], shared["ids"]
    viewer = shared["viewer@test.com"]
    # Viewers subscribe themselves only
    mine = viewer.post(f"{API}/dashboards/{dash['id']}/subscriptions", json={"format": "link"})
    assert mine.status_code == 201 and mine.json()["recipients"] == {
        "users": [ids["viewer@test.com"]],
        "groups": [],
    }
    others = {"recipients": {"users": [ids["ex@test.com"]]}}
    assert viewer.post(f"{API}/dashboards/{dash['id']}/subscriptions", json=others).status_code == 403
    _channels(shared["admin@test.com"])
    teams = owner.get(f"{API}/dashboards/{dash['id']}/channels").json()
    team_id = next(c["id"] for c in teams if c["id"].startswith("teams:"))
    assert all(
        not c["id"].startswith("teams:") for c in viewer.get(f"{API}/dashboards/{dash['id']}/channels").json()
    )
    assert (
        viewer.post(f"{API}/dashboards/{dash['id']}/subscriptions", json={"channels": [team_id]}).status_code
        == 403
    )
    assert (
        viewer.post(f"{API}/dashboards/{dash['id']}/subscriptions", json={"schedule": "nope"}).status_code
        == 422
    )
    # Others' subscriptions are invisible; managers see all
    sid = mine.json()["id"]
    assert shared["ex@test.com"].put(f"{API}/subscriptions/{sid}", json={}).status_code == 404
    assert shared["ex@test.com"].delete(f"{API}/subscriptions/{sid}").status_code == 404
    assert [s["id"] for s in owner.get(f"{API}/dashboards/{dash['id']}/subscriptions").json()] == [sid]
    assert shared["nobody@test.com"].get(f"{API}/dashboards/{dash['id']}/subscriptions").json() == []
    assert viewer.delete(f"{API}/subscriptions/{sid}").status_code == 204


def test_due_subscriptions_run_on_schedule(shared):
    owner, dash = shared["owner"], shared["dash"]
    sub = owner.post(f"{API}/dashboards/{dash['id']}/subscriptions", json={"format": "link"}).json()
    with SessionLocal() as s:
        s.get(Subscription, sub["id"]).next_run_at = datetime.now(UTC) - timedelta(minutes=1)
        s.commit()
    assert delivery.dispatch_due()["subscriptions"] == 1
    assert delivery.dispatch_due()["subscriptions"] == 0  # moved on to the next time
    assert [m["To"] for m in OUTBOX] == ["creator@test.com"]


# ------------------------------------------------------------------ alerts
def test_value_alert_per_recipient_and_once_per_change(shared, http):
    owner, dash, ids = shared["owner"], shared["dash"], shared["ids"]
    _channels(shared["admin@test.com"])
    with SessionLocal() as s:
        u = s.get(User, ids["viewer@test.com"])
        u.attributes = {**u.attributes, "phone": "+971 50 123 4567"}
        s.commit()
    body = {
        "name": "Sales above 250",
        "tile_id": "c1",
        "condition": {"kind": "value", "op": "gt", "value": 250},
        "recipients": {"users": [ids["viewer@test.com"], ids["ex@test.com"]]},
        "channels": ["email", "whatsapp"],
    }
    a = owner.post(f"{API}/dashboards/{dash['id']}/alerts", json=body)
    assert a.status_code == 201, a.text
    aid = a.json()["id"]
    r = owner.post(f"{API}/alerts/{aid}/check").json()
    # The viewer (UAE: 300) and the explorer (KSA: 300) each pass 250 with their own rows
    assert sorted(m["To"] for m in OUTBOX) == ["ex@test.com", "viewer@test.com"]
    wa = [c for c in http.calls if "graph.facebook.com" in c["url"]]
    assert len(wa) == 1 and wa[0]["json"]["to"] == "971501234567"
    assert wa[0]["headers"]["Authorization"] == "Bearer secret-token"
    assert r["sent"] == 3
    # Checking again sends nothing (still true)
    OUTBOX.clear()
    owner.post(f"{API}/alerts/{aid}/check")
    assert OUTBOX == []
    # With a higher threshold it becomes false; lowering it again sends again
    owner.put(f"{API}/alerts/{aid}", json={**body, "condition": {"kind": "value", "op": "gt", "value": 1000}})
    owner.post(f"{API}/alerts/{aid}/check")
    assert OUTBOX == []
    owner.put(f"{API}/alerts/{aid}", json=body)
    owner.post(f"{API}/alerts/{aid}/check")
    assert len(OUTBOX) == 2
    with SessionLocal() as s:
        assert (
            s.query(NotificationLog).filter_by(source="alert", channel="whatsapp", status="skipped").count()
            >= 1
        )


def test_alert_values_follow_each_persons_rows(shared):
    owner, dash, ids = shared["owner"], shared["dash"], shared["ids"]
    # 250 < UAE's 300, but the threshold 350 is only passed by... nobody with their own rows
    body = {
        "name": "Big",
        "tile_id": "c1",
        "condition": {"kind": "value", "op": "gt", "value": 350},
        "recipients": {"users": [ids["viewer@test.com"], ids["ex@test.com"]]},
    }
    aid = owner.post(f"{API}/dashboards/{dash['id']}/alerts", json=body).json()["id"]
    owner.post(f"{API}/alerts/{aid}/check")
    assert OUTBOX == []  # even though all regions together are 1000


def test_kpi_alert(shared):
    owner, dash, ids = shared["owner"], shared["dash"], shared["ids"]
    owner.put(
        f"{API}/dashboards/{dash['id']}",
        json={"name": "Sales", "config": _kpi_config(shared["chart"]["id"], 500)},
    )
    body = {
        "name": "KPI off track",
        "tile_id": "sec1",
        "condition": {"kind": "kpi", "status": "off_track"},
        "recipients": {"users": [ids["viewer@test.com"]]},
    }
    aid = owner.post(f"{API}/dashboards/{dash['id']}/alerts", json=body).json()["id"]
    owner.post(f"{API}/alerts/{aid}/check")
    assert [m["To"] for m in OUTBOX] == ["viewer@test.com"]
    assert "off track" in OUTBOX[0].get_body().get_content()
    assert (
        owner.post(f"{API}/dashboards/{dash['id']}/alerts", json={**body, "tile_id": "c1"}).status_code == 422
    )


def test_alert_permissions_and_teams_as_owner(shared, http):
    dash, ids = shared["dash"], shared["ids"]
    out = _channels(shared["admin@test.com"])
    team = f"teams:{out['teams'][0]['id']}"
    viewer = shared["viewer@test.com"]
    base = {"name": "x", "tile_id": "c1", "condition": {"kind": "value", "op": "gt", "value": 0}}
    assert (
        viewer.post(f"{API}/dashboards/{dash['id']}/alerts", json={**base, "channels": [team]}).status_code
        == 403
    )
    assert (
        viewer.post(
            f"{API}/dashboards/{dash['id']}/alerts",
            json={**base, "recipients": {"users": [ids["ex@test.com"]]}},
        ).status_code
        == 403
    )
    assert (
        viewer.post(f"{API}/dashboards/{dash['id']}/alerts", json={**base, "tile_id": "x1"}).status_code
        == 422
    )
    mine = viewer.post(f"{API}/dashboards/{dash['id']}/alerts", json=base).json()
    assert shared["ex@test.com"].post(f"{API}/alerts/{mine['id']}/check").status_code == 404
    # Teams messages are checked as the owner (an admin here sees all rows)
    admin = shared["admin@test.com"]
    aid = admin.post(
        f"{API}/dashboards/{dash['id']}/alerts",
        json={**base, "channels": [team], "recipients": {"users": []}},
    ).json()["id"]
    admin.post(f"{API}/alerts/{aid}/check")
    teams = [c for c in http.calls if "office.com" in c["url"]]
    assert len(teams) == 1 and "1,000" in str(teams[0]["json"])
    with SessionLocal() as s:
        assert s.query(AuditLog).filter_by(action="alert.create").count() == 2


# ------------------------------------------------------------------ embedding
def test_display_link(shared):
    admin, dash, ids = shared["admin@test.com"], shared["dash"], shared["ids"]
    assert (
        shared["owner"]
        .post(
            f"{API}/dashboards/{dash['id']}/display-links",
            json={"name": "TV", "run_as_id": ids["viewer@test.com"]},
        )
        .status_code
        == 403
    )
    assert (
        admin.post(
            f"{API}/dashboards/{dash['id']}/display-links",
            json={"name": "TV", "run_as_id": ids["admin@test.com"]},
        ).status_code
        == 422
    )
    link = admin.post(
        f"{API}/dashboards/{dash['id']}/display-links",
        json={"name": "TV", "run_as_id": ids["viewer@test.com"]},
    ).json()
    token = link["url"].split("#", 1)[1]
    assert "url" not in admin.get(f"{API}/dashboards/{dash['id']}/display-links").json()[0]
    anon = Client(shared["owner"].app, base_url="http://testserver")
    session = anon.post(f"{API}/embed/session", json={"display": token})
    assert session.status_code == 200, session.text
    h = {"X-Lumen-Embed": session.json()["token"]}
    got = anon.get(f"{API}/dashboards/{dash['id']}", headers=h)
    assert got.status_code == 200 and got.json()["embedded"] and not got.json()["can_edit"]
    rows = anon.post(f"{API}/dashboards/{dash['id']}/query", json={"item": "c1"}, headers=h).json()["rows"]
    assert [r[0] for r in rows] == ["UAE"]  # the run-as user's rows
    # Nothing else: other dashboards, other endpoints, writes
    other = shared["owner"].post(
        f"{API}/dashboards", json={"name": "Other", "config": config(shared["chart"]["id"])}
    )
    assert anon.get(f"{API}/dashboards/{other.json()['id']}", headers=h).status_code == 401
    assert anon.get(f"{API}/dashboards", headers=h).status_code == 401
    assert anon.get(f"{API}/auth/me", headers=h).status_code == 401
    assert (
        anon.post(
            f"{API}/query", json={"dataset": shared["ds"]["id"], "measures": ["amount"]}, headers=h
        ).status_code
        == 401
    )
    assert (
        anon.put(f"{API}/dashboards/{dash['id']}", json={"name": "x", "config": {}}, headers=h).status_code
        == 401
    )
    # Revoking ends it at once
    assert admin.delete(f"{API}/display-links/{link['id']}").status_code == 204
    assert anon.get(f"{API}/dashboards/{dash['id']}", headers=h).status_code == 401
    assert anon.post(f"{API}/embed/session", json={"display": token}).status_code == 401
    assert anon.post(f"{API}/embed/session", json={"display": "made-up"}).status_code == 401


def test_embed_key(shared):
    admin, dash = shared["admin@test.com"], shared["dash"]
    assert shared["owner"].post(f"{API}/admin/embed-keys", json={"name": "Portal"}).status_code == 403
    key = admin.post(f"{API}/admin/embed-keys", json={"name": "Portal", "dashboards": [dash["id"]]}).json()
    assert key["secret"] and "secret" not in admin.get(f"{API}/admin/embed-keys").json()[0]

    def sign(claims, secret=key["secret"], kid=key["id"]):
        return jwt.encode(claims, secret, algorithm="HS256", headers={"kid": str(kid)})

    soon = datetime.now(UTC) + timedelta(minutes=10)
    anon = Client(shared["owner"].app, base_url="http://testserver")
    ok = anon.post(
        f"{API}/embed/session",
        json={"token": sign({"dashboard": dash["id"], "user": "ex@test.com", "exp": soon})},
    )
    assert ok.status_code == 200, ok.text
    rows = anon.post(
        f"{API}/dashboards/{dash['id']}/query",
        json={"item": "c1"},
        headers={"X-Lumen-Embed": ok.json()["token"]},
    ).json()["rows"]
    assert [r[0] for r in rows] == ["KSA"]
    refused = [
        sign(
            {"dashboard": dash["id"], "user": "ex@test.com", "exp": soon},
            secret="wrong-secret-that-is-long-enough-0123456789",
        ),
        sign({"dashboard": dash["id"], "user": "ex@test.com"}),  # no expiry
        sign({"dashboard": dash["id"], "user": "ex@test.com", "exp": soon + timedelta(days=3)}),
        sign({"dashboard": 999, "user": "ex@test.com", "exp": soon}),
        sign({"dashboard": dash["id"], "user": "nobody-here@test.com", "exp": soon}),
        sign({"dashboard": dash["id"], "user": "ex@test.com", "exp": soon}, kid=999),
        "not-a-token",
    ]
    for t in refused:
        assert anon.post(f"{API}/embed/session", json={"token": t}).status_code == 401
    # A user who cannot open the dashboard is refused too
    shared["owner"].put(
        f"{API}/dashboards/{dash['id']}/sharing", json={"everyone": "none", "users": [], "groups": []}
    )
    t = sign({"dashboard": dash["id"], "user": "ex@test.com", "exp": soon})
    assert anon.post(f"{API}/embed/session", json={"token": t}).status_code == 401
    # Turning the key off ends its sessions
    admin.put(
        f"{API}/admin/embed-keys/{key['id']}",
        json={"name": "Portal", "dashboards": [dash["id"]], "enabled": False},
    )
    assert (
        anon.get(f"{API}/dashboards/{dash['id']}", headers={"X-Lumen-Embed": ok.json()["token"]}).status_code
        == 401
    )
    with SessionLocal() as s:
        assert s.query(AuditLog).filter_by(action="embed.refused").count() >= len(refused)
