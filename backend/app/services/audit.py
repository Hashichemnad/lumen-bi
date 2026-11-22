from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def client_ip(request: Request | None) -> str:
    if request is None:
        return ""
    # Caddy sets X-Forwarded-For; uvicorn --proxy-headers maps it to request.client
    return request.client.host if request.client else ""


def record(
    db: Session,
    action: str,
    *,
    user: User | None = None,
    request: Request | None = None,
    object_type: str = "",
    object_id: str | int = "",
    details: dict[str, Any] | None = None,
    commit: bool = True,
) -> None:
    db.add(
        AuditLog(
            action=action,
            user_id=user.id if user else None,
            user_email=user.email if user else "",
            object_type=object_type,
            object_id=str(object_id),
            ip=client_ip(request),
            details=details or {},
        )
    )
    if commit:
        db.commit()
