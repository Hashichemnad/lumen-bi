import re

from app.models import Role
from tests.conftest import PASSWORD, Client, make_user


def test_health(client):
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_login_sets_cookies_and_me(client):
    make_user("a@test.com", Role.creator)
    r = client.login("a@test.com")
    assert r.json()["role"] == "creator"
    assert "lumen_access" in r.cookies and "lumen_refresh" in r.cookies
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "a@test.com"


def test_login_case_insensitive_email(client):
    make_user("mixed@test.com")
    client.login("MIXED@Test.com")


def test_wrong_password_and_unknown_user_same_message(client):
    make_user("a@test.com")
    bad = client.post("/api/v1/auth/login", json={"email": "a@test.com", "password": "wrong"})
    unknown = client.post("/api/v1/auth/login", json={"email": "nobody@test.com", "password": "wrong"})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["detail"] == unknown.json()["detail"]


def test_lockout_after_failed_attempts(client):
    make_user("a@test.com")
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "a@test.com", "password": "wrong"})
    r = client.post("/api/v1/auth/login", json={"email": "a@test.com", "password": PASSWORD})
    assert r.status_code == 423


def test_inactive_user_cannot_login(client):
    make_user("off@test.com", is_active=False)
    r = client.post("/api/v1/auth/login", json={"email": "off@test.com", "password": PASSWORD})
    assert r.status_code == 401


def test_unauthenticated_me(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_header_required_for_cookie_writes(client):
    make_user("a@test.com")
    client.login("a@test.com")
    raw = super(Client, client).request("POST", "/api/v1/auth/logout")  # no X-Requested-With
    assert raw.status_code == 403


def test_bearer_token_works_without_csrf_header(client):
    make_user("a@test.com")
    token = client.login("a@test.com").cookies["lumen_access"]
    client.cookies.clear()
    r = super(Client, client).request("GET", "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


def test_refresh_issues_new_session(client):
    make_user("a@test.com")
    client.login("a@test.com")
    r = client.post("/api/v1/auth/refresh")
    assert r.status_code == 200 and "lumen_access" in r.cookies


def test_logout_everywhere_invalidates_tokens(client):
    make_user("a@test.com")
    token = client.login("a@test.com").cookies["lumen_access"]
    client.post("/api/v1/auth/logout?everywhere=true")
    client.cookies.clear()
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401


def test_change_password(client):
    make_user("a@test.com")
    client.login("a@test.com")
    r = client.post(
        "/api/v1/auth/change-password", json={"current_password": PASSWORD, "new_password": "NewPassword456"}
    )
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me").status_code == 200  # current session kept
    client.cookies.clear()
    client.login("a@test.com", "NewPassword456")


def test_change_password_policy(client):
    make_user("a@test.com")
    client.login("a@test.com")
    r = client.post(
        "/api/v1/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"}
    )
    assert r.status_code == 422


def test_password_reset_flow(client, caplog):
    make_user("a@test.com")
    caplog.set_level("WARNING", logger="lumen.email")
    r = client.post("/api/v1/auth/forgot-password", json={"email": "a@test.com"})
    assert r.status_code == 202
    token = re.search(r"token=([\w-]+)", caplog.text).group(1)

    assert (
        client.post(
            "/api/v1/auth/reset-password", json={"token": token, "new_password": "Reset123456"}
        ).status_code
        == 204
    )
    # token is single-use
    assert (
        client.post(
            "/api/v1/auth/reset-password", json={"token": token, "new_password": "Again123456"}
        ).status_code
        == 400
    )
    client.login("a@test.com", "Reset123456")


def test_forgot_password_unknown_email_same_answer(client):
    r = client.post("/api/v1/auth/forgot-password", json={"email": "ghost@test.com"})
    assert r.status_code == 202


def test_internal_domain_email_allowed(admin_client):
    r = admin_client.post("/api/v1/users", json={"email": "Staff@Company.Local", "password": "Welcome12345"})
    assert r.status_code == 201 and r.json()["email"] == "staff@company.local"
    assert admin_client.post("/api/v1/users", json={"email": "not-an-email"}).status_code == 422
