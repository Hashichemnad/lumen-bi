"""Column masking (PII). A masked column is replaced in the `base` step of every query, after row-level
security has picked the rows, so everything built on it (filters, groups, calculated fields, exports, the
rows behind a chart) only ever sees the masked value. Admins and the mask's exempt groups see real values.
Joined and combined datasets inherit their parents' masks through the same column mapping as RLS rules.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ColumnMask, Dataset
from app.query.rls import Principal, _parent_mappings

METHODS = ("full", "partial", "hash")


@dataclass
class EffectiveMask:
    column: str  # column of this dataset
    method: str
    exempt_groups: list[int]
    origin: Dataset


def effective_masks(db: Session, ds: Dataset, _seen: frozenset[int] = frozenset()) -> list[EffectiveMask]:
    if ds.id in _seen:
        return []
    seen = _seen | {ds.id}
    out = [
        EffectiveMask(m.column, m.method, list(m.exempt_groups or []), ds)
        for m in db.scalars(select(ColumnMask).where(ColumnMask.dataset_id == ds.id))
    ]
    for parent, mapping in _parent_mappings(db, ds):
        for m in effective_masks(db, parent, seen):
            if m.column in mapping:
                out.append(EffectiveMask(mapping[m.column], m.method, m.exempt_groups, m.origin))
    return out


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def mask_sql(column: str, method: str) -> str:
    c = f"CAST({_quote(column)} AS VARCHAR)"
    if method == "hash":
        return f"CASE WHEN {c} IS NULL THEN NULL ELSE substr(md5({c}), 1, 10) END"
    if method == "partial":
        # Emails keep the first letter and the domain; other text keeps its last 4 characters
        return (
            f"CASE WHEN {c} IS NULL THEN NULL "
            f"WHEN contains({c}, '@') THEN left({c}, 1) || '•••' || substr({c}, strpos({c}, '@')) "
            f"WHEN length({c}) <= 4 THEN '••••' "
            f"ELSE '••••' || right({c}, 4) END"
        )
    return f"CASE WHEN {c} IS NULL THEN NULL ELSE '••••' END"


def replacements(db: Session, ds: Dataset, principal: Principal) -> tuple[str, list[str]]:
    """The `* REPLACE (...)` clause for this person (empty if nothing is masked for them) and the masked
    column names. Several masks on one column: the strictest wins (full > partial > hash)."""
    if principal.bypass:
        return "", []
    present = {c["name"] for c in ds.columns or []}
    chosen: dict[str, str] = {}
    rank = {"full": 3, "partial": 2, "hash": 1}
    for m in effective_masks(db, ds):
        if m.column not in present or principal.group_ids & set(m.exempt_groups):
            continue
        if rank.get(m.method, 3) > rank.get(chosen.get(m.column, ""), 0):
            chosen[m.column] = m.method if m.method in METHODS else "full"
    if not chosen:
        return "", []
    parts = ", ".join(f"{mask_sql(col, method)} AS {_quote(col)}" for col, method in sorted(chosen.items()))
    return f" REPLACE ({parts})", sorted(chosen)
