"""Single sign-on: Microsoft Entra ID, UAE PASS and any standard OpenID Connect provider.

Flow: /start builds the provider's authorize URL and stores state, nonce and the PKCE verifier in a
short-lived signed cookie. /callback checks that cookie, exchanges the code, reads the user's identity
(verified ID token, or the userinfo endpoint for UAE PASS), then finds, links or creates the Lumen user.
"""

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.crypto import decrypt
from app.models import Group, IdentityProvider, Role, User, UserIdentity

UAEPASS_HOSTS = {"staging": "https://stg-id.uaepass.ae", "production": "https://id.uaepass.ae"}
UAEPASS_SCOPE = "urn:uae:digitalid:profile:general"
UAEPASS_ACR = "urn:safelayer:tws:policies:authentication:level:low"
UAEPASS_LEVELS = {"SOP1": 1, "SOP2": 2, "SOP3": 3}

# Tests replace this with httpx.MockTransport
transport: httpx.BaseTransport | None = None


class SSOError(Exception):
    """A sign-in problem whose message is safe to show the user."""


def http() -> httpx.Client:
    return httpx.Client(transport=transport, timeout=20, follow_redirects=False)


@dataclass
class ExternalIdentity:
    subject: str
    email: str
    name: str
    claims: dict[str, Any] = field(default_factory=dict)


def callback_url(provider: IdentityProvider) -> str:
    return f"{get_settings().public_url.rstrip('/')}/api/v1/auth/sso/{provider.id}/callback"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


# ------------------------------------------------------------------ OpenID Connect (Entra + generic)
_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, dict]] = {}
CACHE_SECONDS = 3600


def _get_json_cached(cache: dict, url: str) -> dict:
    hit = cache.get(url)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        with http() as client:
            r = client.get(url)
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise SSOError("The sign-in provider could not be reached. Try again later.") from exc
    cache[url] = (time.time() + CACHE_SECONDS, data)
    return data


class OIDCAdapter:
    uses_pkce = True

    def __init__(self, provider: IdentityProvider):
        self.p = provider

    def discovery_url(self) -> str:
        url = (self.p.config or {}).get("discovery_url", "")
        if not url:
            raise SSOError("This sign-in method is not fully configured (missing discovery URL).")
        return url

    def scopes(self) -> str:
        return (self.p.config or {}).get("scopes") or "openid email profile"

    def meta(self) -> dict:
        return _get_json_cached(_discovery_cache, self.discovery_url())

    def authorize_url(self, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self.p.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scopes(),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.meta()['authorization_endpoint']}?{urlencode(params)}"

    def exchange(self, code: str, redirect_uri: str, verifier: str) -> dict:
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.p.client_id,
            "client_secret": decrypt(self.p.client_secret_enc),
            "code_verifier": verifier,
        }
        try:
            with http() as client:
                r = client.post(self.meta()["token_endpoint"], data=data)
        except httpx.HTTPError as exc:
            raise SSOError("The sign-in provider could not be reached. Try again later.") from exc
        if r.status_code != 200:
            raise SSOError("The sign-in provider rejected the request. Ask an admin to check the settings.")
        return r.json()

    def verify_id_token(self, id_token: str, nonce: str) -> dict:
        meta = self.meta()
        try:
            header = jwt.get_unverified_header(id_token)
            jwks = jwt.PyJWKSet.from_dict(_get_json_cached(_jwks_cache, meta["jwks_uri"]))
            key = next((k for k in jwks.keys if k.key_id == header.get("kid")), None)
            if key is None:  # keys may have rotated: refetch once
                _jwks_cache.pop(meta["jwks_uri"], None)
                jwks = jwt.PyJWKSet.from_dict(_get_json_cached(_jwks_cache, meta["jwks_uri"]))
                key = next((k for k in jwks.keys if k.key_id == header.get("kid")), None)
            if key is None:
                raise SSOError("The sign-in token could not be verified.")
            claims = jwt.decode(
                id_token,
                key=key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "PS256"],
                audience=self.p.client_id,
                issuer=meta["issuer"],
                leeway=60,
            )
        except jwt.PyJWTError as exc:
            raise SSOError("The sign-in token could not be verified.") from exc
        if claims.get("nonce") != nonce:
            raise SSOError("The sign-in response did not match this request. Please try again.")
        return claims

    def identity(self, tokens: dict, nonce: str) -> ExternalIdentity:
        if not tokens.get("id_token"):
            raise SSOError("The sign-in provider did not return an ID token.")
        claims = self.verify_id_token(tokens["id_token"], nonce)
        email = claims.get("email") or claims.get("preferred_username") or ""
        return ExternalIdentity(
            subject=str(claims["sub"]), email=email.lower(), name=claims.get("name", ""), claims=claims
        )

    def logout_url(self, post_logout_redirect: str) -> str | None:
        endpoint = self.meta().get("end_session_endpoint")
        if not endpoint:
            return None
        return f"{endpoint}?{urlencode({'post_logout_redirect_uri': post_logout_redirect})}"


class EntraAdapter(OIDCAdapter):
    """Microsoft Entra ID (Azure AD). Single-tenant only: 'common' would let any Microsoft account in."""

    def discovery_url(self) -> str:
        tenant = (self.p.config or {}).get("tenant_id", "").strip()
        if not tenant or tenant.lower() in {"common", "organizations", "consumers"}:
            raise SSOError("Microsoft sign-in needs your organisation's tenant ID. Ask an admin to set it.")
        return f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"

    def scopes(self) -> str:
        return "openid email profile"

    def identity(self, tokens: dict, nonce: str) -> ExternalIdentity:
        claims = self.verify_id_token(tokens.get("id_token", ""), nonce) if tokens.get("id_token") else None
        if claims is None:
            raise SSOError("Microsoft did not return an ID token.")
        # The issuer check in verify_id_token already pins the tenant (issuer contains the tenant id).
        # oid is the stable, tenant-wide user id. preferred_username is the UPN in a single-tenant app.
        email = (claims.get("email") or claims.get("preferred_username") or "").lower()
        return ExternalIdentity(
            subject=str(claims.get("oid") or claims["sub"]),
            email=email,
            name=claims.get("name", ""),
            claims=claims,
        )


class UAEPassAdapter:
    """UAE PASS: OAuth 2.0 authorization code flow.

    Identity comes from the userinfo endpoint (UAE PASS issues no ID token and does not use PKCE).
    """

    uses_pkce = False

    def __init__(self, provider: IdentityProvider):
        self.p = provider
        env = (provider.config or {}).get("environment", "staging")
        self.base = UAEPASS_HOSTS.get(env, UAEPASS_HOSTS["staging"])

    def authorize_url(self, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
        cfg = self.p.config or {}
        params = {
            "response_type": "code",
            "client_id": self.p.client_id,
            "scope": UAEPASS_SCOPE,
            "state": state,
            "redirect_uri": redirect_uri,
            "acr_values": cfg.get("acr_values") or UAEPASS_ACR,
            "ui_locales": cfg.get("ui_locales") or "en",
        }
        return f"{self.base}/idshub/authorize?{urlencode(params)}"

    def exchange(self, code: str, redirect_uri: str, verifier: str) -> dict:
        params = {"grant_type": "authorization_code", "redirect_uri": redirect_uri, "code": code}
        try:
            with http() as client:
                r = client.post(
                    f"{self.base}/idshub/token",
                    params=params,  # UAE PASS documents the parameters in the query string
                    auth=(self.p.client_id, decrypt(self.p.client_secret_enc)),
                    headers={"Content-Type": "multipart/form-data; charset=UTF-8"},
                )
        except httpx.HTTPError as exc:
            raise SSOError("UAE PASS could not be reached. Try again later.") from exc
        if r.status_code != 200:
            raise SSOError("UAE PASS rejected the sign-in. Please try again.")
        return r.json()

    def identity(self, tokens: dict, nonce: str) -> ExternalIdentity:
        try:
            with http() as client:
                r = client.get(
                    f"{self.base}/idshub/userinfo",
                    headers={"Authorization": f"Bearer {tokens.get('access_token', '')}"},
                )
                r.raise_for_status()
                info = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SSOError("Your UAE PASS profile could not be read. Please try again.") from exc

        minimum = (self.p.config or {}).get("min_user_type", "SOP1")
        level = UAEPASS_LEVELS.get(str(info.get("userType", "")).upper(), 0)
        if level < UAEPASS_LEVELS.get(minimum, 1):
            raise SSOError(
                "Your UAE PASS account needs to be verified to sign in here. "
                "Upgrade it in the UAE PASS app, then try again."
            )
        subject = info.get("uuid") or info.get("sub")
        if not subject:
            raise SSOError("UAE PASS did not return a user id.")
        name = info.get("fullnameEN") or " ".join(
            filter(None, [info.get("firstnameEN"), info.get("lastnameEN")])
        )
        return ExternalIdentity(
            subject=str(subject), email=(info.get("email") or "").lower(), name=name, claims=info
        )

    def logout_url(self, post_logout_redirect: str) -> str | None:
        return f"{self.base}/idshub/logout?{urlencode({'redirect_uri': post_logout_redirect})}"


ADAPTERS = {"entra": EntraAdapter, "uaepass": UAEPassAdapter, "oidc": OIDCAdapter}


def adapter_for(provider: IdentityProvider):
    cls = ADAPTERS.get(provider.type)
    if cls is None:
        raise SSOError("Unknown sign-in method.")
    return cls(provider)


# ------------------------------------------------------------------ account resolution
def _claim(claims: dict, name: str) -> Any:
    """Read a claim; dotted names reach into nested objects (e.g. 'address.locality')."""
    value: Any = claims
    for part in name.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _apply_mappings(db: Session, provider: IdentityProvider, user: User, claims: dict) -> None:
    attrs = dict(user.attributes or {})
    for attr, claim_name in (provider.attribute_map or {}).items():
        value = _claim(claims, claim_name)
        if value not in (None, "", []):
            attrs[attr] = value
    user.attributes = attrs

    if provider.group_map:
        claim_values = claims.get("groups") or []
        if isinstance(claim_values, str):
            claim_values = [claim_values]
        wanted = {provider.group_map[v] for v in claim_values if v in provider.group_map}
        source = provider.type
        groups = []
        for name in sorted(wanted):
            group = db.scalar(select(Group).where(func.lower(Group.name) == name.lower()))
            if group is None:
                group = Group(name=name, source=source)
                db.add(group)
            groups.append(group)
        # Replace only the groups managed by this provider; keep groups set by admins
        mapped_names = {n.lower() for n in provider.group_map.values()}
        kept = [g for g in user.groups if g.name.lower() not in mapped_names]
        user.groups = kept + groups


def resolve_user(db: Session, provider: IdentityProvider, ident: ExternalIdentity) -> User:
    now = datetime.now(UTC)
    link = db.scalar(
        select(UserIdentity).where(
            UserIdentity.provider_id == provider.id, UserIdentity.subject == ident.subject
        )
    )
    if link:
        user = db.get(User, link.user_id)
    else:
        user = None
        if ident.email and provider.allowed_domains:
            domain = ident.email.rsplit("@", 1)[-1]
            if domain not in {d.lower().lstrip("@") for d in provider.allowed_domains}:
                raise SSOError("Your email domain is not allowed to sign in here.")
        if ident.email and provider.link_by_email:
            user = db.scalar(select(User).where(User.email == ident.email))
        if user is None:
            if not provider.auto_create:
                raise SSOError("You do not have an account yet. Ask an administrator to invite you.")
            if not ident.email:
                raise SSOError("Your account has no email address, so it cannot be created automatically.")
            role = provider.default_role if provider.default_role in Role.__members__ else Role.viewer.value
            user = User(email=ident.email, name=ident.name, role=role, auth_provider=provider.type)
            db.add(user)
            db.flush()
        link = UserIdentity(
            user_id=user.id, provider_id=provider.id, subject=ident.subject, email=ident.email
        )
        db.add(link)

    if user is None or not user.is_active:
        raise SSOError("Your account is disabled. Contact an administrator.")
    if not user.name and ident.name:
        user.name = ident.name
    link.email = ident.email or link.email
    link.last_login_at = now
    user.last_login_at = now
    _apply_mappings(db, provider, user, ident.claims)
    return user
