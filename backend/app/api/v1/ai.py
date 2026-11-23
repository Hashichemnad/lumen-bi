"""AI features and their administration.

GET  /ai/status                      which AI features are available (anyone signed in)
POST /ai/ask                         a question -> chart, result and short answer (Explorers and up; only
                                     the rows they may see)
POST /ai/explain                     what stands out in a chart (Explorers and up)
POST /ai/explain-tile                the same for a dashboard tile, with the viewer's filters
POST /ai/formula                     a calculated-field formula from a description (Creators)
POST /ai/dashboard                   a starter dashboard for a dataset (Explorers and up)
PUT  /datasets/{id}/sensitive        mark data as sensitive: self-hosted models only (owner or admin)
GET|POST|PUT|DELETE /admin/ai/providers[/{id}], POST .../test     models (admins)
GET  /admin/ai/usage                 requests by model and feature (admins)
GET|PUT /admin/ai/test-set, POST /admin/ai/compare                 compare models on the same questions
"""

from datetime import timedelta
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_admin, require_role
from app.core.crypto import encrypt
from app.db import get_db
from app.models import AiProvider, AiRequest, AppSetting, Dashboard, Dataset, Role, User, Workspace
from app.models.common import utcnow
from app.query.spec import QuerySpec
from app.services import audit, workspaces
from app.services import dashboards as dash_svc
from app.services.ai import features
from app.services.ai import providers as prov
from app.services.ai.providers import AiError

router = APIRouter(tags=["ai"])
require_creator = require_role(Role.creator)
require_explorer = require_role(Role.explorer)
Lang = Literal["en", "ar"]


def _bad(exc: AiError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


# ------------------------------------------------------------------ status
@router.get("/ai/status")
def ai_status(db: Session = Depends(get_db), user: User = Depends(current_user)):
    enabled = db.scalars(select(AiProvider).where(AiProvider.enabled.is_(True))).all()
    return {
        "available": bool(enabled),
        "features": {f: any(prov.allows(p, f) for p in enabled) for f in prov.FEATURES},
        "local": any(p.local for p in enabled),
    }


# ------------------------------------------------------------------ features
class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    dataset_id: int | None = None
    language: Lang | None = None
    answer: bool = True


@router.post("/ai/ask")
def ask(body: AskIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)):
    try:
        out = features.ask(db, user, body.question, body.dataset_id, body.language, body.answer)
    except AiError as exc:
        raise _bad(exc) from exc
    audit.record(
        db, "ai.ask", user=user, request=request, object_type="dataset", object_id=out["dataset_id"],
        details={"provider": out["provider"]},
    )  # fmt: skip
    return out


class ExplainIn(BaseModel):
    query: QuerySpec
    title: str = Field("", max_length=200)
    language: Lang = "en"


@router.post("/ai/explain")
def explain(
    body: ExplainIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    try:
        out = features.explain(db, user, body.query, body.title, body.language)
    except AiError as exc:
        raise _bad(exc) from exc
    audit.record(
        db, "ai.explain", user=user, request=request, object_type="dataset", object_id=body.query.dataset,
        details={"provider": out["provider"]},
    )  # fmt: skip
    return out


class ExplainTileIn(BaseModel):
    dashboard_id: int
    item: str
    filters: list[dict[str, Any]] = []
    params: dict[str, Any] = {}
    drill: list[dict[str, Any]] = []
    title: str = Field("", max_length=200)
    language: Lang = "en"


@router.post("/ai/explain-tile")
def explain_tile(
    body: ExplainTileIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_explorer),
):
    """Explain a dashboard tile as the person sees it (their filters and row-level security)."""
    d = db.get(Dashboard, body.dashboard_id)
    if d is None or not dash_svc.share_level(db, user, d):
        raise HTTPException(404, "Dashboard not found")
    try:
        _, spec = dash_svc.chart_spec(
            db, d, body.item, filters=body.filters, params=body.params, drill=body.drill
        )
        out = features.explain(db, user, spec, body.title, body.language, via_dashboard=True)
    except dash_svc.DashboardError as exc:
        raise HTTPException(exc.status, exc.message) from exc
    except AiError as exc:
        raise _bad(exc) from exc
    audit.record(
        db, "ai.explain", user=user, request=request, object_type="dashboard", object_id=d.id,
        details={"provider": out["provider"], "tile": body.item},
    )  # fmt: skip
    return out


class FormulaIn(BaseModel):
    dataset_id: int
    description: str = Field(min_length=3, max_length=1000)
    language: Lang = "en"


@router.post("/ai/formula")
def formula(body: FormulaIn, db: Session = Depends(get_db), user: User = Depends(require_creator)):
    try:
        return features.formula(db, user, body.dataset_id, body.description, body.language)
    except AiError as exc:
        raise _bad(exc) from exc


class DashboardIn(BaseModel):
    dataset_id: int
    goal: str = Field("", max_length=1000)
    language: Lang = "en"
    workspace_id: int | None = None


@router.post("/ai/dashboard", status_code=status.HTTP_201_CREATED)
def auto_dashboard(
    body: DashboardIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_explorer)
):
    if body.workspace_id is not None and (
        db.get(Workspace, body.workspace_id) is None or not workspaces.can_add_to(db, user, body.workspace_id)
    ):
        raise HTTPException(403, "You can add items only to workspaces where you are an editor or manager")
    try:
        d = features.dashboard(db, user, body.dataset_id, body.goal, body.language, body.workspace_id)
    except AiError as exc:
        db.rollback()
        raise _bad(exc) from exc
    audit.record(
        db, "dashboard.create", user=user, request=request, object_type="dashboard", object_id=d.id,
        details={"name": d.name, "ai": True}, commit=False,
    )  # fmt: skip
    db.commit()
    return {"id": d.id, "name": d.name}


class SensitiveIn(BaseModel):
    sensitive: bool


@router.put("/datasets/{dataset_id}/sensitive")
def set_sensitive(
    dataset_id: int,
    body: SensitiveIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    if not (user.has_role(Role.admin) or ds.owner_id == user.id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the dataset's owner or an admin can change this")
    ds.sensitive = body.sensitive
    audit.record(
        db, "dataset.sensitive", user=user, request=request, object_type="dataset", object_id=ds.id,
        details={"sensitive": body.sensitive}, commit=False,
    )  # fmt: skip
    db.commit()
    return {"sensitive": ds.sensitive, "inherited_sensitive": not ds.sensitive and prov.is_sensitive(db, ds)}


# ------------------------------------------------------------------ admin: providers
class ProviderIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["anthropic", "openai", "openai_compatible"]
    base_url: str = Field("", max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: str | None = Field(None, max_length=1000)  # None = keep the saved key; "" = remove it
    local: bool = False
    enabled: bool = True
    is_default: bool = False
    features: list[Literal["ask", "explain", "formula", "dashboard", "digest"]] = []

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if v:
            u = urlparse(v)
            if u.scheme not in ("http", "https") or not u.netloc:
                raise ValueError("The address must start with http:// or https://")
        return v.rstrip("/")


def _provider_out(p: AiProvider) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "kind": p.kind,
        "base_url": p.base_url,
        "model": p.model,
        "has_key": bool(p.api_key_enc),
        "local": p.local,
        "enabled": p.enabled,
        "is_default": p.is_default,
        "features": p.features or [],
        "updated_at": p.updated_at,
    }


def _check(body: ProviderIn) -> None:
    if body.kind == "openai_compatible" and not body.base_url:
        raise HTTPException(
            422, "Enter the address of the self-hosted model (for example http://ollama:11434/v1)"
        )
    if body.local and body.kind != "openai_compatible":
        # Cloud services are never "self-hosted", so sensitive data can never reach them by mistake
        raise HTTPException(
            422, "Only a self-hosted model (OpenAI-compatible address) can be marked self-hosted"
        )


def _apply(db: Session, p: AiProvider, body: ProviderIn) -> None:
    p.name = body.name.strip()
    p.kind = body.kind
    p.base_url = body.base_url
    p.model = body.model.strip()
    p.local = body.local
    p.enabled = body.enabled
    p.features = list(dict.fromkeys(body.features))
    if body.api_key is not None:
        p.api_key_enc = encrypt(body.api_key.strip()) if body.api_key.strip() else ""
    if body.is_default:
        for other in db.scalars(select(AiProvider).where(AiProvider.is_default.is_(True))):
            other.is_default = False
    p.is_default = body.is_default


@router.get("/admin/ai/providers")
def list_providers(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    rows = db.scalars(select(AiProvider).order_by(AiProvider.is_default.desc(), AiProvider.id)).all()
    return {"providers": [_provider_out(p) for p in rows], "suggested_models": prov.SUGGESTED_MODELS}


@router.post("/admin/ai/providers", status_code=status.HTTP_201_CREATED)
def create_provider(
    body: ProviderIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    _check(body)
    if body.kind != "openai_compatible" and not (body.api_key or "").strip():
        raise HTTPException(422, "Enter the API key")
    p = AiProvider(name=body.name, kind=body.kind, model=body.model)
    _apply(db, p, body)
    db.add(p)
    db.flush()
    audit.record(
        db, "ai.provider_create", user=admin, request=request, object_type="ai_provider", object_id=p.id,
        details={"name": p.name, "kind": p.kind, "model": p.model, "local": p.local}, commit=False,
    )  # fmt: skip
    db.commit()
    return _provider_out(p)


def _provider(db: Session, provider_id: int) -> AiProvider:
    p = db.get(AiProvider, provider_id)
    if p is None:
        raise HTTPException(404, "AI model not found")
    return p


@router.put("/admin/ai/providers/{provider_id}")
def update_provider(
    provider_id: int,
    body: ProviderIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    p = _provider(db, provider_id)
    _check(body)
    _apply(db, p, body)
    audit.record(
        db, "ai.provider_update", user=admin, request=request, object_type="ai_provider", object_id=p.id,
        details={"name": p.name, "kind": p.kind, "model": p.model, "local": p.local, "enabled": p.enabled,
                 "key_changed": body.api_key is not None},
        commit=False,
    )  # fmt: skip
    db.commit()
    return _provider_out(p)


@router.delete("/admin/ai/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    provider_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    p = _provider(db, provider_id)
    audit.record(
        db, "ai.provider_delete", user=admin, request=request, object_type="ai_provider", object_id=p.id,
        details={"name": p.name}, commit=False,
    )  # fmt: skip
    db.delete(p)
    db.commit()


@router.post("/admin/ai/providers/{provider_id}/test")
def test_provider(provider_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    p = _provider(db, provider_id)
    try:
        return features.test_provider(p)
    except AiError as exc:
        return {"ok": False, "error": exc.message}


# ------------------------------------------------------------------ admin: usage and comparison
@router.get("/admin/ai/usage")
def usage(days: int = 30, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    grouped = db.execute(
        select(
            AiRequest.provider_name,
            AiRequest.feature,
            func.count(),
            func.sum(AiRequest.input_tokens),
            func.sum(AiRequest.output_tokens),
            func.avg(AiRequest.ms),
        )
        .where(AiRequest.created_at >= since)
        .group_by(AiRequest.provider_name, AiRequest.feature)
        .order_by(func.count().desc())
    ).all()
    failed = dict(
        ((r[0], r[1]), r[2])
        for r in db.execute(
            select(AiRequest.provider_name, AiRequest.feature, func.count())
            .where(AiRequest.created_at >= since, AiRequest.status == "failed")
            .group_by(AiRequest.provider_name, AiRequest.feature)
        ).all()
    )
    recent = db.scalars(select(AiRequest).order_by(AiRequest.created_at.desc()).limit(50)).all()
    ids = {r.user_id for r in recent if r.user_id}
    people = {u.id: u.email for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    return {
        "summary": [
            {
                "provider": r[0],
                "feature": r[1],
                "requests": r[2],
                "failed": failed.get((r[0], r[1]), 0),
                "input_tokens": int(r[3] or 0),
                "output_tokens": int(r[4] or 0),
                "avg_ms": round(float(r[5] or 0)),
            }
            for r in grouped
        ],
        "recent": [
            {
                "at": r.created_at,
                "user": people.get(r.user_id, "") if r.user_id else "",
                "provider": r.provider_name,
                "feature": r.feature,
                "status": r.status,
                "error": r.error,
                "ms": r.ms,
            }
            for r in recent
        ],
    }


class TestQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    expect_fields: list[str] = []
    expect_chart: str = ""


class TestSet(BaseModel):
    dataset_id: int | None = None
    questions: list[TestQuestion] = Field([], max_length=30)


@router.get("/admin/ai/test-set", response_model=TestSet)
def get_test_set(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    row = db.get(AppSetting, "ai_test_set")
    try:
        return TestSet(**(row.value if row else {}))
    except ValueError:
        return TestSet()


@router.put("/admin/ai/test-set", response_model=TestSet)
def save_test_set(body: TestSet, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    row = db.get(AppSetting, "ai_test_set")
    if row is None:
        row = AppSetting(key="ai_test_set", value={})
        db.add(row)
    row.value = body.model_dump()
    row.updated_by_id = admin.id
    db.commit()
    return body


class CompareIn(TestSet):
    provider_ids: list[int] = Field(min_length=1, max_length=6)


@router.post("/admin/ai/compare")
def compare(body: CompareIn, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    if not body.dataset_id or not body.questions:
        raise HTTPException(422, "Choose a dataset and add at least one question")
    questions = [
        {"question": q.question, "expect": {"fields": q.expect_fields, "chart": q.expect_chart or None}}
        for q in body.questions
    ]
    try:
        return {"results": features.compare(db, admin, body.dataset_id, questions, body.provider_ids)}
    except AiError as exc:
        raise _bad(exc) from exc
