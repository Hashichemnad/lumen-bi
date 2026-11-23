"""Organisation settings (admins) and personal preferences (everyone)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_role
from app.db import get_db
from app.models import Role, User
from app.schemas import UserOut
from app.services import audit
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
