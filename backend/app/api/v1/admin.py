from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db import get_db
from app.models import AuditLog, User
from app.schemas import AuditOut, Page

router = APIRouter(tags=["admin"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/admin/audit", response_model=Page[AuditOut])
def audit_log(
    action: str = "",
    user_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if since:
        stmt = stmt.where(AuditLog.created_at >= since)
    if until:
        stmt = stmt.where(AuditLog.created_at <= until)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(
        stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return Page[AuditOut](items=[AuditOut.model_validate(r) for r in rows], total=total)
