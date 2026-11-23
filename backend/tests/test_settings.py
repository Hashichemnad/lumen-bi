"""Organisation settings (admins), personal preferences, and the calendar settings the query engine uses:
fiscal year levels, the first day of the week and relative fiscal periods."""

from datetime import date, timedelta

from app.db import SessionLocal
from app.models import AuditLog, Role
from tests.conftest import make_user
from tests.test_dashboards import login
from tests.test_model import upload_dataset

API = "/api/v1"
DATES = (
    ["order_date", "amount"],
    [
        ["2026-01-07", 10.0],  # a Wednesday
        ["2026-06-30", 20.0],
        ["2026-07-01", 30.0],
        ["2026-08-15", 40.0],
    ],
)


def regional(client, **changes):
    current = client.get(f"{API}/settings").json()["regional"]
    return client.put(f"{API}/admin/settings/regional", json={**current, **changes})


def test_defaults_for_everyone(client):
    make_user("viewer@test.com", Role.viewer)
    client.login("viewer@test.com")
    r = client.get(f"{API}/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["regional"] == {
        "default_theme": "classic",
        "default_language": "en",
        "calendar": "gregorian",
        "week_start": 1,
        "weekend": [6, 7],
        "fiscal_year_start": 1,
    }
    assert body["effective"] == {"theme": "classic", "language": "en", "calendar": "gregorian"}


def test_settings_need_sign_in(client):
    assert client.get(f"{API}/settings").status_code == 401
    assert client.put(f"{API}/me/preferences", json={"theme": "advanced"}).status_code == 401


def test_only_admins_change_regional_settings(client):
    make_user("admin@test.com", Role.admin)
    for role in (Role.viewer, Role.explorer, Role.creator):
        make_user(f"{role.value}@test.com", role)
        c = login(client.app, f"{role.value}@test.com")
        assert regional(c, default_theme="advanced").status_code == 403
    client.login("admin@test.com")
    r = regional(client, default_theme="analyst", default_language="ar", fiscal_year_start=7, calendar="both")
    assert r.status_code == 200, r.text
    assert client.get(f"{API}/settings").json()["effective"] == {
        "theme": "analyst",
        "language": "ar",
        "calendar": "both",
    }
    with SessionLocal() as s:
        entry = s.query(AuditLog).filter_by(action="settings.update").one()
        assert entry.details["changed"]["fiscal_year_start"] == 7


def test_regional_settings_are_validated(client):
    make_user("admin@test.com", Role.admin)
    client.login("admin@test.com")
    assert regional(client, fiscal_year_start=13).status_code == 422
    assert regional(client, week_start=0).status_code == 422
    assert regional(client, weekend=[8]).status_code == 422
    assert regional(client, weekend=[1, 2, 3, 4]).status_code == 422
    assert regional(client, default_theme="neon").status_code == 422
    assert regional(client, weekend=[7, 6, 6]).json()["weekend"] == [6, 7]


def test_personal_preferences_override_defaults(client):
    make_user("admin@test.com", Role.admin)
    make_user("viewer@test.com", Role.viewer)
    admin = login(client.app, "admin@test.com")
    regional(admin, default_theme="analyst")
    client.login("viewer@test.com")
    r = client.put(f"{API}/me/preferences", json={"theme": "advanced", "language": "ar"})
    assert r.status_code == 200
    assert r.json()["preferences"] == {"theme": "advanced", "language": "ar"}
    assert client.get(f"{API}/auth/me").json()["preferences"] == {"theme": "advanced", "language": "ar"}
    assert client.get(f"{API}/settings").json()["effective"]["theme"] == "advanced"
    # Clearing a choice goes back to the organisation's default
    client.put(f"{API}/me/preferences", json={"language": "ar"})
    assert client.get(f"{API}/settings").json()["effective"]["theme"] == "analyst"
    assert client.put(f"{API}/me/preferences", json={"theme": "neon"}).status_code == 422
    assert client.put(f"{API}/me/preferences", json={"language": "fr"}).status_code == 422
    # Someone else's preferences are untouched
    assert admin.get(f"{API}/settings").json()["effective"]["theme"] == "analyst"


def _ds(creator_client):
    return upload_dataset(creator_client, "Orders", DATES)


def _rows(client, ds_id, **spec):
    r = client.post(f"{API}/query", json={"dataset": ds_id, **spec})
    assert r.status_code == 200, r.text
    return r.json()["rows"]


def test_fiscal_year_levels(creator_client):
    make_user("admin@test.com", Role.admin)
    admin = login(creator_client.app, "admin@test.com")
    ds = _ds(creator_client)
    dims = [{"field": "order_date", "grain": "fiscal_year"}]
    # Calendar year by default
    assert _rows(creator_client, ds["id"], dimensions=dims, measures=["amount"]) == [["2026-01-01", 100.0]]
    regional(admin, fiscal_year_start=7)
    assert _rows(creator_client, ds["id"], dimensions=dims, measures=["amount"]) == [
        ["2025-07-01", 30.0],
        ["2026-07-01", 70.0],
    ]
    # Fiscal year numbers are named by the year they end in
    parts = _rows(
        creator_client,
        ds["id"],
        dimensions=[{"field": "order_date", "part": "fiscal_year"}],
        measures=["amount"],
    )
    assert parts == [[2026, 30.0], [2027, 70.0]]
    quarters = _rows(
        creator_client,
        ds["id"],
        dimensions=[{"field": "order_date", "grain": "fiscal_quarter"}],
        measures=["amount"],
    )
    assert quarters == [["2026-01-01", 10.0], ["2026-04-01", 20.0], ["2026-07-01", 70.0]]
    q_parts = _rows(
        creator_client,
        ds["id"],
        dimensions=[{"field": "order_date", "part": "fiscal_quarter"}],
        measures=["amount"],
    )
    assert q_parts == [[1, 70.0], [3, 10.0], [4, 20.0]]
    out = creator_client.post(
        f"{API}/query", json={"dataset": ds["id"], "dimensions": dims, "measures": ["amount"]}
    ).json()
    assert "Fiscal year" in out["columns"][0]["label"]


def test_week_start(creator_client):
    make_user("admin@test.com", Role.admin)
    admin = login(creator_client.app, "admin@test.com")
    ds = _ds(creator_client)
    spec = {
        "dimensions": [{"field": "order_date", "grain": "week"}],
        "measures": ["amount"],
        "filters": [{"field": "order_date", "op": "lt", "value": "2026-02-01"}],
    }
    assert _rows(creator_client, ds["id"], **spec) == [["2026-01-05", 10.0]]  # Monday
    regional(admin, week_start=7)
    assert _rows(creator_client, ds["id"], **spec) == [["2026-01-04", 10.0]]  # Sunday
    regional(admin, week_start=6)
    assert _rows(creator_client, ds["id"], **spec) == [["2026-01-03", 10.0]]  # Saturday


def test_relative_fiscal_periods(creator_client):
    make_user("admin@test.com", Role.admin)
    admin = login(creator_client.app, "admin@test.com")
    today = date.today()
    data = (
        ["order_date", "amount"],
        [[today.isoformat(), 1.0], [(today - timedelta(days=400)).isoformat(), 2.0]],
    )
    ds = upload_dataset(creator_client, "Recent", data)
    regional(admin, fiscal_year_start=today.month)  # this fiscal year started on the 1st of this month
    for unit in ("fiscal_year", "fiscal_quarter"):
        rows = _rows(
            creator_client,
            ds["id"],
            measures=["amount"],
            filters=[{"field": "order_date", "op": "relative", "unit": unit, "n": 1}],
        )
        assert rows == [[1.0]], unit
    rows = _rows(
        creator_client,
        ds["id"],
        measures=["amount"],
        filters=[{"field": "order_date", "op": "relative", "unit": "fiscal_year", "n": 3}],
    )
    assert rows == [[3.0]]
