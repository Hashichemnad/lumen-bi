import logging
import secrets
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.api.v1.auth import set_session_cookies
from app.core.config import get_settings
from app.core.crypto import encrypt
from app.core.security import create_state_token, decode_state_token
from app.db import get_db
from app.models import IdentityProvider, User
from app.schemas import ProviderIn, ProviderOut, ProviderPublic
from app.services import audit
from app.services.sso import SSOError, adapter_for, callback_url, pkce_pair, resolve_user

log = logging.getLogger("lumen.sso")
STATE_COOKIE = "lumen_sso"
STATE_PATH = "/api/v1/auth/sso"

router = APIRouter(tags=["sso"])


def _safe_next(value: str | None) -> str:
    """Only allow same-site relative paths (prevents open redirects)."""
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


def _login_error(message: str) -> RedirectResponse:
    resp = RedirectResponse(f"/login?sso_error={quote(message)}", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(STATE_COOKIE, path=STATE_PATH)
    return resp


# ------------------------------------------------------------------ public sign-in flow
@router.get("/auth/providers", response_model=list[ProviderPublic])
def enabled_providers(db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(IdentityProvider).where(IdentityProvider.enabled.is_(True)).order_by(IdentityProvider.id)
        )
    )


@router.get("/auth/sso/{provider_id}/start")
def sso_start(provider_id: int, next: str = "/", db: Session = Depends(get_db)):
    provider = db.get(IdentityProvider, provider_id)
    if not provider or not provider.enabled:
        return _login_error("This sign-in method is not available.")
    try:
        adapter = adapter_for(provider)
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair() if adapter.uses_pkce else ("", "")
        url = adapter.authorize_url(callback_url(provider), state, nonce, challenge)
    except SSOError as exc:
        return _login_error(str(exc))

    s = get_settings()
    cookie = create_state_token(
        {"pid": provider.id, "state": state, "nonce": nonce, "verifier": verifier, "next": _safe_next(next)},
        s.sso_state_minutes,
    )
    resp = RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
    # SameSite=Lax: sent back on the provider's top-level redirect to our callback
    resp.set_cookie(
        STATE_COOKIE,
        cookie,
        max_age=s.sso_state_minutes * 60,
        path=STATE_PATH,
        httponly=True,
        secure=s.secure_cookies,
        samesite="lax",
    )
    return resp


@router.get("/auth/sso/{provider_id}/callback")
def sso_callback(
    provider_id: int,
    request: Request,
    code: str = "",
    state: str = "",
    error: str = "",
    error_description: str = "",
    db: Session = Depends(get_db),
):
    if error:
        log.info("SSO provider %s returned error %s: %s", provider_id, error, error_description)
        if error in {"access_denied", "cancelledOnApp", "login_required"}:
            return _login_error("Sign-in was cancelled.")
        return _login_error("The sign-in provider reported a problem. Please try again.")

    saved = decode_state_token(request.cookies.get(STATE_COOKIE, ""))
    if (
        not saved
        or saved.get("pid") != provider_id
        or not state
        or not secrets.compare_digest(str(saved.get("state", "")), state)
    ):
        return _login_error("Your sign-in session expired or did not match. Please try again.")
    provider = db.get(IdentityProvider, provider_id)
    if not provider or not provider.enabled or not code:
        return _login_error("This sign-in method is not available.")

    try:
        adapter = adapter_for(provider)
        tokens = adapter.exchange(code, callback_url(provider), saved.get("verifier", ""))
        ident = adapter.identity(tokens, saved.get("nonce", ""))
        user = resolve_user(db, provider, ident)
    except SSOError as exc:
        db.rollback()
        audit.record(
            db,
            "auth.sso_failed",
            request=request,
            object_type="identity_provider",
            object_id=provider_id,
            details={"reason": str(exc)},
        )
        return _login_error(str(exc))

    audit.record(
        db,
        "auth.login",
        user=user,
        request=request,
        details={"method": provider.type, "provider": provider.name},
    )
    resp = RedirectResponse(saved.get("next") or "/", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(STATE_COOKIE, path=STATE_PATH)
    set_session_cookies(resp, user, provider.id)
    return resp


# ------------------------------------------------------------------ admin settings
def _out(p: IdentityProvider) -> ProviderOut:
    out = ProviderOut.model_validate(p)
    out.has_secret = bool(p.client_secret_enc)
    out.callback_url = callback_url(p)
    return out


def _validate(body: ProviderIn) -> None:
    if body.enabled and not body.client_id:
        raise HTTPException(422, "A client ID is needed before this sign-in method can be turned on")
    if body.type == "entra" and body.enabled:
        tenant = str(body.config.get("tenant_id", "")).strip().lower()
        if not tenant or tenant in {"common", "organizations", "consumers"}:
            raise HTTPException(422, "Enter your organisation's Microsoft tenant ID (not 'common')")
    if body.type == "uaepass" and body.config.get("environment", "staging") not in {"staging", "production"}:
        raise HTTPException(422, "UAE PASS environment must be 'staging' or 'production'")
    if body.type == "oidc" and body.enabled:
        url = str(body.config.get("discovery_url", ""))
        local = url.startswith(("http://localhost", "http://127.0.0.1"))  # tools/dev_idp.py for local trials
        if not (url.startswith("https://") or local):
            raise HTTPException(
                422, "Enter the provider's discovery URL (https://…/.well-known/openid-configuration)"
            )


def _apply(p: IdentityProvider, body: ProviderIn) -> None:
    p.type = body.type
    p.name = body.name.strip()
    p.enabled = body.enabled
    p.client_id = body.client_id.strip()
    if body.client_secret is not None:
        p.client_secret_enc = encrypt(body.client_secret) if body.client_secret else ""
    p.config = body.config
    p.attribute_map = {k.strip(): v.strip() for k, v in body.attribute_map.items() if k.strip() and v.strip()}
    p.group_map = {k.strip(): v.strip() for k, v in body.group_map.items() if k.strip() and v.strip()}
    p.auto_create = body.auto_create
    p.default_role = body.default_role.value
    p.allowed_domains = [d.strip().lower().lstrip("@") for d in body.allowed_domains if d.strip()]
    p.link_by_email = body.link_by_email


@router.get("/admin/identity-providers", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [_out(p) for p in db.scalars(select(IdentityProvider).order_by(IdentityProvider.id))]


@router.post("/admin/identity-providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
def create_provider(
    body: ProviderIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    _validate(body)
    p = IdentityProvider(type=body.type, name=body.name)
    _apply(p, body)
    db.add(p)
    db.flush()
    audit.record(
        db,
        "sso.create",
        user=admin,
        request=request,
        object_type="identity_provider",
        object_id=p.id,
        details={"type": p.type, "name": p.name, "enabled": p.enabled},
    )
    return _out(p)


@router.put("/admin/identity-providers/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: int,
    body: ProviderIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    p = db.get(IdentityProvider, provider_id)
    if not p:
        raise HTTPException(404, "Sign-in method not found")
    if body.type != p.type:
        raise HTTPException(422, "The type of a sign-in method cannot be changed; create a new one")
    _validate(body)
    _apply(p, body)
    audit.record(
        db,
        "sso.update",
        user=admin,
        request=request,
        object_type="identity_provider",
        object_id=p.id,
        details={"enabled": p.enabled, "secret_changed": body.client_secret is not None},
    )
    return _out(p)


@router.delete("/admin/identity-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    provider_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    p = db.get(IdentityProvider, provider_id)
    if not p:
        raise HTTPException(404, "Sign-in method not found")
    name = p.name
    db.delete(p)
    audit.record(
        db,
        "sso.delete",
        user=admin,
        request=request,
        object_type="identity_provider",
        object_id=provider_id,
        details={"name": name},
    )
