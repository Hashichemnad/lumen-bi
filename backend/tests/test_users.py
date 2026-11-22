from app.models import Role
from tests.conftest import PASSWORD, make_user


def test_non_admin_cannot_manage_users(client):
    for role in (Role.viewer, Role.explorer, Role.creator):
        client.cookies.clear()
        make_user(f"{role.value}@test.com", role)
        client.login(f"{role.value}@test.com")
        assert client.get("/api/v1/users").status_code == 403
        assert client.post("/api/v1/users", json={"email": "x@test.com"}).status_code == 403
        assert client.get("/api/v1/groups").status_code == 403
        assert client.get("/api/v1/admin/audit").status_code == 403


def test_create_list_update_user(admin_client):
    g = admin_client.post("/api/v1/groups", json={"name": "Dubai branch"}).json()
    r = admin_client.post(
        "/api/v1/users",
        json={
            "email": "New@Test.com",
            "name": "New Person",
            "role": "creator",
            "password": "Welcome12345",
            "attributes": {"region": ["UAE", "KSA"], "branch": "Dubai"},
            "group_ids": [g["id"]],
        },
    )
    assert r.status_code == 201, r.text
    user = r.json()
    assert user["email"] == "new@test.com"
    assert user["attributes"]["region"] == ["UAE", "KSA"]
    assert user["groups"] == [{"id": g["id"], "name": "Dubai branch"}]

    listed = admin_client.get("/api/v1/users?q=new").json()
    assert listed["total"] == 1

    r = admin_client.patch(
        f"/api/v1/users/{user['id']}", json={"role": "viewer", "attributes": {"region": "UAE"}}
    )
    assert r.json()["role"] == "viewer" and r.json()["attributes"] == {"region": "UAE"}


def test_duplicate_email_rejected(admin_client):
    admin_client.post("/api/v1/users", json={"email": "dup@test.com"})
    assert admin_client.post("/api/v1/users", json={"email": "dup@test.com"}).status_code == 409


def test_cannot_remove_last_admin(admin_client):
    me = admin_client.get("/api/v1/auth/me").json()
    assert admin_client.patch(f"/api/v1/users/{me['id']}", json={"role": "viewer"}).status_code == 409
    assert admin_client.patch(f"/api/v1/users/{me['id']}", json={"is_active": False}).status_code == 409
    assert admin_client.delete(f"/api/v1/users/{me['id']}").status_code == 409


def test_deactivate_signs_user_out(admin_client, client):
    other = make_user("other@test.com")
    from app.main import app
    from tests.conftest import Client

    with Client(app) as c2:
        token = c2.post("/api/v1/auth/login", json={"email": "other@test.com", "password": PASSWORD}).cookies[
            "lumen_access"
        ]
        admin_client.patch(f"/api/v1/users/{other.id}", json={"is_active": False})
        c2.cookies.clear()
        assert c2.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_unlock_user(admin_client, client):
    u = make_user("locked@test.com")
    from datetime import UTC, datetime, timedelta

    from app.db import SessionLocal
    from app.models import User

    with SessionLocal() as s:
        s.get(User, u.id).locked_until = datetime.now(UTC) + timedelta(minutes=10)
        s.commit()
    assert admin_client.get(f"/api/v1/users/{u.id}").json()["locked"] is True
    assert admin_client.patch(f"/api/v1/users/{u.id}", json={"unlock": True}).json()["locked"] is False


def test_delete_user(admin_client):
    u = make_user("bye@test.com")
    assert admin_client.delete(f"/api/v1/users/{u.id}").status_code == 204
    assert admin_client.get(f"/api/v1/users/{u.id}").status_code == 404


def test_csv_import(admin_client):
    csv_text = (
        "email,name,role,groups,attr_region,attr_branch\n"
        "a@test.com,Amal,creator,Finance;Sales,UAE;KSA,Dubai\n"
        "b@test.com,Bilal,,Sales,,\n"
        "bad-row,,,,,\n"
        "c@test.com,Chen,superuser,,,\n"
    )
    r = admin_client.post("/api/v1/users/import", files={"file": ("u.csv", csv_text, "text/csv")})
    body = r.json()
    assert body["created"] == 2 and len(body["errors"]) == 2, body
    users = {u["email"]: u for u in admin_client.get("/api/v1/users").json()["items"]}
    assert users["a@test.com"]["attributes"] == {"region": ["UAE", "KSA"], "branch": "Dubai"}
    assert {g["name"] for g in users["a@test.com"]["groups"]} == {"Finance", "Sales"}
    assert users["b@test.com"]["role"] == "viewer"

    # re-import updates instead of duplicating
    r = admin_client.post(
        "/api/v1/users/import", files={"file": ("u.csv", "email,role\na@test.com,explorer\n", "text/csv")}
    )
    assert r.json()["updated"] == 1


def test_invite_email(admin_client, caplog):
    caplog.set_level("WARNING", logger="lumen.email")
    admin_client.post("/api/v1/users", json={"email": "inv@test.com", "send_invite": True})
    assert "invited" in caplog.text and "reset-password?token=" in caplog.text


def test_groups_crud_and_members(admin_client):
    u1, u2 = make_user("u1@test.com"), make_user("u2@test.com")
    g = admin_client.post("/api/v1/groups", json={"name": "Finance", "description": "Finance team"}).json()
    assert admin_client.post("/api/v1/groups", json={"name": "finance"}).status_code == 409
    r = admin_client.put(f"/api/v1/groups/{g['id']}/members", json={"user_ids": [u1.id, u2.id]})
    assert r.json()["member_count"] == 2
    assert sorted(admin_client.get(f"/api/v1/groups/{g['id']}/members").json()) == sorted([u1.id, u2.id])
    assert (
        admin_client.patch(f"/api/v1/groups/{g['id']}", json={"name": "Finance UAE"}).json()["name"]
        == "Finance UAE"
    )
    filtered = admin_client.get(f"/api/v1/users?group_id={g['id']}").json()
    assert filtered["total"] == 2
    assert admin_client.delete(f"/api/v1/groups/{g['id']}").status_code == 204


def test_audit_log_records_actions(admin_client):
    admin_client.post("/api/v1/users", json={"email": "aud@test.com"})
    log = admin_client.get("/api/v1/admin/audit").json()
    actions = [e["action"] for e in log["items"]]
    assert "user.create" in actions and "auth.login" in actions
    only_users = admin_client.get("/api/v1/admin/audit?action=user.").json()
    assert all(e["action"].startswith("user.") for e in only_users["items"])
