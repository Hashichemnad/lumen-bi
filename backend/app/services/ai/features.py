"""The AI features. Each one: route to an allowed model, send only what is needed, check what comes back.

- ask: a question in English or Arabic -> a chart (validated query, run with the person's row-level
  security) and a short answer
- explain: what stands out in a chart's result
- formula: a calculated-field formula from a description, checked by the formula compiler
- dashboard: a starter dashboard for a dataset (charts and layout), created for the person
- compare: the same questions on several providers (admins), to choose a model
"""

import re
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AiProvider, Chart, Dashboard, Dataset, User
from app.query import duck, engine, rls
from app.query.spec import QuerySpec
from app.services.ai import context, intent
from app.services.ai.providers import AiError, call_model, candidates, generate, is_sensitive, parse_json

ARABIC = re.compile("[\u0600-\u06ff]")


class Unanswerable(AiError):
    """The model says the data cannot answer the question (not worth a retry)."""

    def __init__(self, message: str):
        super().__init__(message[:300], 422)


def language_of(text: str, fallback: str = "en") -> str:
    return "ar" if ARABIC.search(text or "") else fallback


def _lang_rule(lang: str) -> str:
    return "Write every sentence in Arabic." if lang == "ar" else "Write every sentence in English."


# ------------------------------------------------------------------ prompts
INTENT_SCHEMA = """{"title": "short chart title",
 "chart": "auto|table|kpi|bar|stacked|line|area|combo|donut|scatter|heatmap|treemap|map|funnel|gauge",
 "dimensions": [{"field": "exact field name",
                 "grain": "year|quarter|month|week|day|fiscal_year|fiscal_quarter"}],
 "measures": [{"field": "exact field name, or * for the number of rows",
               "agg": "sum|avg|count|count_distinct|min|max|median"}],
 "filters": [{"field": "...",
              "op": "in|not_in|eq|neq|gt|gte|lt|lte|between|relative|top|bottom|contains|starts_with",
              "value": "...", "values": ["..."],
              "unit": "day|week|month|quarter|year|fiscal_quarter|fiscal_year", "n": 12,
              "by": {"field": "...", "agg": "sum"}, "agg": "sum"}],
 "sort": {"by": "field name", "dir": "asc|desc"},
 "limit": 10,
 "note": "one short line saying how you read the question"}"""

INTENT_RULES = """Rules:
- Use only the field names listed, written exactly. Never invent fields or values.
- grain is only for date fields. Use "relative" for periods counted back from today: "this year" is
  unit year, n 1; "last 6 months" is unit month, n 6 (it includes the current month). Use "between" with
  ISO dates for fixed ranges.
- "How many orders" is {"field": "*", "agg": "count"}, or count_distinct of an id field if there is one.
- "Top 5 X by Y": a "top" filter on X with n 5 and by Y, and sort by Y desc.
- To filter on an aggregated value (e.g. regions with sales over 1M) set "agg" on the filter.
- Pick the chart that fits: a single number -> kpi (no dimensions); trend over time -> line; ranking or
  comparison -> bar; share of a whole with few parts -> donut; two measures against each other ->
  scatter.
- Keep it simple: at most 3 dimensions and 4 measures."""

CANNOT = (
    '- If the fields cannot answer the question, answer {"error": "one short sentence saying what is '
    'missing"}.'
)


def _ask_system(lang: str) -> str:
    return (
        "You turn business questions into charts for Lumen BI, a business intelligence tool.\n"
        "Answer with ONE JSON object and nothing else:\n"
        + INTENT_SCHEMA
        + "\n"
        + INTENT_RULES
        + "\n"
        + CANNOT
        + "\nThe question may be in English or Arabic. Field names stay exactly as listed. "
        + _lang_rule(lang)
        + " (title, note and error)."
    )


ANSWER_SYSTEM = (
    "You are a careful data analyst. Answer the question from the query result in one to three short "
    "sentences, using the actual numbers (rounded sensibly). Do not invent anything that is not in the "
    "result. Plain text, no headings, no markdown tables."
)

EXPLAIN_SYSTEM = (
    "You are a careful data analyst explaining a chart to a busy manager. Give 3 to 5 short bullet "
    "points, each starting with '- ': the main pattern, the highest and lowest, notable changes or "
    "outliers, and one thing worth checking next. Use the numbers. Never invent causes; when the data "
    "cannot say why, say so. Plain text only."
)

FORMULA_SYSTEM = """You write calculated-field formulas for Lumen BI. The language is like Tableau's:
- Fields in square brackets: [Sales]. Parameters: @Name. Text in single quotes: 'UAE'.
- Dates: #2026-01-31#.
- Operators: + - * / % = <> < <= > >= AND OR NOT, and IN ('a', 'b').
- IF cond THEN x ELSEIF cond THEN y ELSE z END, CASE [f] WHEN 'a' THEN 1 ELSE 0 END, IIF(cond, x, y).
- Aggregates: SUM AVG MEDIAN MIN MAX COUNT COUNTD STDEV VAR PERCENTILE(x, 0.9). A formula is either
  row level or fully aggregated: SUM([Profit]) / SUM([Sales]) is fine; [Profit] / SUM([Sales]) is not.
- Numbers: ABS ROUND(x, n) CEILING FLOOR SQRT POWER EXP LN LOG SIGN DIV ZN IFNULL COALESCE ISNULL
- Text: LEN LOWER UPPER TRIM LTRIM RTRIM LEFT RIGHT MID CONTAINS STARTSWITH ENDSWITH REPLACE SPLIT FIND
- Conversion: STR INT FLOAT
- Dates: YEAR QUARTER MONTH WEEK DAY WEEKDAY DATETRUNC('month', d) DATEPART('year', d) DATEADD('day', n, d)
  DATEDIFF('day', start, end) MAKEDATE(y, m, d) TODAY() NOW() DATE DATETIME
Answer with ONE JSON object only:
{"name": "short field name", "formula": "...", "explanation": "one short sentence"}.
Use only the fields listed, exactly as written."""


def _dashboard_system(lang: str) -> str:
    return (
        "You design a starter dashboard in Lumen BI for one dataset. Propose 4 to 8 charts that together "
        "give a clear overview: first 2 to 4 headline numbers (chart kpi, no dimensions), then trends and "
        "breakdowns.\n"
        'Answer with ONE JSON object only: {"name": "dashboard name", "description": "one sentence", '
        '"charts": [chart, chart, ...]} where each chart is:\n'
        + INTENT_SCHEMA
        + "\n"
        + INTENT_RULES
        + f"\n{_lang_rule(lang)} (names, titles and notes). Field names stay exactly as listed."
    )


# ------------------------------------------------------------------ helpers
def queryable(db: Session, user: User, dataset_id: int) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None or not rls.can_query(db, user, ds):
        raise AiError("Dataset not found", 404)
    if not ds.current_version:
        raise AiError("This dataset has no data yet. Sync it first.", 409)
    return ds


def _run(db: Session, spec: QuerySpec, principal: rls.Principal) -> dict:
    try:
        return engine.run(db, spec, principal)
    except (engine.QueryError, duck.QueryTimeout) as exc:
        raise AiError(getattr(exc, "message", str(exc))) from exc


def _pick_dataset(db: Session, user: User, question: str) -> Dataset:
    usable = [
        d
        for d in db.scalars(select(Dataset).where(Dataset.current_version > 0).order_by(Dataset.name))
        if rls.can_query(db, user, d)
    ]
    if not usable:
        raise AiError("There is no data you can ask about yet.", 404)
    if not candidates(db, "ask", usable)[0]:
        # No self-hosted model: sensitive datasets cannot be described to a cloud model, so leave them out
        usable = [d for d in usable if not is_sensitive(db, d)] or usable
    if len(usable) == 1:
        return usable[0]
    lines = []
    for d in usable[:60]:
        names = ", ".join(f.name for f in context.usable_fields(db, d)[:40])
        lines.append(f"- id {d.id}: {d.name}. {d.description[:200]} Fields: {names}")
    system = (
        "Choose the one dataset that can best answer the question. Answer with ONE JSON object only: "
        '{"dataset": id} or {"error": "one short sentence"} if none fits. '
        + _lang_rule(language_of(question))
    )
    reply, _ = generate(
        db, user=user, feature="ask", datasets=usable[:60], system=system,
        prompt=f"Datasets:\n{chr(10).join(lines)}\n\nQuestion: {question}", max_tokens=200,
    )  # fmt: skip
    data = parse_json(reply.text)
    if data.get("error"):
        raise AiError(str(data["error"])[:300], 422)
    chosen = next((d for d in usable if d.id == _int(data.get("dataset"))), None)
    if chosen is None:
        raise AiError("Lumen could not tell which data you mean. Choose a dataset and ask again.", 422)
    return chosen


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _intent_to_result(
    db: Session, ds: Dataset, principal: rls.Principal, data: dict
) -> tuple[QuerySpec, dict, dict, dict]:
    if data.get("error"):
        raise Unanswerable(str(data["error"]))
    spec, config, cleaned = intent.build(data, context.usable_fields(db, ds), ds.id)
    return spec, config, cleaned, _run(db, spec, principal)


def _ask_once(
    db: Session,
    user: User | None,
    ds: Dataset,
    principal: rls.Principal,
    question: str,
    lang: str,
    provider: AiProvider | None = None,
) -> dict:
    """Question -> validated chart and result. One retry, telling the model what went wrong."""
    brief = context.describe(db, ds, principal)
    prompt = f"{context.today_line(db)}\n\n{brief}\n\nQuestion: {question}"
    system = _ask_system(lang)
    reply, used = generate(
        db,
        user=user,
        feature="ask",
        datasets=[ds],
        system=system,
        prompt=prompt,
        max_tokens=1200,
        provider=provider,
    )
    try:
        data = parse_json(reply.text)
        spec, config, cleaned, result = _intent_to_result(db, ds, principal, data)
    except Unanswerable:
        raise
    except AiError as exc:
        retry = (
            f"{prompt}\n\nYour previous answer was:\n{reply.text[:3000]}\n"
            f"It could not be used: {exc.message}. Fix it."
        )
        reply, used = generate(
            db,
            user=user,
            feature="ask",
            datasets=[ds],
            system=system,
            prompt=retry,
            max_tokens=1200,
            provider=used,
        )
        data = parse_json(reply.text)
        spec, config, cleaned, result = _intent_to_result(db, ds, principal, data)
    return {
        "dataset_id": ds.id,
        "dataset_name": ds.name,
        "title": cleaned["title"],
        "note": cleaned["note"],
        "chart": cleaned["chart"],
        "config": config,
        "query": spec.model_dump(exclude={"dataset"}, exclude_defaults=True),
        "result": result,
        "provider": used.name,
    }


# ------------------------------------------------------------------ features
def ask(
    db: Session,
    user: User,
    question: str,
    dataset_id: int | None,
    lang: str | None = None,
    answer: bool = True,
) -> dict:
    question = question.strip()
    if not question:
        raise AiError("Type a question")
    lang = lang or language_of(question)
    ds = queryable(db, user, dataset_id) if dataset_id else _pick_dataset(db, user, question)
    principal = rls.principal_for(db, user)
    out = _ask_once(db, user, ds, principal, question, lang)
    out["answer"] = ""
    if answer and out["result"]["rows"]:
        prompt = (
            f"Question: {question}\nHow it was read: {out['note']}\n"
            f"Result:\n{context.result_table(out['result'])}"
        )
        reply, _ = generate(
            db, user=user, feature="ask", datasets=[ds], system=ANSWER_SYSTEM + " " + _lang_rule(lang),
            prompt=prompt, max_tokens=400,
        )  # fmt: skip
        out["answer"] = reply.text.strip()[:1500]
    elif answer:
        out["answer"] = (
            "لا توجد بيانات تطابق هذا السؤال." if lang == "ar" else "No data matches this question."
        )
    return out


def explain(
    db: Session, user: User, spec: QuerySpec, title: str, lang: str = "en", via_dashboard: bool = False
) -> dict:
    """via_dashboard: the spec comes from a dashboard tile the person may view (dashboards let viewers see
    a chart's data without access to the whole dataset), so the dataset check is already done."""
    ds = db.get(Dataset, spec.dataset) if via_dashboard else queryable(db, user, spec.dataset)
    if ds is None:
        raise AiError("Dataset not found", 404)
    principal = rls.principal_for(db, user)
    if spec.mode != "aggregate":
        raise AiError("Only charts can be explained, not lists of rows")
    result = _run(db, spec.model_copy(update={"limit": min(spec.limit or 200, 200)}), principal)
    if not result["rows"]:
        raise AiError("There is no data to explain")
    prompt = (
        f"{context.today_line(db)}\nChart: {title or ds.name} (dataset {ds.name})\n"
        f"Result:\n{context.result_table(result, 200)}"
    )
    reply, used = generate(
        db, user=user, feature="explain", datasets=[ds], system=EXPLAIN_SYSTEM + " " + _lang_rule(lang),
        prompt=prompt, max_tokens=700,
    )  # fmt: skip
    return {"text": reply.text.strip()[:4000], "provider": used.name}


def formula(db: Session, user: User, dataset_id: int, description: str, lang: str = "en") -> dict:
    from app.api.v1.model import _compile_candidate, _sample
    from app.semantic import model
    from app.semantic.formula import FormulaError

    ds = queryable(db, user, dataset_id)
    principal = rls.principal_for(db, user)
    fields = model.fields_of(db, ds.id)
    brief = context.describe(db, ds, principal)
    prompt = f"{brief}\n\nWhat the field should do: {description.strip()}"
    system = FORMULA_SYSTEM + " " + _lang_rule(lang) + " (name and explanation)"
    reply, used = generate(
        db, user=user, feature="formula", datasets=[ds], system=system, prompt=prompt, max_tokens=600
    )
    last_error = ""
    for attempt in range(2):
        data = parse_json(reply.text)
        if data.get("error"):
            raise AiError(str(data["error"])[:300], 422)
        text = str(data.get("formula") or "").strip()
        name = str(data.get("name") or "New field").strip()[:100] or "New field"
        try:
            compiled = _compile_candidate(db, ds, "__ai_formula__", "calculated", text, {}, fields)
            sample = _sample(db, ds, compiled, user)
            return {
                "name": name,
                "formula": text,
                "explanation": str(data.get("explanation") or "").strip()[:500],
                "type": compiled.type,
                "is_aggregate": compiled.level == "agg",
                "sample": sample,
                "provider": used.name,
            }
        except FormulaError as exc:
            last_error = exc.message
            if attempt == 0:
                retry = f"{prompt}\n\nYour formula was: {text}\nIt does not work: {exc.message}. Fix it."
                reply, used = generate(
                    db, user=user, feature="formula", datasets=[ds], system=system, prompt=retry,
                    max_tokens=600, provider=used,
                )  # fmt: skip
    raise AiError(f"The AI model could not write a working formula: {last_error}", 422)


def dashboard(
    db: Session, user: User, dataset_id: int, goal: str, lang: str, workspace_id: int | None
) -> Dashboard:
    """Creates the charts and the dashboard, owned by the person (in their workspace if they chose one)."""
    from app.services import dashboards as dash_svc

    ds = queryable(db, user, dataset_id)
    principal = rls.principal_for(db, user)
    purpose = goal.strip() or "a general overview"
    prompt = f"{context.today_line(db)}\n\n{context.describe(db, ds, principal)}\n\nWhat it is for: {purpose}"
    reply, _ = generate(
        db, user=user, feature="dashboard", datasets=[ds], system=_dashboard_system(lang), prompt=prompt,
        max_tokens=3000,
    )  # fmt: skip
    data = parse_json(reply.text)
    proposals = data.get("charts") if isinstance(data.get("charts"), list) else []
    built: list[tuple[str, str, dict, dict]] = []
    for p in proposals[:8]:
        if not isinstance(p, dict) or p.get("error"):
            continue
        try:
            spec, config, cleaned, result = _intent_to_result(db, ds, principal, p)
        except AiError:
            continue  # skip charts that do not fit the data; keep the rest
        built.append(
            (
                cleaned["title"] or ds.name,
                cleaned["chart"],
                config,
                spec.model_dump(exclude={"dataset"}, exclude_defaults=True),
            )
        )
    if not built:
        raise AiError(
            "The AI model could not design charts for this data. Try describing what you need.", 422
        )

    items = []
    x = y = 0
    kpis = [b for b in built if b[1] in ("kpi", "gauge")]
    others = [b for b in built if b[1] not in ("kpi", "gauge")]
    charts: list[Chart] = []
    for title, _chart, config, query in kpis + others:
        c = Chart(
            name=title[:200], description="", dataset_id=ds.id, owner_id=user.id, workspace_id=workspace_id,
            config=config, query=query,
        )  # fmt: skip
        db.add(c)
        charts.append(c)
    db.flush()
    kpi_w = 12 // max(1, min(len(kpis), 4)) if kpis else 3
    phone_y = 0
    for i, c in enumerate(charts):
        is_kpi = i < len(kpis)
        if is_kpi:
            w, h = kpi_w, 4
            if x + w > 12:
                x, y = 0, y + h
        else:
            if i == len(kpis):
                x, y = 0, y + (4 if kpis else 0)
            w, h = 6, 9
            if x + w > 12:
                x, y = 0, y + h
        items.append(
            {
                "id": f"t{i + 1}",
                "kind": "chart",
                "chart_id": c.id,
                "title": c.name,
                "show_title": True,
                "desktop": {"x": x, "y": y, "w": w, "h": h},
                "phone": {"x": 0, "y": phone_y, "w": 4, "h": 4 if is_kpi else 8},
            }
        )
        phone_y += 4 if is_kpi else 8
        x += w
    name = str(data.get("name") or f"{ds.name} overview").strip()[:200]
    config = dash_svc.validate_config(
        db,
        user,
        {
            "tabs": [{"id": "tab1", "name": "Overview" if lang != "ar" else "نظرة عامة", "items": items}],
            "filters": [],
        },
        None,
    )
    d = Dashboard(
        name=name, description=str(data.get("description") or "")[:1000], owner_id=user.id,
        workspace_id=workspace_id, config=config,
    )  # fmt: skip
    db.add(d)
    db.flush()
    return d


def compare(
    db: Session, admin: User, dataset_id: int, questions: list[dict], provider_ids: list[int]
) -> list[dict]:
    """Runs each question on each provider (as the admin; no answers, just the chart) and checks expectations:
    {"question": "...", "expect": {"fields": ["Region", "Amount"], "chart": "bar"}}."""
    ds = queryable(db, admin, dataset_id)
    principal = rls.principal_for(db, admin)
    allowed, sensitive = candidates(db, "ask", [ds])
    allowed_ids = {p.id for p in allowed}
    providers = db.scalars(select(AiProvider).where(AiProvider.id.in_(provider_ids))).all()
    out = []
    for p in providers:
        rows = []
        if p.id not in allowed_ids:
            reason = (
                "This data is sensitive; only self-hosted models may see it"
                if sensitive and not p.local
                else "This model is turned off or not allowed for Ask Data"
            )
            out.append(
                {"provider_id": p.id, "provider": p.name, "skipped": reason, "results": [], "score": None}
            )
            continue
        passed = 0
        total_ms = 0
        for q in questions[:30]:
            text = str(q.get("question") or "").strip()
            expect = q.get("expect") or {}
            started = time.perf_counter()
            try:
                res = _ask_once(db, admin, ds, principal, text, language_of(text), provider=p)
                ms = round((time.perf_counter() - started) * 1000)
                used_fields = sorted(
                    {d["field"] for d in res["query"].get("dimensions", [])}
                    | {m["field"] for m in res["query"].get("measures", [])}
                    | {f["field"] for f in res["query"].get("filters", [])}
                )
                checks = []
                for f in expect.get("fields") or []:
                    checks.append(str(f).lower() in {u.lower() for u in used_fields})
                if expect.get("chart"):
                    checks.append(res["chart"] == expect["chart"])
                ok = all(checks) if checks else True
                passed += ok
                rows.append(
                    {"question": text, "ok": ok, "ms": ms, "title": res["title"], "chart": res["chart"],
                     "fields": used_fields, "rows": res["result"]["row_count"], "error": ""}
                )  # fmt: skip
            except AiError as exc:
                ms = round((time.perf_counter() - started) * 1000)
                rows.append(
                    {
                        "question": text,
                        "ok": False,
                        "ms": ms,
                        "title": "",
                        "chart": "",
                        "fields": [],
                        "rows": 0,
                        "error": exc.message,
                    }
                )
            total_ms += ms
        out.append(
            {
                "provider_id": p.id,
                "provider": p.name,
                "skipped": "",
                "results": rows,
                "score": round(100 * passed / len(rows)) if rows else None,
                "avg_ms": round(total_ms / len(rows)) if rows else None,
            }
        )
    return out


def test_provider(p: AiProvider) -> dict:
    """A tiny request to check the address, key and model name."""
    started = time.perf_counter()
    reply = call_model(p, "Reply with the single word OK.", "Say OK.", max_tokens=20)
    return {
        "ok": True,
        "reply": reply.text.strip()[:100],
        "ms": round((time.perf_counter() - started) * 1000),
    }
