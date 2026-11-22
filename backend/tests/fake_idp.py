"""A fake identity provider (Entra-style OIDC and UAE PASS) served through httpx.MockTransport."""

import base64
import json
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

TENANT = "11111111-2222-3333-4444-555555555555"
ENTRA_BASE = f"https://login.microsoftonline.com/{TENANT}/v2.0"
ENTRA_ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"


class FakeIdP:
    def __init__(self, client_id: str = "lumen-client", client_secret: str = "s3cret"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key-1"
        self.codes: dict[str, dict] = {}  # code -> {"claims"|"userinfo", "nonce", "verifier"?}
        self.last_token_request: httpx.Request | None = None
        self.id_token_overrides: dict = {}
        self.sign_with = None  # set to another private key to simulate a forged token

    # ---- what a test calls
    def issue_code(self, nonce: str, claims: dict | None = None, userinfo: dict | None = None) -> str:
        code = secrets.token_urlsafe(8)
        self.codes[code] = {"nonce": nonce, "claims": claims or {}, "userinfo": userinfo or {}}
        return code

    def jwks(self) -> dict:
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}

    def id_token(self, claims: dict, nonce: str) -> str:
        now = int(time.time())
        payload = {
            "iss": ENTRA_ISSUER,
            "aud": self.client_id,
            "iat": now,
            "nbf": now,
            "exp": now + 3600,
            "nonce": nonce,
            "tid": TENANT,
            **claims,
            **self.id_token_overrides,
        }
        return jwt.encode(payload, self.sign_with or self.key, algorithm="RS256", headers={"kid": self.kid})

    # ---- the HTTP side
    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        path = request.url.path
        if url.startswith(ENTRA_BASE) and path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ENTRA_ISSUER,
                    "authorization_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/authorize",
                    "token_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
                    "jwks_uri": f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys",
                    "end_session_endpoint": f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/logout",
                },
            )
        if path.endswith("/discovery/v2.0/keys"):
            return httpx.Response(200, json=self.jwks())
        if path.endswith("/oauth2/v2.0/token"):
            self.last_token_request = request
            form = parse_qs(request.content.decode())
            entry = self.codes.pop(form.get("code", [""])[0], None)
            if not entry or form.get("client_secret", [""])[0] != self.client_secret:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": "at",
                    "token_type": "Bearer",
                    "id_token": self.id_token(entry["claims"], entry["nonce"]),
                },
            )
        if "uaepass.ae" in url and path == "/idshub/token":
            self.last_token_request = request
            params = parse_qs(urlparse(url).query)
            auth = request.headers.get("authorization", "")
            expected = "Basic " + base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            entry = self.codes.get(params.get("code", [""])[0])
            if auth != expected or not entry:
                return httpx.Response(400, json={"error": "invalid_grant"})
            token = secrets.token_urlsafe(8)
            self.codes[token] = entry
            return httpx.Response(
                200, json={"access_token": token, "token_type": "Bearer", "expires_in": 3600}
            )
        if "uaepass.ae" in url and path == "/idshub/userinfo":
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            entry = self.codes.get(token)
            if not entry:
                return httpx.Response(401)
            return httpx.Response(200, json=entry["userinfo"])
        return httpx.Response(404)
