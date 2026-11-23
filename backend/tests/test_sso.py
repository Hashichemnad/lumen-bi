from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.db import SessionLocal
from app.models import IdentityProvider, Role
from app.services import sso
from tests.conftest import Client, make_user
from tests.fake_idp import TENANT, FakeIdP

API = "/api/v1"


@pytest.fixture
def idp():
    fake = FakeIdP()
    sso.transport = httpx.MockTransport(fake.handler)
    sso._discovery_cache.clear()
    sso._jwks_cache.clear()
    yield fake
    sso.transport = None


def create_provider(admin_client, **overrides) -> dict:
    body = {
        "type": "entra",
        "name": "Microsoft",
        "enabled": True,
        "client_id": "lumen-client",
        "client_secret": "s3cret",
        "config": {"tenant_id": TENANT},
    }
    body.update(overrides)
    r = admin_client.post(f"{API}/admin/identity-providers", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def start(client, provider_id: int, next_path: str = "/") -> dict:
    r = client.get(f"{API}/auth/sso/{provider_id}/start", params={"next": next_path}, follow_redirects=False)
    assert r.status_code == 303, r.text
    location = r.headers["location"]
    q = {k: v[0] for k, v in parse_qs(urlparse(location).query).items()}
    q["_location"] = location
    return q


def callback(client, provider_id: int, code: str, state: str):
    return client.get(
        f"{API}/auth/sso/{provider_id}/callback",
        params={"code": code, "state": state},
        follow_redirects=False,
    )


def entra_login(client, idp, provider_id: int, claims: dict, next_path: str = "/"):
    q = start(client, provider_id, next_path)
    code = idp.issue_code(q["nonce"], claims=claims)
    return callback(client, provider_id, code, q["state"])


def error_of(resp) -> str:
    assert resp.status_code == 303
    loc = resp.headers["location"]
    assert loc.startswith("/login?sso_error="), loc
    return parse_qs(urlparse(loc).query)["sso_error"][0]


# ------------------------------------------------------------------ admin settings
def test_admin_crud_and_secret_never_returned(admin_client):
    p = create_provider(admin_client)
    assert p["has_secret"] is True and "client_secret" not in p
    assert p["callback_url"].endswith(f"/api/v1/auth/sso/{p['id']}/callback")
    with SessionLocal() as s:
        stored = s.get(IdentityProvider, p["id"]).client_secret_enc
        assert stored and "s3cret" not in stored  # encrypted at rest

    # update without a secret keeps the stored one
    body = {
        "type": "entra",
        "name": "Microsoft 365",
        "enabled": True,
        "client_id": "lumen-client",
        "config": {"tenant_id": TENANT},
    }
    r = admin_client.put(f"{API}/admin/identity-providers/{p['id']}", json=body)
    assert r.json()["name"] == "Microsoft 365" and r.json()["has_secret"] is True
    assert admin_client.get(f"{API}/admin/identity-providers").json()[0]["name"] == "Microsoft 365"
    assert admin_client.delete(f"{API}/admin/identity-providers/{p['id']}").status_code == 204


def test_provider_validation(admin_client):
    r = admin_client.post(
        f"{API}/admin/identity-providers",
        json={
            "type": "entra",
            "name": "MS",
            "enabled": True,
            "client_id": "x",
            "config": {"tenant_id": "common"},
        },
    )
    assert r.status_code == 422
    r = admin_client.post(
        f"{API}/admin/identity-providers", json={"type": "entra", "name": "MS", "enabled": True}
    )
    assert r.status_code == 422  # needs client id
    r = admin_client.post(f"{API}/admin/identity-providers", json={"type": "saml", "name": "X"})
    assert r.status_code == 422


def test_non_admin_cannot_manage_providers(client):
    make_user("creator@test.com", Role.creator)
    client.login("creator@test.com")
    assert client.get(f"{API}/admin/identity-providers").status_code == 403
    assert (
        client.post(f"{API}/admin/identity-providers", json={"type": "entra", "name": "x"}).status_code == 403
    )


def test_public_list_shows_only_enabled(admin_client, client):
    create_provider(admin_client, name="Microsoft")
    create_provider(admin_client, type="uaepass", name="UAE PASS", enabled=False, config={})
    admin_client.cookies.clear()
    listed = client.get(f"{API}/auth/providers").json()
    assert [p["name"] for p in listed] == ["Microsoft"]
    assert set(listed[0]) == {"id", "type", "name"}  # no settings leak


# ------------------------------------------------------------------ Microsoft Entra flow
def test_entra_invite_only_rejects_unknown_user(admin_client, idp):
    p = create_provider(admin_client)
    admin_client.cookies.clear()
    r = entra_login(admin_client, idp, p["id"], {"sub": "s1", "oid": "o1", "email": "stranger@corp.ae"})
    assert "invite" in error_of(r)


def test_entra_links_existing_user_by_email_then_by_identity(admin_client, idp):
    p = create_provider(admin_client)
    make_user("sara@corp.ae", Role.creator)
    admin_client.cookies.clear()

    r = entra_login(
        admin_client,
        idp,
        p["id"],
        {"sub": "s1", "oid": "oid-sara", "email": "Sara@Corp.ae", "name": "Sara"},
        next_path="/admin/users",
    )
    assert r.status_code == 303 and r.headers["location"] == "/admin/users"
    me = admin_client.get(f"{API}/auth/me").json()
    assert me["email"] == "sara@corp.ae" and me["role"] == "creator"
    assert me["sign_in_methods"][0]["provider_name"] == "Microsoft"

    # Second sign-in: matched by the stable object id even if the email changed
    admin_client.cookies.clear()
    entra_login(admin_client, idp, p["id"], {"sub": "s1", "oid": "oid-sara", "email": "sara.new@corp.ae"})
    assert admin_client.get(f"{API}/auth/me").json()["email"] == "sara@corp.ae"


def test_entra_auto_create_with_mappings(admin_client, idp):
    p = create_provider(
        admin_client,
        auto_create=True,
        default_role="explorer",
        allowed_domains=["corp.ae"],
        attribute_map={"department": "department", "region": "extension_region"},
        group_map={"gid-fin": "Finance", "gid-dxb": "Dubai branch"},
    )
    admin_client.cookies.clear()
    r = entra_login(
        admin_client,
        idp,
        p["id"],
        {
            "sub": "s2",
            "oid": "oid-ali",
            "email": "ali@corp.ae",
            "name": "Ali Hassan",
            "department": "Sales",
            "extension_region": ["UAE", "KSA"],
            "groups": ["gid-fin", "gid-other"],
        },
    )
    assert r.status_code == 303 and r.headers["location"] == "/"
    me = admin_client.get(f"{API}/auth/me").json()
    assert me["role"] == "explorer" and me["name"] == "Ali Hassan"
    assert me["attributes"] == {"department": "Sales", "region": ["UAE", "KSA"]}
    assert [g["name"] for g in me["groups"]] == ["Finance"]

    # Group membership follows Microsoft on the next sign-in
    admin_client.cookies.clear()
    entra_login(
        admin_client,
        idp,
        p["id"],
        {"sub": "s2", "oid": "oid-ali", "email": "ali@corp.ae", "groups": ["gid-dxb"]},
    )
    assert [g["name"] for g in admin_client.get(f"{API}/auth/me").json()["groups"]] == ["Dubai branch"]


def test_entra_domain_restriction(admin_client, idp):
    p = create_provider(admin_client, auto_create=True, allowed_domains=["corp.ae"])
    admin_client.cookies.clear()
    r = entra_login(admin_client, idp, p["id"], {"sub": "s", "oid": "o", "email": "x@gmail.com"})
    assert "domain" in error_of(r)


def test_entra_uses_pkce_and_sends_secret(admin_client, idp):
    p = create_provider(admin_client, auto_create=True)
    admin_client.cookies.clear()
    q = start(admin_client, p["id"])
    assert q["code_challenge_method"] == "S256" and q["code_challenge"] and q["nonce"]
    code = idp.issue_code(q["nonce"], claims={"sub": "s", "oid": "o", "email": "p@corp.ae"})
    callback(admin_client, p["id"], code, q["state"])
    form = parse_qs(idp.last_token_request.content.decode())
    assert form["code_verifier"][0] and form["client_secret"][0] == "s3cret"


def test_logout_returns_provider_logout_url(admin_client, idp):
    p = create_provider(admin_client, auto_create=True)
    admin_client.cookies.clear()
    entra_login(admin_client, idp, p["id"], {"sub": "s", "oid": "o", "email": "lo@corp.ae"})
    r = admin_client.post(f"{API}/auth/logout")
    assert r.json()["logout_url"].startswith(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/logout")


def test_password_login_logout_has_no_provider_url(client):
    make_user("pw@test.com")
    client.login("pw@test.com")
    assert client.post(f"{API}/auth/logout").json()["logout_url"] is None


# ------------------------------------------------------------------ attacks and failures
def test_state_mismatch_rejected(admin_client, idp):
    p = create_provider(admin_client, auto_create=True)
    admin_client.cookies.clear()
    q = start(admin_client, p["id"])
    code = idp.issue_code(q["nonce"], claims={"sub": "s", "oid": "o", "email": "a@corp.ae"})
    assert "expired or did not match" in error_of(callback(admin_client, p["id"], code, "forged-state"))


def test_callback_without_state_cookie_rejected(admin_client, idp):
    p = create_provider(admin_client, auto_create=True)
    admin_client.cookies.clear()
    q = start(admin_client, p["id"])
    code = idp.issue_code(q["nonce"], claims={"sub": "s", "oid": "o", "email": "a@corp.ae"})
    with Client(admin_client.app) as other_browser:  # attacker's browser has no state cookie
        assert "expired" in error_of(callback(other_browser, p["id"], code, q["state"]))


def test_wrong_nonce_rejected(admin_client, idp):
    p = create_provider(admin_client, auto_create=True)
    admin_client.cookies.clear()
    q = start(admin_client, p["id"])
    code = idp.issue_code("some-other-nonce", claims={"sub": "s", "oid": "o", "email": "a@corp.ae"})
    assert "did not match" in error_of(callback(admin_client, p["id"], code, q["state"]))


def test_forged_signature_rejected(admin_client, idp):
    p = create_provider(admin_client, auto_create=True)
    admin_client.cookies.clear()
    idp.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    r = entra_login(admin_client, idp, p["id"], {"sub": "s", "oid": "o", "email": "a@corp.ae"})
    assert "could not be verified" in error_of(r)
    assert admin_client.get(f"{API}/auth/me").status_code == 401


@pytest.mark.parametrize(
    "override", [{"aud": "someone-else"}, {"iss": "https://evil.example/v2.0"}, {"exp": 1000}]
)
def test_bad_token_claims_rejected(admin_client, idp, override):
    p = create_provider(admin_client, auto_create=True)
    admin_client.cookies.clear()
    idp.id_token_overrides = override
    r = entra_login(admin_client, idp, p["id"], {"sub": "s", "oid": "o", "email": "a@corp.ae"})
    assert "could not be verified" in error_of(r)


def test_disabled_user_blocked(admin_client, idp):
    p = create_provider(admin_client)
    make_user("off@corp.ae", is_active=False)
    admin_client.cookies.clear()
    r = entra_login(admin_client, idp, p["id"], {"sub": "s", "oid": "o", "email": "off@corp.ae"})
    assert "disabled" in error_of(r)


def test_open_redirect_blocked(admin_client, idp):
    p = create_provider(admin_client, auto_create=True)
    admin_client.cookies.clear()
    r = entra_login(
        admin_client,
        idp,
        p["id"],
        {"sub": "s", "oid": "o", "email": "a@corp.ae"},
        next_path="//evil.example/steal",
    )
    assert r.headers["location"] == "/"


def test_provider_error_and_disabled_provider(admin_client, idp):
    p = create_provider(admin_client, auto_create=True)
    r = admin_client.get(
        f"{API}/auth/sso/{p['id']}/callback", params={"error": "access_denied"}, follow_redirects=False
    )
    assert "cancelled" in error_of(r)
    r = admin_client.get(f"{API}/auth/sso/999/start", follow_redirects=False)
    assert "not available" in error_of(r)


def test_failed_sso_is_audited(admin_client, idp):
    p = create_provider(admin_client)
    entra_login(admin_client, idp, p["id"], {"sub": "s", "oid": "o", "email": "nobody@corp.ae"})
    admin_client.cookies.clear()
    admin_client.login("admin@test.com")
    actions = [e["action"] for e in admin_client.get(f"{API}/admin/audit?action=auth.sso").json()["items"]]
    assert "auth.sso_failed" in actions


# ------------------------------------------------------------------ UAE PASS flow
UAEPASS_PROFILE = {
    "sub": "uaepass-sub-1",
    "uuid": "uuid-1",
    "fullnameEN": "Mariam Al Mansoori",
    "email": "Mariam@Example.ae",
    "mobile": "971500000000",
    "userType": "SOP2",
    "idn": "784199012345678",
    "nationalityEN": "ARE",
}


def uaepass_provider(admin_client, **overrides) -> dict:
    return create_provider(
        admin_client,
        type="uaepass",
        name="UAE PASS",
        config={"environment": "staging", **overrides.pop("config", {})},
        **overrides,
    )


def uaepass_login(client, idp, provider_id: int, profile: dict):
    q = start(client, provider_id)
    code = idp.issue_code("", userinfo=profile)
    return q, callback(client, provider_id, code, q["state"])


def test_uaepass_authorize_request(admin_client, idp):
    p = uaepass_provider(admin_client, auto_create=True, config={"ui_locales": "ar"})
    q = start(admin_client, p["id"])
    assert q["_location"].startswith("https://stg-id.uaepass.ae/idshub/authorize?")
    assert q["scope"] == "urn:uae:digitalid:profile:general"
    assert q["acr_values"] == "urn:safelayer:tws:policies:authentication:level:low"
    assert q["ui_locales"] == "ar" and q["response_type"] == "code"
    assert "code_challenge" not in q  # UAE PASS does not use PKCE
    assert q["redirect_uri"].endswith(f"/api/v1/auth/sso/{p['id']}/callback")


def test_uaepass_sign_in_creates_user_with_mapped_attributes(admin_client, idp):
    p = uaepass_provider(admin_client, auto_create=True, attribute_map={"nationality": "nationalityEN"})
    admin_client.cookies.clear()
    _, r = uaepass_login(admin_client, idp, p["id"], UAEPASS_PROFILE)
    assert r.status_code == 303 and r.headers["location"] == "/", r.headers.get("location")
    me = admin_client.get(f"{API}/auth/me").json()
    assert me["email"] == "mariam@example.ae" and me["name"] == "Mariam Al Mansoori"
    assert me["attributes"] == {"nationality": "ARE"}  # Emirates ID is not stored unless mapped
    # Token request used HTTP Basic and query parameters, as UAE PASS documents
    req = idp.last_token_request
    assert req.headers["authorization"].startswith("Basic ")
    assert parse_qs(req.url.query.decode())["grant_type"] == ["authorization_code"]


def test_uaepass_minimum_account_level(admin_client, idp):
    p = uaepass_provider(admin_client, auto_create=True, config={"min_user_type": "SOP2"})
    admin_client.cookies.clear()
    _, r = uaepass_login(admin_client, idp, p["id"], {**UAEPASS_PROFILE, "userType": "SOP1"})
    assert "verified" in error_of(r)
    _, r = uaepass_login(admin_client, idp, p["id"], {**UAEPASS_PROFILE, "userType": "SOP3"})
    assert r.headers["location"] == "/"


def test_uaepass_production_endpoints_and_logout(admin_client, idp):
    p = uaepass_provider(admin_client, auto_create=True, config={"environment": "production"})
    q = start(admin_client, p["id"])
    assert q["_location"].startswith("https://id.uaepass.ae/idshub/authorize?")
    # use staging for the rest (fake server answers on both hosts)
    admin_client.cookies.clear()
    code = idp.issue_code("", userinfo=UAEPASS_PROFILE)
    callback(admin_client, p["id"], code, q["state"])  # no state cookie after clear -> rejected
    assert admin_client.get(f"{API}/auth/me").status_code == 401
    _, r = uaepass_login(admin_client, idp, p["id"], UAEPASS_PROFILE)
    assert r.headers["location"] == "/"
    out = admin_client.post(f"{API}/auth/logout").json()
    assert out["logout_url"].startswith("https://id.uaepass.ae/idshub/logout?redirect_uri=")


def test_one_user_can_link_microsoft_and_uaepass(admin_client, idp):
    ms = create_provider(admin_client)
    up = uaepass_provider(admin_client)
    make_user("mariam@example.ae", Role.viewer)
    admin_client.cookies.clear()
    entra_login(admin_client, idp, ms["id"], {"sub": "s", "oid": "oid-m", "email": "mariam@example.ae"})
    admin_client.cookies.clear()
    uaepass_login(admin_client, idp, up["id"], UAEPASS_PROFILE)
    methods = {m["provider_type"] for m in admin_client.get(f"{API}/auth/me").json()["sign_in_methods"]}
    assert methods == {"entra", "uaepass"}


def test_oidc_discovery_url_must_be_https_except_localhost(admin_client):
    base = {"type": "oidc", "name": "SSO", "enabled": True, "client_id": "c"}
    bad = admin_client.post(
        f"{API}/admin/identity-providers",
        json={**base, "config": {"discovery_url": "http://idp.example.com/.well-known/x"}},
    )
    assert bad.status_code == 422
    ok = admin_client.post(
        f"{API}/admin/identity-providers",
        json={**base, "config": {"discovery_url": "http://localhost:9000/.well-known/x"}},
    )
    assert ok.status_code == 201
