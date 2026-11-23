# ruff: noqa: F811 - pytest fixtures imported from test_dashboards are used as parameters
"""Milestone 14 admin: branding, email settings, backups (create, download, restore), usage analytics, the
sync monitor, lineage and impact."""

import base64
import tarfile
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.config import get_settings
from app.db import SessionLocal
from app.models import AuditLog, Chart
from app.services import backup, org
from app.services.email import OUTBOX
from tests.test_dashboards import world  # noqa: F401 - fixture
from tests.test_model import add_calc, upload_dataset

API = "/api/v1"
PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 100).decode()


# ------------------------------------------------------------------ branding and email
def test_branding(world):
    admin, owner = world["admin@test.com"], world["owner"]
    assert owner.put(f"{API}/admin/settings/branding", json={"name": "X"}).status_code == 403
    body = {"name": "Dubai Analytics", "accent": "#0B6E4F", "logo": PNG, "login_message": "Staff only"}
    r = admin.put(f"{API}/admin/settings/branding", json=body)
    assert r.status_code == 200, r.text
    from tests.conftest import Client

    with Client(owner.app, base_url="http://testserver") as anon:
        b = anon.get(f"{API}/branding").json()
    assert b["name"] == "Dubai Analytics" and b["accent"] == "#0b6e4f" and b["logo"] == PNG
    assert admin.put(f"{API}/admin/settings/branding", json={**body, "accent": "#ffee88"}).status_code == 422
    big = "data:image/png;base64," + base64.b64encode(b"0" * 300_000).decode()
    assert admin.put(f"{API}/admin/settings/branding", json={**body, "logo": big}).status_code == 422
    svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg onload='x()'></svg>").decode()
    assert admin.put(f"{API}/admin/settings/branding", json={**body, "logo": svg}).status_code == 422
    assert (
        admin.put(f"{API}/admin/settings/branding", json={**body, "logo": "https://x/logo.png"}).status_code
        == 422
    )


def test_email_settings_hide_the_password_and_are_used(world, monkeypatch):
    admin = world["admin@test.com"]
    assert world["owner"].get(f"{API}/admin/settings/email").status_code == 403
    body = {
        "host": "smtp.company.ae",
        "port": 2525,
        "user": "lumen",
        "password": "s3cret",
        "sender": "BI <bi@company.ae>",
        "tls": False,
    }
    r = admin.put(f"{API}/admin/settings/email", json=body)
    assert r.status_code == 200 and r.json()["has_password"] and "s3cret" not in r.text
    assert r.json()["in_use"] == "settings"
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent.append(("tls",))

        def login(self, user, password):
            sent.append(("login", user, password))

        def send_message(self, msg):
            sent.append(("send", msg["From"], msg["To"]))

    import smtplib

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    r = admin.post(f"{API}/admin/settings/email/test")
    assert r.json()["ok"] is True
    assert sent == [
        ("connect", "smtp.company.ae", 2525),
        ("login", "lumen", "s3cret"),
        ("send", "BI <bi@company.ae>", "admin@test.com"),
    ]
    # Saving without a password keeps it
    admin.put(f"{API}/admin/settings/email", json={**body, "password": None})
    assert org.smtp()["password"] == "s3cret"
    with SessionLocal() as s:
        row = s.query(AuditLog).filter_by(action="settings.update", object_id="email").first()
        assert "s3cret" not in str(row.details)
    # Clearing the host goes back to the environment (none in tests: emails are kept in the outbox)
    admin.put(f"{API}/admin/settings/email", json={**body, "host": ""})
    OUTBOX.clear()
    assert admin.post(f"{API}/admin/settings/email/test").json()["ok"] and len(OUTBOX) == 1


# ------------------------------------------------------------------ backups
def test_backup_and_restore(world):
    admin, owner = world["admin@test.com"], world["owner"]
    assert owner.post(f"{API}/admin/backups").status_code == 403
    r = admin.post(f"{API}/admin/backups")
    assert r.status_code == 202 and r.json()["name"].startswith("lumen-")
    name = r.json()["name"]
    listed = admin.get(f"{API}/admin/backups").json()
    assert listed["backups"][0]["name"] == name and listed["keep"] == 7
    dl = admin.get(f"{API}/admin/backups/{name}")
    assert dl.status_code == 200 and dl.content[:2] == b"\x1f\x8b"
    assert admin.get(f"{API}/admin/backups/..%2Fsecrets").status_code == 404
    assert owner.get(f"{API}/admin/backups/{name}").status_code == 403
    with tarfile.open(backup.folder() / name) as tar:
        names = tar.getnames()
    assert "manifest.json" in names and "db/charts.jsonl.gz" in names
    assert any(n.startswith(f"data/datasets/{world['ds']['id']}/v1") for n in names)

    # Something changes after the backup ...
    chart_id = world["chart"]["id"]
    owner.post(
        f"{API}/charts", json={"name": "Later", "dataset_id": world["ds"]["id"], "config": {}, "query": {}}
    )
    with SessionLocal() as s:
        s.get(Chart, chart_id).name = "Renamed"
        s.commit()
    snap = get_settings().data_dir / "datasets" / str(world["ds"]["id"])
    import shutil

    shutil.rmtree(snap)
    # ... and the backup brings everything back
    out = backup.restore(backup.folder() / name)
    assert out["tables"]["charts"] == 1
    with SessionLocal() as s:
        assert [c.name for c in s.query(Chart)] == ["By region"]
    assert (snap / "v1").exists()
    q = {
        "dataset": world["ds"]["id"],
        "dimensions": [{"field": "region", "key": "d"}],
        "measures": [{"field": "amount", "key": "m"}],
    }
    from tests.conftest import Client

    with Client(admin.app, base_url="http://testserver") as c:
        c.login("admin@test.com")
        assert len(c.post(f"{API}/query", json=q).json()["rows"]) == 3
    assert admin.delete(f"{API}/admin/backups/{name}").status_code in (
        204,
        401,
    )  # restored sessions may differ


def test_nightly_backup_once_a_day_and_pruned(world):
    tz = ZoneInfo(get_settings().timezone)
    morning = datetime.now(tz).replace(hour=1, minute=0)
    if morning > datetime.now(tz):
        morning -= timedelta(days=1)
    assert backup.nightly(morning) is None  # before 02:00
    later = datetime.now(tz)
    if later.hour < 2:
        pytest.skip("runs after 02:00 local time")
    first = backup.nightly(later)
    assert first and backup.nightly(later) is None
    for _ in range(3):
        backup.create()
    assert backup.prune(2) == 2 and len(backup.listing()) == 2


# ------------------------------------------------------------------ usage and monitor
def test_usage_counts_views_but_not_rendered_reports(world):
    admin, owner, dash = world["admin@test.com"], world["owner"], world["dash"]
    owner.put(f"{API}/dashboards/{dash['id']}/sharing", json={"everyone": "view", "users": [], "groups": []})
    world["viewer@test.com"].get(f"{API}/dashboards/{dash['id']}")
    owner.get(f"{API}/dashboards/{dash['id']}")
    owner.get(f"{API}/dashboards/{dash['id']}", headers={"X-Lumen-Render": "1"})
    owner.get(f"{API}/charts/{world['chart']['id']}")
    assert owner.get(f"{API}/admin/usage").status_code == 403
    u = admin.get(f"{API}/admin/usage?days=7").json()
    assert u["totals"]["dashboard_views"] == 2 and u["totals"]["chart_views"] == 1
    assert u["top_dashboards"][0] == {**u["top_dashboards"][0], "name": "Sales", "views": 2, "viewers": 2}
    assert u["view_sources"] == {"app": 2, "render": 1}
    assert u["totals"]["active_users"] >= 2 and len(u["daily"]) == 7
    assert u["daily"][-1]["dashboard_views"] == 2


def test_sync_monitor(world):
    owner = world["owner"]
    m = owner.get(f"{API}/admin/sync-monitor").json()
    row = next(d for d in m["datasets"] if d["id"] == world["ds"]["id"])
    assert row["last_status"] == "success" and row["runs_7d"] >= 1 and row["failed_7d"] == 0
    assert m["health"]["ok"] >= 1
    assert world["viewer@test.com"].get(f"{API}/admin/sync-monitor").status_code == 403


def test_lineage_and_impact(world):
    owner, ds = world["owner"], world["ds"]
    other = upload_dataset(
        owner, "More sales", (["region", "amount", "order_date"], [["OMN", 5.0, "2026-04-01"]]), "more.csv"
    )
    u = owner.post(
        f"{API}/datasets",
        json={"name": "All sales", "source": {"union": {"datasets": [ds["id"], other["id"]]}}},
    )
    assert u.status_code == 201, u.text
    assert add_calc(owner, ds["id"], "Double", "[amount] * 2").status_code == 201
    lin = owner.get(f"{API}/lineage/datasets/{u.json()['id']}").json()
    assert {p["name"] for p in lin["upstream"]} == {"Sales", "More sales"}
    assert lin["upstream"][0]["connection"]["type"] == "file"
    down = owner.get(f"{API}/lineage/datasets/{ds['id']}").json()["downstream"]
    assert [d["name"] for d in down["datasets"]] == ["All sales"]
    assert down["charts"][0]["dashboards"] == ["Sales"] and down["dashboards"][0]["name"] == "Sales"
    amount = owner.get(f"{API}/lineage/datasets/{ds['id']}/impact?field=amount").json()
    assert [c["name"] for c in amount["charts"]] == ["By region"] and amount["calculated_fields"] == [
        "Double"
    ]
    branch = owner.get(f"{API}/lineage/datasets/{ds['id']}/impact?field=branch").json()
    assert branch["charts"] == [] and branch["dashboards"] == []
    assert world["ex@test.com"].get(f"{API}/lineage/datasets/{ds['id']}").status_code == 403
