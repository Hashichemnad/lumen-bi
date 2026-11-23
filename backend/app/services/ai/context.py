"""What a model is told about a dataset: its fields and, for text fields, a few common values.

Values are read as the person asking (row-level security and masking apply), and masked columns are never
sampled. Nothing else from the data is sent.
"""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Dataset, DatasetField
from app.query import engine, masking
from app.query.rls import Principal
from app.query.spec import QuerySpec
from app.semantic import model
from app.services import settings as app_settings

SAMPLED_FIELDS = 12
VALUES_PER_FIELD = 15
MAX_FIELDS = 150


def usable_fields(db: Session, ds: Dataset) -> list[DatasetField]:
    """Fields a model may use: not hidden, not broken."""
    return [f for f in model.fields_of(db, ds.id) if not f.hidden and not f.missing and not f.error][
        :MAX_FIELDS
    ]


def _short(v: Any) -> str:
    text = str(v)
    return text if len(text) <= 60 else text[:57] + "…"


def today_line(db: Session) -> str:
    tz = ZoneInfo(get_settings().timezone)
    now = datetime.now(tz)
    reg = app_settings.regional(db)
    month = datetime(2000, reg.fiscal_year_start, 1).strftime("%B")
    day = datetime(2024, 1, reg.week_start).strftime("%A")  # 1 Jan 2024 was a Monday
    return f"Today is {now:%A %d %B %Y}. The fiscal year starts in {month}. Weeks start on {day}."


def describe(db: Session, ds: Dataset, principal: Principal, *, values: bool = True) -> str:
    fields = usable_fields(db, ds)
    _, masked = masking.replacements(db, ds, principal)
    masked_set = set(masked)
    lines = [f'Dataset "{ds.name}" (id {ds.id}), about {ds.row_count} rows.']
    if ds.description:
        lines.append(f"About it: {ds.description[:500]}")
    lines.append("Fields (name | role | type | default aggregation | description):")
    sampled = 0
    dates = [f for f in fields if f.data_type in ("date", "datetime")]
    for f in fields:
        parts = [f'"{f.name}"', f.role, f.data_type]
        if f.role == "measure":
            parts.append("already aggregated" if f.is_aggregate else f.default_agg)
        else:
            parts.append("")
        desc = (f.description or "")[:200]
        if f.label and f.label != f.name:
            desc = f"shown as {f.label}. {desc}".strip()
        parts.append(desc)
        lines.append("- " + " | ".join(parts))
        if (
            values
            and f.role == "dimension"
            and f.data_type == "string"
            and f.name not in masked_set
            and sampled < SAMPLED_FIELDS
        ):
            try:
                found = engine.field_values(
                    db, ds.id, principal, f.name, limit=VALUES_PER_FIELD, order="frequency"
                )
            except Exception:  # noqa: BLE001 - a broken field must not stop the question
                continue
            sampled += 1
            vals = [_short(v) for v in found["values"] if v is not None]
            if vals:
                more = ", …" if found["truncated"] else ""
                lines.append("    values: " + ", ".join(f'"{v}"' for v in vals) + more)
        elif f.name in masked_set:
            lines.append("    (values are hidden)")
    if values and dates:
        try:
            spec = QuerySpec(
                dataset=ds.id,
                measures=[
                    {"field": d.name, "agg": a, "key": f"{a}{i}"}
                    for i, d in enumerate(dates[:3])
                    for a in ("min", "max")
                ],
            )
            res = engine.run(db, spec, principal)
            row = res["rows"][0] if res["rows"] else []
            for i, d in enumerate(dates[:3]):
                lo, hi = (row[2 * i : 2 * i + 2] + [None, None])[:2]
                if lo or hi:
                    lines.append(f'Dates in "{d.name}" run from {str(lo)[:10]} to {str(hi)[:10]}.')
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(lines)


def result_table(result: dict, max_rows: int = 60) -> str:
    """A query result as a compact text table for a model (aggregated rows only)."""
    cols = result.get("columns") or []
    header = " | ".join(c.get("label") or c.get("key") for c in cols)
    rows = result.get("rows") or []
    out = [header]
    for r in rows[:max_rows]:
        out.append(" | ".join("" if v is None else _short(_num(v)) for v in r))
    if len(rows) > max_rows:
        out.append(f"… {len(rows) - max_rows} more rows")
    if result.get("totals"):
        out.append("Totals: " + " | ".join("" if v is None else _short(_num(v)) for v in result["totals"]))
    if result.get("restricted"):
        out.append("(Only the rows this person may see are included.)")
    return "\n".join(out)


def _num(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 4)
    return v
