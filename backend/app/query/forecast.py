"""Forecasts for time series in query results (no AI: exponential smoothing, like Tableau's forecast).

For each series (the other dimensions of the row) and each plain measure:
- at least 2 full seasons -> additive Holt-Winters (trend and season); season length from the date level
  (12 months, 4 quarters, 7 days, 52 weeks, 24 hours);
- at least 4 points -> Holt's linear trend;
- fewer -> no forecast for that series.
Smoothing constants are chosen by a small grid search on one-step-ahead errors. The prediction interval
widens with the horizon (normal approximation of the in-sample errors).
"""

import calendar
import math
from datetime import datetime, timedelta
from itertools import product
from statistics import NormalDist
from typing import Any

SEASONS = {"month": 12, "quarter": 4, "fiscal_quarter": 4, "week": 52, "day": 7, "hour": 24}
MONTHS = {"month": 1, "quarter": 3, "fiscal_quarter": 3, "year": 12, "fiscal_year": 12}
GRID = (0.1, 0.3, 0.5, 0.7, 0.9)
TREND_GRID = (0.05, 0.1, 0.3, 0.5)
MAX_SERIES = 12
MAX_POINTS = 2000
TRAIN_POINTS = 400  # the most recent periods used to fit
ADDITIVE = {"sum", "count", "count_distinct"}


# ------------------------------------------------------------------ dates
def _parse(v: Any) -> datetime | None:
    if not isinstance(v, str):
        return None
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _add_months(d: datetime, n: int) -> datetime:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, calendar.monthrange(y, m)[1]))


def step(d: datetime, grain: str, n: int = 1) -> datetime:
    if grain in MONTHS:
        return _add_months(d, MONTHS[grain] * n)
    if grain == "week":
        return d + timedelta(days=7 * n)
    if grain == "day":
        return d + timedelta(days=n)
    if grain == "hour":
        return d + timedelta(hours=n)
    return d + timedelta(minutes=n)


def _fmt(d: datetime, like: str) -> str:
    return d.date().isoformat() if len(like) <= 10 else d.isoformat()


# ------------------------------------------------------------------ models
def _holt(y: list[float], a: float, b: float) -> tuple[float, float, float]:
    """(level, trend, sse of one-step errors)."""
    level, trend = y[0], y[1] - y[0]
    sse = 0.0
    for t in range(1, len(y)):
        fc = level + trend
        sse += (y[t] - fc) ** 2
        new_level = a * y[t] + (1 - a) * (level + trend)
        trend = b * (new_level - level) + (1 - b) * trend
        level = new_level
    return level, trend, sse


def _hw(y: list[float], m: int, a: float, b: float, g: float) -> tuple[float, float, list[float], float]:
    """Additive Holt-Winters: (level, trend, last m seasonal terms, sse)."""
    first = sum(y[:m]) / m
    second = sum(y[m : 2 * m]) / m
    level, trend = first, (second - first) / m
    season = [y[i] - first for i in range(m)]
    sse = 0.0
    for t in range(m, len(y)):
        s = season[t % m]
        fc = level + trend + s
        sse += (y[t] - fc) ** 2
        new_level = a * (y[t] - s) + (1 - a) * (level + trend)
        trend = b * (new_level - level) + (1 - b) * trend
        season[t % m] = g * (y[t] - new_level) + (1 - g) * s
        level = new_level
    n = len(y)
    ordered = [season[(n + i) % m] for i in range(m)]  # seasonal term for steps n, n+1, ...
    return level, trend, ordered, sse


def predict(
    y: list[float], periods: int, season: int | None, z: float
) -> tuple[str, list[tuple[float, float, float]]]:
    """(method, [(forecast, lower, upper)])."""
    n = len(y)
    if n < 4:
        return "none", []
    if season and season > 1 and n >= 2 * season:
        best = None
        for a, b, g in product(GRID, TREND_GRID, GRID):
            level, trend, seas, sse = _hw(y, season, a, b, g)
            if best is None or sse < best[0]:
                best = (sse, level, trend, seas)
        sse, level, trend, seas = best  # type: ignore[misc]
        sigma = math.sqrt(sse / max(1, n - season))
        out = []
        for h in range(1, periods + 1):
            fc = level + h * trend + seas[(h - 1) % season]
            width = z * sigma * math.sqrt(h)
            out.append((fc, fc - width, fc + width))
        return "holt_winters", out
    best = None
    for a, b in product(GRID, TREND_GRID):
        level, trend, sse = _holt(y, a, b)
        if best is None or sse < best[0]:
            best = (sse, level, trend)
    sse, level, trend = best  # type: ignore[misc]
    sigma = math.sqrt(sse / max(1, n - 2))
    out = []
    for h in range(1, periods + 1):
        fc = level + h * trend
        width = z * sigma * math.sqrt(h)
        out.append((fc, fc - width, fc + width))
    return "holt", out


# ------------------------------------------------------------------ query results
def compute(
    columns: list[dict],
    rows: list[list],
    periods: int,
    interval: float,
    seasonality: Any,
    exclude_current: bool = True,
    now: datetime | None = None,
) -> dict:
    """Forecast rows in the same layout as the result rows, with lower and upper bounds at the measure
    positions. Needs one date dimension with a level (grain)."""
    date_idx = [i for i, c in enumerate(columns) if c["role"] == "dimension" and c.get("grain")]
    if len(date_idx) != 1:
        return {"error": "A forecast needs one date at a level such as month (and no other date)"}
    di = date_idx[0]
    grain = columns[di]["grain"]
    measure_idx = [i for i, c in enumerate(columns) if c["role"] == "measure" and not c.get("calc")]
    if not measure_idx:
        return {"error": "A forecast needs a measure"}
    series_idx = [i for i, c in enumerate(columns) if c["role"] == "dimension" and i != di]
    if seasonality == "none":
        season = None
    elif isinstance(seasonality, int) and seasonality > 1:
        season = seasonality
    else:
        season = SEASONS.get(grain)
    z = NormalDist().inv_cdf(0.5 + interval / 2)

    groups: dict[tuple, dict[datetime, list]] = {}
    sample = ""
    for r in rows[:50_000]:
        d = _parse(r[di])
        if d is None:
            continue
        sample = sample or r[di]
        key = tuple(r[i] for i in series_idx)
        groups.setdefault(key, {})[d] = r
    out_rows: list[list] = []
    lower: list[list] = []
    upper: list[list] = []
    method = "none"
    skipped = 0
    for key, by_date in list(groups.items())[:MAX_SERIES]:
        dates = sorted(by_date)
        # every period from the first to the last, so gaps do not bend the model
        full = [dates[0]]
        while full[-1] < dates[-1] and len(full) < MAX_POINTS:
            full.append(step(full[-1], grain))
        # The period we are in is not over yet: leave it out of the fit and forecast it too
        current = (
            exclude_current
            and len(full) > 4
            and step(full[-1], grain).replace(tzinfo=None) > (now or datetime.now()).replace(tzinfo=None)
        )
        fit = full[:-1] if current else full
        start = fit[-1]
        future = [step(start, grain, h) for h in range(1, periods + 1)]
        new_rows = [[None] * len(columns) for _ in future]
        lo_rows = [[None] * len(columns) for _ in future]
        hi_rows = [[None] * len(columns) for _ in future]
        any_measure = False
        for mi in measure_idx:
            agg = columns[mi].get("agg") or ""
            ys: list[float] = []
            last: float | None = None
            for d in fit[-TRAIN_POINTS:]:
                v = by_date.get(d, [None] * len(columns))[mi]
                if isinstance(v, int | float) and not isinstance(v, bool):
                    last = float(v)
                    ys.append(float(v))
                elif agg in ADDITIVE and d not in by_date:
                    ys.append(0.0)
                elif last is not None:
                    ys.append(last)
            m, preds = predict(ys, periods, season, z)
            if not preds:
                continue
            if ys and min(ys) >= 0:
                # Counts and amounts that were never negative are not forecast below zero
                preds = [(max(fc, 0.0), max(lo, 0.0), max(hi, 0.0)) for fc, lo, hi in preds]
            any_measure = True
            method = m if method == "none" or m == "holt_winters" else method
            for h, (fc, lo, hi) in enumerate(preds):
                new_rows[h][mi], lo_rows[h][mi], hi_rows[h][mi] = round(fc, 6), round(lo, 6), round(hi, 6)
        if not any_measure:
            skipped += 1
            continue
        for h, d in enumerate(future):
            for row in (new_rows[h], lo_rows[h], hi_rows[h]):
                row[di] = _fmt(d, sample)
                for j, si in enumerate(series_idx):
                    row[si] = key[j]
        out_rows += new_rows
        lower += lo_rows
        upper += hi_rows
    result: dict[str, Any] = {
        "rows": out_rows,
        "lower": lower,
        "upper": upper,
        "method": method,
        "season": season if method == "holt_winters" else None,
        "interval": interval,
        "periods": periods,
    }
    if skipped:
        result["skipped"] = skipped
    if len(groups) > MAX_SERIES:
        result["truncated"] = True
    if not out_rows:
        result["error"] = "Not enough history to forecast (at least 4 periods are needed)"
    return result
