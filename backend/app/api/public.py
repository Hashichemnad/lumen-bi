"""Endpoints outside /api/v1 that MCP clients (such as Claude) call directly.

GET  /.well-known/oauth-protected-resource[/mcp]   which authorization server protects /mcp (RFC 9728)
GET  /.well-known/oauth-authorization-server       the OAuth server's metadata (RFC 8414)
POST /oauth/register                               dynamic client registration (RFC 7591), public clients
POST /oauth/token                                  authorization_code (PKCE S256) and refresh_token grants
POST /oauth/revoke                                 revoke a token (RFC 7009)
POST /mcp                                          the MCP server (Streamable HTTP, stateless, JSON only)

The browser part of the sign-in (/oauth/authorize) is a page of the web app; it calls /api/v1/oauth/*.
"""

import json
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from app.core.ratelimit import limit
from app.db import get_db
from app.services import mcp, oauth

router = APIRouter(include_in_schema=False)
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _oauth_error(exc: oauth.OAuthError) -> JSONResponse:
    return JSONResponse(
        {"error": exc.error, "error_description": exc.description}, status_code=exc.status, headers=NO_STORE
    )


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def protected_resource():
    return {
        "resource": oauth.resource_url(),
        "authorization_servers": [oauth.base_url()],
        "scopes_supported": [oauth.SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Lumen BI",
    }


@router.get("/.well-known/oauth-authorization-server")
def authorization_server():
    base = oauth.base_url()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [oauth.SCOPE],
        "authorization_response_iss_parameter_supported": True,
    }


async def _body(request: Request) -> dict:
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


def _token_key(request: Request) -> str:
    import hashlib

    return hashlib.sha256(request.headers.get("authorization", "").encode()).hexdigest()[:32]


@router.post("/oauth/register", dependencies=[Depends(limit("oauth_register", 10, 60))])
async def register(request: Request, db: Session = Depends(get_db)):
    try:
        client = oauth.register(db, await _body(request))
    except oauth.OAuthError as exc:
        return _oauth_error(exc)
    return JSONResponse(
        {
            "client_id": client.client_id,
            "client_name": client.name,
            "redirect_uris": client.redirect_uris,
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_id_issued_at": int(client.created_at.timestamp()),
        },
        status_code=201,
        headers=NO_STORE,
    )


@router.post("/oauth/token", dependencies=[Depends(limit("oauth_token", 60, 60))])
async def token(request: Request, db: Session = Depends(get_db)):
    try:
        return JSONResponse(oauth.exchange(db, await _body(request)), headers=NO_STORE)
    except oauth.OAuthError as exc:
        return _oauth_error(exc)


@router.post("/oauth/revoke", dependencies=[Depends(limit("oauth_token", 60, 60))])
async def revoke(request: Request, db: Session = Depends(get_db)):
    value = str((await _body(request)).get("token") or "")
    if value:
        oauth.revoke_token(db, value)
    return Response(status_code=200, headers=NO_STORE)


# ------------------------------------------------------------------ MCP
def _challenge(description: str = "") -> JSONResponse:
    meta = f"{oauth.base_url()}/.well-known/oauth-protected-resource"
    header = f'Bearer resource_metadata="{meta}", scope="{oauth.SCOPE}"'
    if description:
        header += f', error="invalid_token", error_description="{description}"'
    return JSONResponse({"error": "unauthorized"}, status_code=401, headers={"WWW-Authenticate": header})


def _origin_ok(request: Request) -> bool:
    """Browsers send Origin; only our own pages may call /mcp from a browser (DNS-rebinding protection)."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    own = urlparse(oauth.base_url())
    o = urlparse(origin)
    return (o.scheme, o.netloc) == (own.scheme, own.netloc)


# Per sign-in (Claude's servers share addresses between many people)
@router.post("/mcp", dependencies=[Depends(limit("mcp", 240, 60, key=_token_key))])
async def mcp_post(request: Request, db: Session = Depends(get_db)):
    if not _origin_ok(request):
        return JSONResponse({"error": "Origin not allowed"}, status_code=403)
    if not oauth.settings(db)["enabled"]:
        return JSONResponse({"error": "The Lumen MCP server is turned off"}, status_code=404)
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return _challenge()
    found = oauth.user_for_token(db, auth[7:].strip())
    if found is None:
        return _challenge("The access token is not valid or has expired")
    user, tok = found
    try:
        msg = json.loads(await request.body())
    except ValueError:
        return JSONResponse(mcp._error(None, -32700, "Parse error"), status_code=400)
    if isinstance(msg, list):
        return JSONResponse(mcp._error(None, -32600, "Batches are not supported"), status_code=400)
    answer = mcp.handle(db, user, tok.client_id, msg, request)
    if answer is None:
        return Response(status_code=202)
    return JSONResponse(jsonable_encoder(answer))


@router.get("/mcp")
@router.delete("/mcp")
def mcp_other():
    # Stateless server: no event stream and no sessions to end
    return Response(status_code=405, headers={"Allow": "POST"})
