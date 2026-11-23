import os
import shutil
import tempfile
from pathlib import Path

# Configure before the app is imported. Set TEST_DATABASE_URL to run against PostgreSQL (CI does).
_tmp = Path(tempfile.mkdtemp(prefix="lumen-test-"))
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL") or f"sqlite:///{_tmp / 'test.db'}"
os.environ["DATA_DIR"] = str(_tmp)
os.environ["SECRET_KEY"] = "test-secret-key-for-unit-tests-only-0123456789"
os.environ["SMTP_HOST"] = ""
os.environ["JOBS_MODE"] = "eager"  # syncs run inside the request, so tests can check results directly

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.security import hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Role, User  # noqa: E402

PASSWORD = "Password123"


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    shutil.rmtree(_tmp / "datasets", ignore_errors=True)  # ids restart with the fresh database
    shutil.rmtree(_tmp / "uploads", ignore_errors=True)
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


def make_user(email: str, role: Role = Role.viewer, password: str = PASSWORD, **kw) -> User:
    with SessionLocal() as session:
        user = User(
            email=email,
            name=kw.pop("name", email.split("@")[0]),
            role=role.value,
            password_hash=hash_password(password),
            **kw,
        )
        session.add(user)
        session.commit()
        return user


class Client(TestClient):
    """Test client that sends the CSRF header like the real frontend does."""

    def request(self, method, url, **kwargs):
        headers = kwargs.pop("headers", None) or {}
        headers.setdefault("X-Requested-With", "lumen")
        return super().request(method, url, headers=headers, **kwargs)

    def login(self, email: str, password: str = PASSWORD):
        r = self.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return r


@pytest.fixture
def client():
    with Client(app, base_url="http://testserver") as c:
        yield c


@pytest.fixture
def creator_client(client):
    make_user("creator@test.com", Role.creator)
    client.login("creator@test.com")
    return client


@pytest.fixture
def admin_client(client):
    make_user("admin@test.com", Role.admin)
    client.login("admin@test.com")
    return client
