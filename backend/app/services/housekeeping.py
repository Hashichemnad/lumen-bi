"""Hourly housekeeping: the nightly backup (once it is past the backup hour), old usage events, used or
expired OAuth codes and tokens."""

import logging
from datetime import timedelta

from sqlalchemy import delete

from app.db import SessionLocal
from app.models import OAuthCode, OAuthToken
from app.models.common import utcnow
from app.services import backup, usage

log = logging.getLogger(__name__)


def run() -> dict:
    out: dict = {}
    try:
        out["backup"] = backup.nightly()
    except Exception:  # noqa: BLE001 - keep going with the rest
        log.exception("Nightly backup failed")
        out["backup"] = "failed"
    with SessionLocal() as db:
        out["usage_pruned"] = usage.prune(db)
        now = utcnow()
        db.execute(delete(OAuthCode).where(OAuthCode.expires_at < now - timedelta(hours=1)))
        db.execute(delete(OAuthToken).where(OAuthToken.refresh_expires_at < now - timedelta(days=1)))
        db.commit()
    return out
