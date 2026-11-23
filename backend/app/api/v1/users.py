import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db import get_db
from app.models import Group, Role, User
from app.query import rls
from app.schemas import Page, UserCreate, UserImportResult, UserOut, UserUpdate
from app.services import audit
from app.services.users import (
    ensure_not_last_admin,
    load_groups,
    send_reset_email,
    set_password,
    to_out,
    validate_password,
)

router = APIRouter(prefix="/users", tags=["users"])


def _get(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return user


@router.get("", response_model=Page[UserOut])
def list_users(
    q: str = "",
    role: Role | None = None,
    group_id: int | None = None,
    active: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.name).like(like)))
    if role:
        stmt = stmt.where(User.role == role.value)
    if active is not None:
        stmt = stmt.where(User.is_active.is_(active))
    if group_id:
        stmt = stmt.where(User.groups.any(Group.id == group_id))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(User.name, User.email).offset((page - 1) * page_size).limit(page_size))
    return Page[UserOut](items=[to_out(u) for u in rows], total=total)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "A user with this email already exists")
    user = User(
        email=body.email,
        name=body.name,
        role=body.role.value,
        attributes=body.attributes,
        groups=load_groups(db, body.group_ids),
    )
    if body.password:
        set_password(user, body.password)
    db.add(user)
    db.flush()
    audit.record(
        db,
        "user.create",
        user=admin,
        request=request,
        object_type="user",
        object_id=user.id,
        details={"email": user.email, "role": user.role},
    )
    if body.send_invite:
        send_reset_email(db, user, invite=True)
    return to_out(user)


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return to_out(_get(db, user_id))


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    body: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = _get(db, user_id)
    changes = body.model_dump(exclude_unset=True, exclude={"password"})
    losing_admin = (body.role is not None and body.role != Role.admin) or body.is_active is False
    if losing_admin:
        ensure_not_last_admin(db, user)
    if body.name is not None:
        user.name = body.name
    if body.role is not None:
        user.role = body.role.value
    if body.is_active is not None:
        user.is_active = body.is_active
        if not body.is_active:
            user.token_version += 1  # sign out immediately
    if body.attributes is not None:
        user.attributes = body.attributes
    if body.group_ids is not None:
        user.groups = load_groups(db, body.group_ids)
    if body.password:
        set_password(user, body.password)
        changes["password"] = "changed"
    if body.unlock:
        user.locked_until = None
        user.failed_logins = 0
    audit.record(
        db,
        "user.update",
        user=admin,
        request=request,
        object_type="user",
        object_id=user.id,
        details={"changes": {k: v for k, v in changes.items()}},
    )
    return to_out(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    user = _get(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete your own account")
    ensure_not_last_admin(db, user)
    email = user.email
    rls.forget_principal(db, "user", user_id)
    db.delete(user)
    audit.record(
        db,
        "user.delete",
        user=admin,
        request=request,
        object_type="user",
        object_id=user_id,
        details={"email": email},
    )


@router.post("/{user_id}/send-reset", status_code=status.HTTP_202_ACCEPTED)
def send_reset(
    user_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    user = _get(db, user_id)
    send_reset_email(db, user, invite=user.password_hash is None)
    audit.record(db, "user.send_reset", user=admin, request=request, object_type="user", object_id=user.id)
    return {"detail": "Email sent"}


@router.post("/{user_id}/sign-out", status_code=status.HTTP_204_NO_CONTENT)
def sign_out_everywhere(
    user_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    user = _get(db, user_id)
    user.token_version += 1
    audit.record(db, "user.sign_out", user=admin, request=request, object_type="user", object_id=user.id)


@router.post("/import", response_model=UserImportResult)
async def import_users(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """CSV columns: email (required), name, role, groups (separated by ;), password, attr_<name>...

    Attribute values containing ";" become lists, e.g. attr_region = "EMEA;APAC".
    Existing users (matched by email) are updated; unknown groups are created.
    """
    text = (await file.read()).decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or "email" not in [f.strip().lower() for f in reader.fieldnames]:
        raise HTTPException(422, "CSV must have an 'email' column")
    created = updated = 0
    errors: list[str] = []
    group_cache = {g.name.lower(): g for g in db.scalars(select(Group))}

    for line_no, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        email = row.get("email", "").lower()
        if not email or "@" not in email:
            errors.append(f"Line {line_no}: missing or invalid email")
            continue
        role = row.get("role") or None
        if role and role not in Role.__members__:
            errors.append(f"Line {line_no}: unknown role '{role}'")
            continue
        password = row.get("password") or None
        if password:
            try:
                validate_password(password)
            except HTTPException as exc:
                errors.append(f"Line {line_no}: {exc.detail}")
                continue
        attrs = {}
        for key, value in row.items():
            if key.startswith("attr_") and value:
                attrs[key[5:]] = [v.strip() for v in value.split(";")] if ";" in value else value
        groups = []
        for name in filter(None, (g.strip() for g in row.get("groups", "").split(";"))):
            if name.lower() not in group_cache:
                group_cache[name.lower()] = Group(name=name)
                db.add(group_cache[name.lower()])
            groups.append(group_cache[name.lower()])

        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            user = User(email=email, name=row.get("name", ""), role=role or Role.viewer.value)
            db.add(user)
            created += 1
        else:
            if row.get("name"):
                user.name = row["name"]
            if role:
                user.role = role
            updated += 1
        if attrs:
            user.attributes = {**(user.attributes or {}), **attrs}
        if groups:
            user.groups = groups
        if password:
            set_password(user, password)

    audit.record(
        db,
        "user.import",
        user=admin,
        request=request,
        details={"created": created, "updated": updated, "errors": len(errors)},
    )
    return UserImportResult(created=created, updated=updated, errors=errors)
