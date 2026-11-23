"""Organisation settings (admins) and personal preferences (everyone)."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_role
from app.db import get_db
from app.models import Role, User
from app.schemas import UserOut
from app.services import audit, backup, org, twofactor
from app.services import settings as svc
from app.services.users import to_out

router = APIRouter(tags=["settings"])
require_admin = require_role(Role.admin)


@router.get("/settings")
def get_settings(db: Session = Depends(get_db), user: User = Depends(current_user)):
    """What every signed-in page needs: the organisation's regional settings and this person's effective
    theme, language and calendar."""
    return {"regional": svc.regional(db).model_dump(), "effective": svc.effective(db, user)}


@router.put("/admin/settings/regional", response_model=svc.RegionalSettings)
def set_regional(
    body: svc.RegionalSettings,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    before = svc.regional(db).model_dump()
    svc.save_regional(db, body, user)
    changed = {k: v for k, v in body.model_dump().items() if before.get(k) != v}
    audit.record(
        db,
        "settings.update",
        user=user,
        request=request,
        object_type="settings",
        object_id="regional",
        details={"changed": changed},
        commit=False,
    )
    db.commit()
    return body


@router.put("/me/preferences", response_model=UserOut)
def set_preferences(body: svc.Preferences, db: Session = Depends(get_db), user: User = Depends(current_user)):
    user.preferences = body.model_dump(exclude_none=True)
    db.commit()
    return to_out(user)


# ------------------------------------------------------------------ security
class SecurityIn(BaseModel):
    require_two_factor: Literal["off", "admins", "everyone"] = "off"


@router.get("/admin/settings/security")
def get_security(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    from app.models import User as U

    on = db.scalar(select(func.count()).select_from(U).where(U.totp_enabled.is_(True), U.is_active.is_(True)))
    total = db.scalar(
        select(func.count()).select_from(U).where(U.is_active.is_(True), U.password_hash.is_not(None))
    )
    return {"require_two_factor": twofactor.policy(db), "users_with_two_factor": on, "password_users": total}


@router.put("/admin/settings/security")
def set_security(
    body: SecurityIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    twofactor.save_policy(db, body.require_two_factor, admin)
    audit.record(
        db, "settings.update", user=admin, request=request, object_type="settings", object_id="security",
        details=body.model_dump(), commit=False,
    )  # fmt: skip
    db.commit()
    return get_security(db, admin)


# ------------------------------------------------------------------ branding
@router.get("/branding")
def get_branding(db: Session = Depends(get_db)):
    """Public (the sign-in page shows it): name, logo, accent colour, sign-in message."""
    return org.branding(db).model_dump()


@router.put("/admin/settings/branding")
def set_branding(
    body: org.Branding, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    org.save_branding(db, body, admin)
    audit.record(
        db, "settings.update", user=admin, request=request, object_type="settings", object_id="branding",
        details={"name": body.name, "accent": body.accent, "logo": bool(body.logo)}, commit=False,
    )  # fmt: skip
    db.commit()
    return body.model_dump()


# ------------------------------------------------------------------ email
@router.get("/admin/settings/email")
def get_email(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return org.email_view(db)


@router.put("/admin/settings/email")
def set_email(
    body: org.EmailSettings,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    org.save_email(db, body, admin)
    audit.record(
        db, "settings.update", user=admin, request=request, object_type="settings", object_id="email",
        details={"host": body.host, "port": body.port, "user": body.user,
                 "password_changed": body.password is not None},
        commit=False,
    )  # fmt: skip
    db.commit()
    return org.email_view(db)


@router.post("/admin/settings/email/test")
def test_email(db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    from app.services.email import send_email

    try:
        send_email(admin.email, "Lumen test email", "Email from Lumen works.")
    except Exception as exc:  # noqa: BLE001 - show the server's answer
        return {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"[:300]}
    return {"ok": True, "to": admin.email}


# ------------------------------------------------------------------ backups
class BackupSettingsIn(BaseModel):
    enabled: bool = True
    keep: int = Field(7, ge=1, le=90)
    hour: int = Field(2, ge=0, le=23)


def _backups_view(db: Session) -> dict:
    return {**backup.settings(db), "backups": backup.listing(), "folder": str(backup.folder())}


@router.get("/admin/backups")
def list_backups(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return _backups_view(db)


@router.put("/admin/backups/settings")
def set_backups(
    body: BackupSettingsIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    backup.save_settings(db, body.enabled, body.keep, body.hour, admin)
    audit.record(
        db, "settings.update", user=admin, request=request, object_type="settings", object_id="backup",
        details=body.model_dump(), commit=False,
    )  # fmt: skip
    db.commit()
    return _backups_view(db)


@router.post("/admin/backups", status_code=202)
def backup_now(request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    import threading

    from app.core.config import get_settings as app_settings

    audit.record(db, "backup.create", user=admin, request=request)
    mode = app_settings().jobs_mode
    if mode == "eager":
        name = backup.create().name
        backup.prune(backup.settings(db)["keep"])
        return {"started": True, "name": name}
    if mode == "inline":
        threading.Thread(target=backup.create, daemon=True).start()
    else:
        from app.worker import make_backup

        make_backup.delay()
    return {"started": True, "name": None}


@router.get("/admin/backups/{name}")
def download_backup(
    name: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    try:
        path = backup.path_of(name)
    except backup.BackupError as exc:
        raise HTTPException(404, str(exc)) from exc
    audit.record(db, "backup.download", user=admin, request=request, object_id=name)
    return FileResponse(path, media_type="application/gzip", filename=name)


@router.delete("/admin/backups/{name}", status_code=204)
def delete_backup(
    name: str, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    try:
        backup.path_of(name).unlink()
    except backup.BackupError as exc:
        raise HTTPException(404, str(exc)) from exc
    audit.record(db, "backup.delete", user=admin, request=request, object_id=name)
