"""A tiny OpenID Connect provider for trying single sign-on locally. NOT for production.

Run:   python tools/dev_idp.py            (listens on http://localhost:9000)
Then in Lumen: Admin → Sign-in methods → Other OpenID Connect
       Discovery URL: http://localhost:9000/.well-known/openid-configuration
       Client ID: lumen-dev   Client secret: dev-secret
The sign-in page offers a few test people; pick one to sign in as them.
"""

import base64
import hashlib
import html
import json
import secrets
import time
from urllib.parse import urlencode

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

ISSUER = "http://localhost:9000"
CLIENT_ID, CLIENT_SECRET = "lumen-dev", "dev-secret"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KID = "dev-key"

PEOPLE = {
    "sara": {
        "sub": "dev-sara",
        "email": "sara@example.ae",
        "name": "Sara Ahmed",
        "department": "Finance",
        "region": ["UAE", "KSA"],
        "groups": ["grp-finance"],
    },
    "omar": {
        "sub": "dev-omar",
        "email": "omar@example.ae",
        "name": "Omar Khan",
        "department": "Sales",
        "region": ["UAE"],
        "groups": ["grp-sales"],
    },
    "lina": {
        "sub": "dev-lina",
        "email": "lina@other.com",
        "name": "Lina Haddad",
        "department": "Ops",
        "region": ["QAT"],
        "groups": [],
    },
}
BUTTON_STYLE = "display:block;margin:8px 0;padding:10px 16px;width:280px"
codes: dict[str, dict] = {}
tokens: dict[str, dict] = {}

app = FastAPI(title="Lumen dev identity provider")


@app.get("/.well-known/openid-configuration")
def discovery():
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
        "jwks_uri": f"{ISSUER}/jwks",
        "end_session_endpoint": f"{ISSUER}/logout",
        "response_types_supported": ["code"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "code_challenge_methods_supported": ["S256"],
    }


@app.get("/jwks")
def jwks():
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key()))
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


@app.get("/authorize", response_class=HTMLResponse)
def authorize(client_id: str, redirect_uri: str, state: str, nonce: str = "", code_challenge: str = ""):
    if client_id != CLIENT_ID:
        raise HTTPException(400, "unknown client_id")
    hidden = "".join(
        f'<input type="hidden" name="{k}" value="{html.escape(v)}">'
        for k, v in {
            "redirect_uri": redirect_uri,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
        }.items()
    )
    buttons = "".join(
        f'<button name="person" value="{key}" style="{BUTTON_STYLE}">'
        f"Sign in as {html.escape(p['name'])} ({html.escape(p['email'])})</button>"
        for key, p in PEOPLE.items()
    )
    return f"""<html><body style="font-family:sans-serif;padding:40px">
      <h2>Dev identity provider</h2><p>Testing only. Choose who to sign in as:</p>
      <form method="post" action="/authorize">{hidden}{buttons}
      <button name="person" value="__cancel" style="margin-top:16px">Cancel</button></form></body></html>"""


@app.post("/authorize")
def authorize_submit(
    person: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(...),
    nonce: str = Form(""),
    code_challenge: str = Form(""),
):
    if person == "__cancel":
        return RedirectResponse(
            f"{redirect_uri}?{urlencode({'error': 'access_denied', 'state': state})}", 303
        )
    code = secrets.token_urlsafe(16)
    codes[code] = {"person": PEOPLE[person], "nonce": nonce, "challenge": code_challenge}
    return RedirectResponse(f"{redirect_uri}?{urlencode({'code': code, 'state': state})}", 303)


@app.post("/token")
def token(
    code: str = Form(...),
    client_id: str = Form(...),
    client_secret: str = Form(...),
    code_verifier: str = Form(""),
    grant_type: str = Form(...),
    redirect_uri: str = Form(""),
):
    entry = codes.pop(code, None)
    if (
        not entry
        or client_id != CLIENT_ID
        or client_secret != CLIENT_SECRET
        or grant_type != "authorization_code"
    ):
        raise HTTPException(400, "invalid_grant")
    if entry["challenge"]:
        digest = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).rstrip(b"=").decode()
        )
        if digest != entry["challenge"]:
            raise HTTPException(400, "invalid_grant (PKCE)")
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "iat": now,
        "exp": now + 600,
        "nonce": entry["nonce"],
        **entry["person"],
    }
    access = secrets.token_urlsafe(16)
    tokens[access] = entry["person"]
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": 600,
        "id_token": jwt.encode(claims, KEY, algorithm="RS256", headers={"kid": KID}),
    }


@app.get("/logout", response_class=HTMLResponse)
def logout(post_logout_redirect_uri: str = "/"):
    target = html.escape(post_logout_redirect_uri)
    return (
        f'<html><body style="font-family:sans-serif;padding:40px"><p>Signed out of the dev provider.</p>'
        f'<a href="{target}">Back to Lumen</a></body></html>'
    )


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9000)
