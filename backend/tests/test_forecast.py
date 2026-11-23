"""Forecasts in query results (Holt / Holt-Winters with intervals) and anomaly alerts (median and MAD)."""

import math
from datetime import date, timedelta

from app.db import SessionLocal
from app.models import Alert
from app.query import forecast
from app.services import anomaly, delivery
from tests.test_model import upload_dataset

API = "/api/v1"


def _monthly():
    rows = []
    for i in range(36):
        y, m = 2022 + i // 12, i % 12 + 1
        value = 1000 + 20 * i + 200 * math.sin(2 * math.pi * (m - 1) / 12)
        rows.append([f"{y}-{m:02d}-15", "A", round(value, 2)])
        rows.append([f"{y}-{m:02d}-15", "B", round(value / 2, 2)])
    return (["day", "team", "sales"], rows)


def test_predict_linear_trend():
    method, out = forecast.predict([10, 20, 30, 40, 50, 60], 3, None, 1.96)
    assert method == "holt"
    assert [round(p[0]) for p in out] == [70, 80, 90]
    assert all(lo <= fc <= hi for fc, lo, hi in out)
    assert forecast.predict([1, 2, 3], 3, None, 1.96) == ("none", [])


def test_query_with_forecast_per_series(creator_client):
    ds = upload_dataset(creator_client, "Monthly", _monthly())
    q = {
        "dataset": ds["id"],
        "dimensions": [{"field": "day", "grain": "month", "key": "d"}, {"field": "team", "key": "t"}],
        "measures": [{"field": "sales", "key": "m"}],
        "sort": [{"key": "d", "dir": "asc"}],
        "forecast": {"periods": 6, "interval": 0.9},
    }
    r = creator_client.post(f"{API}/query", json=q)
    assert r.status_code == 200, r.text
    f = r.json()["forecast"]
    assert f["method"] == "holt_winters" and f["season"] == 12 and f["interval"] == 0.9
    assert len(f["rows"]) == 12  # 6 months for each team
    a = [row for row in f["rows"] if row[1] == "A"]
    assert [row[0][:7] for row in a] == ["2025-01", "2025-02", "2025-03", "2025-04", "2025-05", "2025-06"]
    # Seasonal and trending: next January is close to the pattern (1000 + 20*36)
    assert abs(a[0][2] - 1720) < 120
    lows = [row for row in f["lower"] if row[1] == "A"]
    highs = [row for row in f["upper"] if row[1] == "A"]
    assert all(lo[2] < row[2] < hi[2] for lo, row, hi in zip(lows, a, highs, strict=True))
    # Intervals widen further out
    assert (highs[-1][2] - lows[-1][2]) > (highs[0][2] - lows[0][2])


def test_forecast_needs_a_date_level(creator_client):
    ds = upload_dataset(creator_client, "Monthly", _monthly())
    q = {
        "dataset": ds["id"],
        "dimensions": [{"field": "team"}],
        "measures": [{"field": "sales"}],
        "forecast": {},
    }
    f = creator_client.post(f"{API}/query", json=q).json()["forecast"]
    assert "date" in f["error"]
    q["forecast"] = {"periods": 500}
    assert creator_client.post(f"{API}/query", json=q).status_code == 422


def test_forecast_leaves_out_the_current_period():
    cols = [
        {"key": "d", "role": "dimension", "grain": "month"},
        {"key": "m", "role": "measure", "agg": "sum"},
    ]
    rows = [[f"2026-{m:02d}-01", 100.0 * m] for m in range(1, 12)] + [
        ["2026-11-01", 5.0]
    ]  # November not over
    from datetime import datetime

    f = forecast.compute(cols, rows, 2, 0.95, "none", now=datetime(2026, 11, 20))
    assert [r[0] for r in f["rows"]] == ["2026-11-01", "2026-12-01"]
    assert abs(f["rows"][0][1] - 1100) < 50  # not dragged down by the unfinished month


# ------------------------------------------------------------------ anomalies
def test_anomaly_score():
    history = [100, 102, 98, 101, 99, 100, 103, 97, 100, 101]
    assert anomaly.score(history, 101) < 2
    assert anomaly.score(history, 160) > 10
    assert anomaly.score(history, 40) < -10
    assert anomaly.score(history[:5], 160) is None
    assert anomaly.score([5] * 10, 5) == 0.0 and anomaly.score([5] * 10, 6) > 3


def _daily(spike: float):
    start = date.today() - timedelta(days=25)
    rows = [[(start + timedelta(days=i)).isoformat(), 100 + (i % 3)] for i in range(20)]
    rows.append([(start + timedelta(days=20)).isoformat(), spike])
    return (["day", "visits"], rows)


def _alert_on(client, spike: float, **cond):
    ds = upload_dataset(client, "Visits", _daily(spike))
    chart = client.post(
        f"{API}/charts",
        json={
            "name": "Visits",
            "dataset_id": ds["id"],
            "config": {"type": "line"},
            "query": {
                "dimensions": [{"field": "day", "grain": "day", "key": "d"}],
                "measures": [{"field": "visits", "key": "m"}],
            },
        },
    ).json()
    cfg = {
        "tabs": [
            {
                "id": "t1",
                "name": "A",
                "items": [
                    {
                        "id": "c1",
                        "kind": "chart",
                        "chart_id": chart["id"],
                        "desktop": {"x": 0, "y": 0, "w": 6, "h": 6},
                    }
                ],
            }
        ]
    }
    dash = client.post(f"{API}/dashboards", json={"name": "Web", "config": cfg}).json()
    r = client.post(
        f"{API}/dashboards/{dash['id']}/alerts",
        json={
            "name": "Odd visits",
            "tile_id": "c1",
            "condition": {"kind": "anomaly", **cond},
            "channels": ["email"],
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_anomaly_alert_fires_on_a_spike(creator_client):
    a = _alert_on(creator_client, 400)
    assert a["condition"] == {"kind": "anomaly", "sensitivity": "medium", "direction": "both"}
    assert a["description"] == "the latest value is unusual"
    with SessionLocal() as s:
        alert = s.get(Alert, a["id"])
        delivery.run_alert(s, alert)
        assert alert.triggered_for and "400" in alert.last_value and "usual about 101" in alert.last_value


def test_anomaly_alert_respects_direction(creator_client):
    a = _alert_on(creator_client, 400, direction="down")
    with SessionLocal() as s:
        alert = s.get(Alert, a["id"])
        delivery.run_alert(s, alert)
        assert alert.triggered_for == [] and alert.last_error == ""
