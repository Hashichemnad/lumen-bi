"""Two-factor sign-in (TOTP + recovery codes), the organisation policy, admin reset, and rate limits."""

import pytest

from app.core import ratelimit
from app.core.config import get_settings
from app.db import SessionLocal
from app.models import AuditLog, Role, User
from app.services import twofactor
from tests.conftest import PASSWORD, Client, make_user

API = "/api/v1"


def _secret(email: str) -> str:
    from app.core.crypto import decrypt

    with SessionLocal() as s:
        u = s.query(User).filter_by(email=email).one()
        return decrypt(u.totp_secret_enc or u.totp_pending_enc)


def _code(email: str, offset: int = 0) -> str:
    import time

    return twofactor.code_at(_secret(email), time.time() + offset * 30)


def _turn_on(client, email):
    r = client.post(f"{API}/auth/two-factor/setup", json={})
    assert r.status_code == 200, r.text
    assert r.json()["uri"].startswith("otpauth://totp/") and "<svg" in r.json()["qr_svg"]
    r = client.post(f"{API}/auth/two-factor/enable", json={"code": _code(email)})
    assert r.status_code == 200, r.text
    return r.json()["recovery_codes"]


def test_rfc6238_vector():
    # RFC 6238 test secret "12345678901234567890" at 59 s -> 94287082 (8 digits) -> last 6: 287082
    import base64

    secret = base64.b32encode(b"12345678901234567890").decode()
    assert twofactor.code_at(secret, 59) == "287082"
    assert twofactor.matching_step(secret, "287 082", when=59) == 1
    assert twofactor.matching_step(secret, "287082", after=1, when=59) is None  # no replay


def test_setup_and_sign_in_with_code(client):
    make_user("a@test.com")
    client.login("a@test.com")
    codes = _turn_on(client, "a@test.com")
    assert len(codes) == 10 and client.get(f"{API}/auth/me").json()["totp_enabled"] is True
    with Client(client.app, base_url="http://testserver") as c:
        r = c.post(f"{API}/auth/login", json={"email": "a@test.com", "password": PASSWORD})
        assert r.status_code == 200 and r.json()["two_factor"] == "code"
        assert "lumen_access" not in r.cookies  # not signed in yet
        ticket = r.json()["ticket"]
        bad = c.post(f"{API}/auth/login/two-factor", json={"ticket": ticket, "code": "000000"})
        assert bad.status_code == 401
        r = c.post(f"{API}/auth/login/two-factor", json={"ticket": ticket, "code": _code("a@test.com", 1)})
        assert r.status_code == 200, r.text
        assert c.get(f"{API}/auth/me").status_code == 200
        # The same code cannot be used again
        r = c.post(f"{API}/auth/login", json={"email": "a@test.com", "password": PASSWORD})
        again = c.post(
            f"{API}/auth/login/two-factor",
            json={"ticket": r.json()["ticket"], "code": _code("a@test.com", 1)},
        )
        assert again.status_code == 401
        # A recovery code works once
        rec = c.post(f"{API}/auth/login/two-factor", json={"ticket": r.json()["ticket"], "code": codes[0]})
        assert rec.status_code == 200 and rec.json()["recovery_codes_left"] == 9
        r = c.post(f"{API}/auth/login", json={"email": "a@test.com", "password": PASSWORD})
        assert (
            c.post(
                f"{API}/auth/login/two-factor", json={"ticket": r.json()["ticket"], "code": codes[0]}
            ).status_code
            == 401
        )
    with SessionLocal() as s:
        assert s.query(AuditLog).filter_by(action="auth.two_factor_on").count() == 1
        assert s.query(AuditLog).filter_by(action="auth.recovery_code_used").count() == 1


def test_wrong_codes_lock_the_account(client):
    make_user("b@test.com")
    client.login("b@test.com")
    _turn_on(client, "b@test.com")
    with Client(client.app, base_url="http://testserver") as c:
        ticket = c.post(f"{API}/auth/login", json={"email": "b@test.com", "password": PASSWORD}).json()[
            "ticket"
        ]
        for _ in range(get_settings().max_failed_logins):
            c.post(f"{API}/auth/login/two-factor", json={"ticket": ticket, "code": "123456"})
        r = c.post(f"{API}/auth/login/two-factor", json={"ticket": ticket, "code": _code("b@test.com")})
        assert r.status_code == 423


def test_ticket_is_not_a_session_and_expires_with_sign_out(client):
    make_user("c@test.com")
    client.login("c@test.com")
    _turn_on(client, "c@test.com")
    with Client(client.app, base_url="http://testserver") as c:
        ticket = c.post(f"{API}/auth/login", json={"email": "c@test.com", "password": PASSWORD}).json()[
            "ticket"
        ]
        assert c.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {ticket}"}).status_code == 401
    client.post(f"{API}/auth/logout?everywhere=true")
    with Client(client.app, base_url="http://testserver") as c:
        r = c.post(f"{API}/auth/login/two-factor", json={"ticket": ticket, "code": _code("c@test.com")})
        assert r.status_code == 401


def test_policy_forces_setup_at_sign_in(client):
    make_user("admin@test.com", Role.admin)
    make_user("v@test.com")
    client.login("admin@test.com")
    assert (
        client.put(f"{API}/admin/settings/security", json={"require_two_factor": "everyone"}).status_code
        == 200
    )
    with Client(client.app, base_url="http://testserver") as c:
        r = c.post(f"{API}/auth/login", json={"email": "v@test.com", "password": PASSWORD})
        assert r.json()["two_factor"] == "setup"
        ticket = r.json()["ticket"]
        assert c.post(f"{API}/auth/two-factor/setup", json={"ticket": ticket}).status_code == 200
        r = c.post(f"{API}/auth/two-factor/enable", json={"ticket": ticket, "code": _code("v@test.com")})
        assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
        assert c.get(f"{API}/auth/me").json()["email"] == "v@test.com"  # signed in
        # Required: cannot be turned off
        assert c.post(f"{API}/auth/two-factor/disable", json={"password": PASSWORD}).status_code == 403
    sec = client.get(f"{API}/admin/settings/security").json()
    assert sec["require_two_factor"] == "everyone" and sec["users_with_two_factor"] == 1


def test_policy_and_reset_are_admin_only(client):
    make_user("admin@test.com", Role.admin)
    make_user("d@test.com", Role.creator)
    client.login("d@test.com")
    _turn_on(client, "d@test.com")
    assert client.put(f"{API}/admin/settings/security", json={"require_two_factor": "off"}).status_code == 403
    with SessionLocal() as s:
        uid = s.query(User).filter_by(email="d@test.com").one().id
    assert client.post(f"{API}/users/{uid}/two-factor/reset").status_code == 403
    with Client(client.app, base_url="http://testserver") as admin:
        admin.login("admin@test.com")
        r = admin.post(f"{API}/users/{uid}/two-factor/reset")
        assert r.status_code == 200 and r.json()["totp_enabled"] is False
    assert client.get(f"{API}/auth/me").status_code == 401  # signed out


def test_turn_off_and_new_recovery_codes(client):
    make_user("e@test.com")
    client.login("e@test.com")
    _turn_on(client, "e@test.com")
    r = client.post(f"{API}/auth/two-factor/recovery-codes", json={"code": _code("e@test.com", 1)})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    assert client.post(f"{API}/auth/two-factor/disable", json={"password": "wrong"}).status_code == 400
    r = client.post(f"{API}/auth/two-factor/disable", json={"password": PASSWORD})
    assert r.status_code == 200 and r.json()["totp_enabled"] is False
    r = client.post(f"{API}/auth/login", json={"email": "e@test.com", "password": PASSWORD})
    assert "two_factor" not in r.json()


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setattr(get_settings(), "rate_limits", True)
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_sign_in_is_rate_limited(client, limits):
    make_user("f@test.com")
    n = get_settings().login_per_minute
    codes = [
        client.post(f"{API}/auth/login", json={"email": "f@test.com", "password": "nope"}).status_code
        for _ in range(n + 1)
    ]
    assert codes[:n].count(429) == 0 and codes[n] == 429


def test_oauth_registration_is_rate_limited(client, limits):
    codes = [client.post("/oauth/register", json={}).status_code for _ in range(12)]
    assert 429 in codes
