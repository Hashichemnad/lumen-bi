"""Excel exports and dashboard PDF/PNG exports (the browser step itself is covered by the browser tests)."""

import io

from openpyxl import load_workbook

from app.db import SessionLocal
from app.models import AuditLog, Role
from app.services import render
from tests.conftest import Client, make_user
from tests.test_model import upload_dataset

API = "/api/v1"
DATA = (
    ["region", "amount", "share", "day", "note"],
    [["UAE", 1200.5, 0.25, "2026-01-05", "=SUM(A1)"], ["KSA", 300.0, 0.75, "2026-02-01", "ok"]],
)


def test_query_export_as_excel_keeps_formats(creator_client):
    ds = upload_dataset(creator_client, "Sales", DATA)
    fields = {f["name"]: f for f in creator_client.get(f"{API}/datasets/{ds['id']}/fields").json()}
    creator_client.patch(
        f"{API}/datasets/{ds['id']}/fields/{fields['amount']['id']}",
        json={"format": {"type": "currency", "currency": "AED", "decimals": 0}},
    )
    creator_client.patch(
        f"{API}/datasets/{ds['id']}/fields/{fields['share']['id']}",
        json={"format": {"type": "percent", "decimals": 1}},
    )
    r = creator_client.post(
        f"{API}/query/export",
        json={
            "dataset": ds["id"],
            "mode": "rows",
            "fields": ["region", "amount", "share", "day", "note"],
            "sort": [{"key": "region"}],
            "format": "xlsx",
        },
    )
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert r.headers["content-disposition"].endswith('.xlsx"')
    ws = load_workbook(io.BytesIO(r.content)).active
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0] == ("Region", "Amount", "Share", "Day", "Note")
    assert rows[1][0] == "KSA" and rows[2][1] == 1200.5 and rows[2][4] == "'=SUM(A1)"
    assert ws["B2"].number_format == '"AED" #,##0' and ws["C2"].number_format == "0.0%"
    assert ws["D2"].is_date and ws["D2"].number_format == "yyyy-mm-dd"


def test_dashboard_render_endpoint(creator_client, monkeypatch):
    ds = upload_dataset(creator_client, "Sales", DATA)
    calls = []

    def fake(user_id, dashboard_id, fmt, state=None):
        calls.append((user_id, dashboard_id, fmt, state))
        return b"%PDF-1.7 fake" if fmt == "pdf" else b"\x89PNG fake"

    monkeypatch.setattr(render, "render_dashboard", fake)
    chart = creator_client.post(
        f"{API}/charts",
        json={
            "name": "C",
            "dataset_id": ds["id"],
            "config": {},
            "query": {"measures": [{"field": "amount", "key": "m"}]},
        },
    ).json()
    cfg = {
        "tabs": [
            {
                "id": "t",
                "name": "T",
                "items": [
                    {
                        "id": "c",
                        "kind": "chart",
                        "chart_id": chart["id"],
                        "desktop": {"x": 0, "y": 0, "w": 6, "h": 6},
                    }
                ],
            }
        ],
        "filters": [],
        "options": {},
    }
    d = creator_client.post(f"{API}/dashboards", json={"name": "Weekly", "config": cfg}).json()
    r = creator_client.post(f"{API}/dashboards/{d['id']}/render", json={"format": "pdf", "state": "abc"})
    assert (
        r.status_code == 200
        and r.content.startswith(b"%PDF")
        and r.headers["content-type"] == "application/pdf"
    )
    assert 'filename="Weekly ' in r.headers["content-disposition"]
    r = creator_client.post(f"{API}/dashboards/{d['id']}/render", json={"format": "png"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert calls[0][2:] == ("pdf", "abc")
    with SessionLocal() as db:
        assert db.query(AuditLog).filter_by(action="dashboard.export").count() == 2
    # Someone the dashboard is not shared with cannot export it; a viewer it is shared with can
    make_user("v@test.com", Role.viewer)
    with Client(creator_client.app, base_url="http://testserver") as v:
        v.login("v@test.com")
        assert v.post(f"{API}/dashboards/{d['id']}/render", json={"format": "pdf"}).status_code == 404
        creator_client.put(f"{API}/dashboards/{d['id']}/sharing", json={"everyone": "view"})
        assert v.post(f"{API}/dashboards/{d['id']}/render", json={"format": "pdf"}).status_code == 200
        assert v.post(f"{API}/dashboards/{d['id']}/render", json={"format": "docx"}).status_code == 422

    # A failing renderer gives a readable error
    def broken(*_a, **_k):
        raise render.RenderError("The dashboard could not be drawn.")

    monkeypatch.setattr(render, "render_dashboard", broken)
    r = creator_client.post(f"{API}/dashboards/{d['id']}/render", json={"format": "pdf"})
    assert r.status_code == 503 and "could not be drawn" in r.json()["detail"]
