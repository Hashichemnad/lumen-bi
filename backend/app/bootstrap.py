"""Run once at container start: create the first admin if no users exist."""

import logging

from sqlalchemy import func, select

from app.core.config import get_settings
from app.core.security import hash_password, password_problems
from app.db import SessionLocal
from app.models import Role, User

log = logging.getLogger("lumen.bootstrap")


def check_secrets() -> None:
    s = get_settings()
    weak = len(s.secret_key) < 32 or s.secret_key.startswith(("change-me", "dev-secret"))
    if weak and s.cookie_secure:
        raise SystemExit("SECRET_KEY is missing or too short. Generate one with: openssl rand -hex 32")
    if weak:
        log.warning("SECRET_KEY is weak. Fine for a local trial, but set a strong one before going live.")


def ensure_admin() -> None:
    s = get_settings()
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(User)):
            return
        if password_problems(s.admin_password):
            raise SystemExit("ADMIN_PASSWORD must be at least 10 characters with a letter and a number")
        db.add(
            User(
                email=s.admin_email.lower(),
                name=s.admin_name,
                role=Role.admin.value,
                password_hash=hash_password(s.admin_password),
            )
        )
        db.commit()
        log.info("Created first admin %s", s.admin_email)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    check_secrets()
    ensure_admin()
