"""Calling AI models, and choosing which one may see which data.

Routing rules (fail closed):
- only enabled providers that allow the feature are candidates;
- if any dataset involved is sensitive (or built from a sensitive dataset), only self-hosted (local)
  providers are candidates;
- the default provider wins, then the oldest;
- no candidate -> AiError with a plain reason. Nothing is ever sent to a cloud model as a fallback.

What is sent: field names, labels, descriptions and types, a few distinct values of text fields (read as
the person asking, so row-level security and masking apply), and aggregated query results. Raw rows are
never sent.
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.models import AiProvider, AiRequest, Dataset, User
from app.models.common import utcnow

KINDS = ("anthropic", "openai", "openai_compatible")
FEATURES = ("ask", "explain", "formula", "dashboard", "digest")
DEFAULT_URLS = {"anthropic": "https://api.anthropic.com", "openai": "https://api.openai.com/v1"}
SUGGESTED_MODELS = {"anthropic": "claude-sonnet-5-5", "openai": "gpt-5", "openai_compatible": "llama3.3"}


class AiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Reply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


# ------------------------------------------------------------------ sensitivity
def is_sensitive(db: Session, ds: Dataset, _seen: frozenset[int] = frozenset()) -> bool:
    """A dataset is sensitive if it is marked so, or is built (join/union) from a sensitive dataset."""
    from app.query.rls import _parent_mappings

    if ds.sensitive:
        return True
    if ds.id in _seen:
        return False
    seen = _seen | {ds.id}
    return any(is_sensitive(db, parent, seen) for parent, _ in _parent_mappings(db, ds))


# ------------------------------------------------------------------ routing
def allows(p: AiProvider, feature: str) -> bool:
    return not p.features or feature in p.features


def candidates(db: Session, feature: str, datasets: list[Dataset]) -> tuple[list[AiProvider], bool]:
    """(providers that may handle this, whether the data is sensitive)."""
    sensitive = any(is_sensitive(db, d) for d in datasets)
    rows = db.scalars(
        select(AiProvider)
        .where(AiProvider.enabled.is_(True))
        .order_by(AiProvider.is_default.desc(), AiProvider.id)
    ).all()
    out = [p for p in rows if allows(p, feature) and (p.local or not sensitive)]
    return out, sensitive


def choose(db: Session, feature: str, datasets: list[Dataset], provider_id: int | None = None) -> AiProvider:
    found, sensitive = candidates(db, feature, datasets)
    if provider_id is not None:
        found = [p for p in found if p.id == provider_id]
    if found:
        return found[0]
    if not db.scalar(select(func.count()).select_from(AiProvider).where(AiProvider.enabled.is_(True))):
        raise AiError("AI is not set up yet. An admin can add a model in Admin → AI.", 409)
    if sensitive:
        raise AiError(
            "This data is marked sensitive, so it can only go to a self-hosted AI model, and none is set up "
            "for this.",
            409,
        )
    raise AiError("No AI model is set up for this feature.", 409)


def available(db: Session) -> bool:
    return bool(db.scalar(select(func.count()).select_from(AiProvider).where(AiProvider.enabled.is_(True))))


# ------------------------------------------------------------------ limits and log
def check_limit(db: Session, user: User) -> None:
    limit = get_settings().ai_hourly_limit
    since = utcnow() - timedelta(hours=1)
    used = db.scalar(
        select(func.count())
        .select_from(AiRequest)
        .where(AiRequest.user_id == user.id, AiRequest.created_at >= since)
    )
    if limit and (used or 0) >= limit:
        raise AiError("You have used AI a lot in the last hour. Try again in a little while.", 429)


def log(
    db: Session,
    *,
    user: User | None,
    provider: AiProvider | None,
    feature: str,
    dataset_id: int | None,
    started: float,
    reply: Reply | None = None,
    error: str = "",
) -> None:
    db.add(
        AiRequest(
            user_id=user.id if user else None,
            provider_id=provider.id if provider else None,
            provider_name=provider.name if provider else "",
            feature=feature,
            dataset_id=dataset_id,
            status="failed" if error else "ok",
            error=error[:2000],
            input_tokens=reply.input_tokens if reply else 0,
            output_tokens=reply.output_tokens if reply else 0,
            ms=round((time.perf_counter() - started) * 1000),
        )
    )
    db.commit()


# ------------------------------------------------------------------ calling models
def _client() -> httpx.Client:
    return httpx.Client(timeout=get_settings().ai_timeout_seconds)


def _url(p: AiProvider) -> str:
    return (p.base_url or DEFAULT_URLS.get(p.kind, "")).rstrip("/")


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        err = body.get("error", body)
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except ValueError:
        msg = resp.text[:200]
    return f"The AI model returned an error ({resp.status_code}): {msg}"


def call_model(p: AiProvider, system: str, prompt: str, max_tokens: int = 2000) -> Reply:
    """One request to a model. Raises AiError with a readable message on failure."""
    key = decrypt(p.api_key_enc) if p.api_key_enc else ""
    base = _url(p)
    if not base:
        raise AiError("This AI model has no address")
    try:
        with _client() as client:
            if p.kind == "anthropic":
                resp = client.post(
                    f"{base}/v1/messages",
                    headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                    json={
                        "model": p.model,
                        "max_tokens": max_tokens,
                        "system": system,
                        "messages": [{"role": "user", "content": prompt}],
                    },
                )
                if resp.status_code >= 400:
                    raise AiError(_error_text(resp), 502)
                body = resp.json()
                text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
                usage = body.get("usage") or {}
                return Reply(text, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
            headers = {}
            if key:
                headers["Authorization"] = f"Bearer {key}"
                if p.kind == "openai_compatible":
                    headers["api-key"] = key  # Azure OpenAI
            payload: dict[str, Any] = {
                "model": p.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            }
            payload["max_completion_tokens" if p.kind == "openai" else "max_tokens"] = max_tokens
            resp = client.post(f"{base}/chat/completions", headers=headers, json=payload)
            if resp.status_code >= 400:
                raise AiError(_error_text(resp), 502)
            body = resp.json()
            choices = body.get("choices") or [{}]
            text = (choices[0].get("message") or {}).get("content") or ""
            usage = body.get("usage") or {}
            return Reply(text, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0))
    except httpx.TimeoutException as exc:
        raise AiError("The AI model took too long to answer", 504) from exc
    except httpx.HTTPError as exc:
        raise AiError(f"Could not reach the AI model: {exc.__class__.__name__}", 502) from exc
    except ValueError as exc:
        raise AiError("The AI model sent an answer Lumen could not read", 502) from exc


def parse_json(text: str) -> dict:
    """The first JSON object in a model's answer (models sometimes wrap it in ``` fences or prose)."""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.MULTILINE)
    try:
        value = json.loads(cleaned)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        value = json.loads(text[start : i + 1])
                    except ValueError:
                        break
                    if isinstance(value, dict):
                        return value
                    break
        start = text.find("{", start + 1)
    raise AiError("The AI model did not answer in the expected format. Try rephrasing.", 502)


def generate(
    db: Session,
    *,
    user: User | None,
    feature: str,
    datasets: list[Dataset],
    system: str,
    prompt: str,
    max_tokens: int = 2000,
    provider: AiProvider | None = None,
) -> tuple[Reply, AiProvider]:
    """Route, call and log. `provider` skips routing (callers must have checked it with candidates())."""
    if user is not None:
        check_limit(db, user)
    chosen = provider or choose(db, feature, datasets)
    started = time.perf_counter()
    dataset_id = datasets[0].id if datasets else None
    try:
        reply = call_model(chosen, system, prompt, max_tokens)
    except AiError as exc:
        log(
            db,
            user=user,
            provider=chosen,
            feature=feature,
            dataset_id=dataset_id,
            started=started,
            error=exc.message,
        )
        raise
    log(db, user=user, provider=chosen, feature=feature, dataset_id=dataset_id, started=started, reply=reply)
    return reply, chosen
