"""The signed-in side of connecting AI apps (MCP) to Lumen, and managing those connections.

GET    /oauth/request                   check an app's sign-in request (the consent page)
POST   /oauth/approve | /oauth/deny     the person's answer -> where to send the browser back
GET    /me/connected-apps               apps connected to my account
DELETE /me/connected-apps/{client_id}   disconnect one
GET|PUT /admin/mcp                      turn the MCP server on or off, who may connect, all connections
DELETE /admin/mcp/connections/{user_id}/{client_id}
"""

from typing import Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_admin
from app.db import get_db
from app.models import User
from app.services import audit, oauth

router = APIRouter(tags=["mcp"])


class AuthorizeIn(BaseModel):
    client_id: str = ""
    redirect_uri: str = ""
    response_type: str = ""
    code_challenge: str = ""
    code_challenge_method: str = ""
    state: str | None = None
    scope: str | None = None
    resource: str | None = None


def _bad(exc: oauth.OAuthError) -> HTTPException:
    return HTTPException(400, exc.description)


@router.get("/oauth/request")
def check_request(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    q = AuthorizeIn(**{k: v for k, v in request.query_params.items() if k in AuthorizeIn.model_fields})
    if not oauth.settings(db)["enabled"]:
        raise HTTPException(403, "Connecting AI apps is turned off. An admin can turn it on in Admin → AI.")
    try:
        client = oauth.check_request(db, q.model_dump())
    except oauth.OAuthError as exc:
        raise _bad(exc) from exc
    return {
        "client_name": client.name,
        "redirect_host": urlparse(q.redirect_uri).netloc,
        "allowed": oauth.may_connect(db, user),
        "user": {"name": user.name, "email": user.email},
    }


@router.post("/oauth/approve")
def approve(
    body: AuthorizeIn, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    try:
        redirect = oauth.approve(db, user, body.model_dump())
    except oauth.OAuthError as exc:
        raise _bad(exc) from exc
    if oauth.may_connect(db, user):
        client = oauth.client_for(db, body.client_id, body.redirect_uri)
        audit.record(
            db, "mcp.connect", user=user, request=request, object_type="oauth_client",
            object_id=body.client_id, details={"app": client.name},
        )  # fmt: skip
    return {"redirect": redirect}


@router.post("/oauth/deny")
def deny(body: AuthorizeIn, db: Session = Depends(get_db), _: User = Depends(current_user)):
    try:
        return {"redirect": oauth.deny(db, body.model_dump())}
    except oauth.OAuthError as exc:
        raise _bad(exc) from exc


@router.get("/me/connected-apps")
def my_apps(db: Session = Depends(get_db), user: User = Depends(current_user)):
    return oauth.connections(db, user.id)


@router.delete("/me/connected-apps/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(
    client_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    if oauth.revoke_app(db, user.id, client_id):
        audit.record(
            db, "mcp.disconnect", user=user, request=request, object_type="oauth_client", object_id=client_id
        )


# ------------------------------------------------------------------ admin
class McpSettingsIn(BaseModel):
    enabled: bool
    min_role: Literal["viewer", "explorer", "creator", "admin"] = "explorer"


def _admin_view(db: Session) -> dict:
    conns = oauth.connections(db)
    ids = {c["user_id"] for c in conns}
    people = {u.id: u for u in db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
    for c in conns:
        u = people.get(c["user_id"])
        c["user"] = (u.name or u.email) if u else ""
        c["email"] = u.email if u else ""
    return {**oauth.settings(db), "server_url": oauth.resource_url(), "connections": conns}


@router.get("/admin/mcp")
def get_mcp(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return _admin_view(db)


@router.put("/admin/mcp")
def put_mcp(
    body: McpSettingsIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    oauth.save_settings(db, body.enabled, body.min_role, admin)
    audit.record(db, "mcp.settings", user=admin, request=request, details=body.model_dump(), commit=False)
    db.commit()
    return _admin_view(db)


@router.delete("/admin/mcp/connections/{user_id}/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_disconnect(
    user_id: int,
    client_id: str,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if oauth.revoke_app(db, user_id, client_id):
        audit.record(
            db, "mcp.disconnect", user=admin, request=request, object_type="oauth_client",
            object_id=client_id, details={"user_id": user_id},
        )  # fmt: skip
