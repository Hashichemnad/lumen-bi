"""Row-level security: which rows of a dataset a user may see.

Rules are turned into one SQL condition that the query engine puts inside the only place a dataset is read
(the `base` CTE), so it applies before filters, aggregation, totals, top-N, table calculations, filter
value lists and exports.

Principles:
- Fail closed. A missing attribute, a broken field, an unsynced access table or anything unexpected gives
  FALSE (no rows), never "no filter".
- Admins bypass. Everyone else, Creators included, is filtered.
- Rules of a dataset combine with AND. Joined and combined datasets inherit the rules of the datasets they
  are built from (mapped to their own columns), so building a join cannot be used to get around a rule.
- User values are bound parameters, never pasted into SQL.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Dataset, Group, RlsRule, User
from app.query import duck
from app.semantic import derived, model
from app.semantic.formula import Context, FieldSource, FormulaError, quote_ident
from app.services import snapshots

BUILT_IN_ATTRIBUTES = ("email", "user_id", "name", "groups")
MAX_VALUES = 20_000


class Params:
    """Collects bound values and hands out $n placeholders (order does not matter)."""

    def __init__(self) -> None:
        self.values: list[Any] = []

    def add(self, value: Any) -> str:
        self.values.append(value)
        return f"${len(self.values)}"


@dataclass
class Principal:
    """The person whose access is being applied (the signed-in user, or the one being previewed)."""

    user: User
    attributes: dict[str, list[str]]
    group_ids: set[int]

    @property
    def bypass(self) -> bool:
        return self.user.role == "admin"

    def values(self, attribute: str) -> list[str] | None:
        """The user's values for an attribute, or None if they have none (which blocks the rule)."""
        found = self.attributes.get(str(attribute).strip().lower())
        return found or None


def principal_for(db: Session, user: User) -> Principal:
    attrs: dict[str, list[str]] = {}
    for key, value in (user.attributes or {}).items():
        items = value if isinstance(value, list) else [value]
        cleaned = [str(v).strip() for v in items if v is not None and str(v).strip() != ""]
        if cleaned:
            attrs[str(key).strip().lower()] = cleaned
    groups = list(user.groups)
    # Built-ins win over custom attributes of the same name, so nobody can set "email" to someone else's
    attrs["email"] = [user.email]
    attrs["user_id"] = [str(user.id)]
    if user.name:
        attrs["name"] = [user.name]
    else:
        attrs.pop("name", None)
    attrs["groups"] = [g.name for g in groups]
    if not attrs["groups"]:
        del attrs["groups"]
    return Principal(user=user, attributes=attrs, group_ids={g.id for g in groups})


# ------------------------------------------------------------------ which rules apply to a dataset
@dataclass
class EffectiveRule:
    rule: RlsRule
    origin: Dataset  # the dataset the rule is defined on
    columns: dict[str, str] | None  # origin column -> column of the queried dataset; None = same dataset


@dataclass
class RuleResult:
    rule: RlsRule
    origin: Dataset
    status: str  # applies | exempt | all | blocked
    values: list[Any] = field(default_factory=list)
    reason: str = ""

    def jsonable(self, inherited: bool) -> dict:
        return {
            "rule_id": self.rule.id,
            "name": self.rule.name or describe(self.rule),
            "inherited_from": {"id": self.origin.id, "name": self.origin.name} if inherited else None,
            "status": self.status,
            "values": [model.jsonable(v) for v in self.values[:50]],
            "value_count": len(self.values),
            "reason": self.reason,
        }


def describe(rule: RlsRule) -> str:
    cfg = rule.config or {}
    if rule.kind == "entitlement":
        return f"[{cfg.get('field', '?')}] from access table"
    return f"[{cfg.get('field', '?')}] = user {cfg.get('attribute', '?')}"


def _parent_mappings(db: Session, ds: Dataset) -> list[tuple[Dataset, dict[str, str]]]:
    """For each dataset this one is built from: parent column -> this dataset's column."""
    source = ds.source or {}
    out: list[tuple[Dataset, dict[str, str]]] = []
    if "join" in source:
        spec = source["join"]
        base = db.get(Dataset, spec.get("base"))
        if base is not None:
            out.append((base, {c["name"]: c["name"] for c in base.columns or []}))
        for j in spec.get("joins", []):
            right = db.get(Dataset, j.get("dataset"))
            if right is not None:
                out.append((right, {c["name"]: f"{j['alias']}.{c['name']}" for c in right.columns or []}))
    elif "union" in source:
        for pid in source["union"].get("datasets", []):
            parent = db.get(Dataset, pid)
            if parent is not None:
                out.append((parent, {c["name"]: c["name"] for c in parent.columns or []}))
    return out


def effective_rules(db: Session, ds: Dataset, _seen: frozenset[int] = frozenset()) -> list[EffectiveRule]:
    if ds.id in _seen:  # cycles are refused when saving; never loop forever
        return []
    seen = _seen | {ds.id}
    own = db.scalars(
        select(RlsRule).where(RlsRule.dataset_id == ds.id, RlsRule.enabled.is_(True)).order_by(RlsRule.id)
    )
    rules = [EffectiveRule(r, ds, None) for r in own]
    if ds.kind in derived.DERIVED_KINDS:
        for parent, mapping in _parent_mappings(db, ds):
            for inherited in effective_rules(db, parent, seen):
                if inherited.columns is None:
                    composed = dict(mapping)
                else:
                    composed = {o: mapping[p] for o, p in inherited.columns.items() if p in mapping}
                rules.append(EffectiveRule(inherited.rule, inherited.origin, composed))
    return rules


def has_rules(db: Session, ds: Dataset) -> bool:
    return bool(effective_rules(db, ds))


# ------------------------------------------------------------------ turning rules into SQL
def _rule_context(db: Session, eff: EffectiveRule, own_ctx: Context) -> Context:
    if eff.columns is None:
        return own_ctx
    # Compile the origin's field against this dataset's columns; unmapped columns disappear (-> blocked)
    ctx = model.build_context(db, eff.origin.id)
    for name, src in list(ctx.fields.items()):
        if src.sql is not None:
            if name in eff.columns:
                ctx.fields[name] = FieldSource(name, src.type, sql=quote_ident(eff.columns[name]))
            else:
                del ctx.fields[name]
    return ctx


def _coerce(values: list[Any], data_type: str) -> list[Any]:
    out = []
    for v in values:
        try:
            if data_type == "integer":
                f = float(v)
                if not f.is_integer():
                    continue
                out.append(int(f))
            elif data_type == "number":
                out.append(float(v))
            elif data_type == "boolean":
                out.append(v if isinstance(v, bool) else str(v).strip().lower() in {"true", "1", "yes"})
            elif data_type == "date":
                out.append(v if isinstance(v, date) else date.fromisoformat(str(v)[:10]))
            elif data_type == "datetime":
                out.append(v if isinstance(v, datetime) else datetime.fromisoformat(str(v)))
            else:
                out.append(str(v))
        except (TypeError, ValueError):
            continue
    return out


def _entitlement_values(db: Session, cfg: dict, principal: Principal) -> tuple[list[Any] | None, str]:
    """Values the access table lists for this user; None + reason when it cannot be used."""
    ent = db.get(Dataset, cfg.get("dataset"))
    if ent is None:
        return None, "The access table dataset no longer exists"
    path = snapshots.snapshot_path(ent.id, ent.current_version)
    if path is None:
        return None, f'The access table "{ent.name}" has no data yet'
    columns = {c["name"] for c in ent.columns or []}
    user_col, value_col = cfg.get("user_column"), cfg.get("value_column")
    if user_col not in columns or value_col not in columns:
        return None, f'The access table "{ent.name}" no longer has the chosen columns'
    match = principal.values(cfg.get("match") or "email")
    if not match:
        return None, f"The user has no {cfg.get('match') or 'email'}"
    ignore_case = cfg.get("ignore_case", True)
    p = Params()
    keys = [m.lower() if ignore_case else m for m in match]
    left = f"CAST({quote_ident(user_col)} AS VARCHAR)"
    left = f"lower({left})" if ignore_case else left
    sql = (
        f"SELECT DISTINCT {quote_ident(value_col)} FROM read_parquet({snapshots._lit(path)}) "
        f"WHERE trim({left}) IN ({', '.join(p.add(k) for k in keys)}) LIMIT {MAX_VALUES + 1}"
    )
    _, rows = duck.run(sql, p.values)
    return [r[0] for r in rows if r[0] is not None], ""


def evaluate(db: Session, ds: Dataset, principal: Principal, ctx: Context, params: Params):
    """Return (SQL condition or None for no restriction, per-rule results)."""
    results: list[RuleResult] = []
    conditions: list[str] = []
    if principal.bypass:
        return None, results
    for eff in effective_rules(db, ds):
        rule, cfg = eff.rule, eff.rule.config or {}
        if principal.group_ids & {int(g) for g in rule.exempt_groups or [] if str(g).isdigit()}:
            results.append(RuleResult(rule, eff.origin, "exempt", reason="Member of an exempt group"))
            continue
        try:
            compiled = model.compile_field(_rule_context(db, eff, ctx), str(cfg.get("field", "")))
        except FormulaError as exc:
            results.append(RuleResult(rule, eff.origin, "blocked", reason=f"Field problem: {exc.message}"))
            conditions.append("FALSE")
            continue
        if compiled.level == "agg":
            results.append(RuleResult(rule, eff.origin, "blocked", reason="Rules need a row-level field"))
            conditions.append("FALSE")
            continue

        if rule.kind == "entitlement":
            raw, reason = _entitlement_values(db, cfg, principal)
            all_value = cfg.get("all_value")
            if raw is not None and all_value not in (None, "") and any(str(v) == str(all_value) for v in raw):
                results.append(RuleResult(rule, eff.origin, "all", reason="Access table gives all rows"))
                continue
        elif rule.kind == "attribute":
            raw = principal.values(cfg.get("attribute", ""))
            reason = "" if raw else f"The user has no {cfg.get('attribute') or 'attribute'} value"
        else:
            raw, reason = None, "Unknown kind of rule"

        if raw is None or not raw:
            results.append(RuleResult(rule, eff.origin, "blocked", reason=reason or "No values for the user"))
            conditions.append("FALSE")
            continue
        if len(raw) > MAX_VALUES:
            results.append(RuleResult(rule, eff.origin, "blocked", reason="Too many values for the user"))
            conditions.append("FALSE")
            continue
        ignore_case = cfg.get("ignore_case", True) and compiled.type == "string"
        values = _coerce(raw, compiled.type)
        if ignore_case:
            values = list(dict.fromkeys(str(v).strip().lower() for v in values))
        if not values:
            results.append(RuleResult(rule, eff.origin, "blocked", reason="None of the user's values fit"))
            conditions.append("FALSE")
            continue
        expr = f"lower(trim({compiled.sql}))" if ignore_case else compiled.sql
        placeholders = ", ".join(params.add(v) for v in values)
        conditions.append(f"coalesce({expr} IN ({placeholders}), FALSE)")
        results.append(RuleResult(rule, eff.origin, "applies", values=values))
    if not conditions:
        return None, results
    return " AND ".join(f"({c})" for c in conditions), results


# ------------------------------------------------------------------ who may query a dataset at all
def can_query(db: Session, user: User, ds: Dataset) -> bool:
    from app.models import DatasetShare

    if user.role in {"admin", "creator"}:
        return True
    shares = db.scalars(select(DatasetShare).where(DatasetShare.dataset_id == ds.id)).all()
    group_ids = {g.id for g in user.groups}
    for s in shares:
        if s.principal == "everyone":
            return True
        if s.principal == "user" and s.principal_id == user.id:
            return True
        if s.principal == "group" and s.principal_id in group_ids:
            return True
    return False


def group_names(db: Session, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    return {g.id: g.name for g in db.scalars(select(Group).where(Group.id.in_(ids)))}


def datasets_using_as_access_table(db: Session, dataset_id: int) -> list[Dataset]:
    out = []
    for rule in db.scalars(select(RlsRule).where(RlsRule.kind == "entitlement")):
        if str((rule.config or {}).get("dataset")) == str(dataset_id) and rule.dataset_id != dataset_id:
            ds = db.get(Dataset, rule.dataset_id)
            if ds is not None and ds not in out:
                out.append(ds)
    return out


def forget_principal(db: Session, principal: str, principal_id: int) -> None:
    """Remove a deleted user or group from dataset shares and rule exemptions (ids may be reused)."""
    from app.models import DashboardShare, DatasetShare

    for share_model in (DatasetShare, DashboardShare):
        for share in db.scalars(
            select(share_model).where(
                share_model.principal == principal, share_model.principal_id == principal_id
            )
        ):
            db.delete(share)
    from app.services import workspaces

    workspaces.forget_principal(db, principal, principal_id)
    if principal == "group":
        for rule in db.scalars(select(RlsRule)):
            if principal_id in (rule.exempt_groups or []):
                rule.exempt_groups = [g for g in rule.exempt_groups if g != principal_id]
