from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db import get_db
from app.models import Group, User, user_groups
from app.schemas import GroupIn, GroupMembersIn, GroupOut
from app.services import audit

router = APIRouter(prefix="/groups", tags=["groups"])


def _get(db: Session, group_id: int) -> Group:
    group = db.get(Group, group_id)
    if not group:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Group not found")
    return group


def _out(db: Session, group: Group) -> GroupOut:
    out = GroupOut.model_validate(group)
    out.member_count = db.scalar(
        select(func.count()).select_from(user_groups).where(user_groups.c.group_id == group.id)
    )
    return out


def _check_unique(db: Session, name: str, exclude_id: int | None = None) -> None:
    stmt = select(Group).where(func.lower(Group.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(Group.id != exclude_id)
    if db.scalar(stmt):
        raise HTTPException(status.HTTP_409_CONFLICT, "A group with this name already exists")


@router.get("", response_model=list[GroupOut])
def list_groups(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [_out(db, g) for g in db.scalars(select(Group).order_by(Group.name))]


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(
    body: GroupIn, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    _check_unique(db, body.name)
    group = Group(name=body.name.strip(), description=body.description)
    db.add(group)
    db.flush()
    audit.record(
        db,
        "group.create",
        user=admin,
        request=request,
        object_type="group",
        object_id=group.id,
        details={"name": group.name},
    )
    return _out(db, group)


@router.patch("/{group_id}", response_model=GroupOut)
def update_group(
    group_id: int,
    body: GroupIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    group = _get(db, group_id)
    _check_unique(db, body.name, exclude_id=group.id)
    group.name = body.name.strip()
    group.description = body.description
    audit.record(db, "group.update", user=admin, request=request, object_type="group", object_id=group.id)
    return _out(db, group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    group_id: int, request: Request, db: Session = Depends(get_db), admin: User = Depends(require_admin)
):
    group = _get(db, group_id)
    name = group.name
    db.delete(group)
    audit.record(
        db,
        "group.delete",
        user=admin,
        request=request,
        object_type="group",
        object_id=group_id,
        details={"name": name},
    )


@router.get("/{group_id}/members", response_model=list[int])
def get_members(group_id: int, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [u.id for u in _get(db, group_id).members]


@router.put("/{group_id}/members", response_model=GroupOut)
def set_members(
    group_id: int,
    body: GroupMembersIn,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    group = _get(db, group_id)
    users = list(db.scalars(select(User).where(User.id.in_(body.user_ids)))) if body.user_ids else []
    if len(users) != len(set(body.user_ids)):
        raise HTTPException(422, "Unknown user ids")
    group.members = users
    audit.record(
        db,
        "group.members",
        user=admin,
        request=request,
        object_type="group",
        object_id=group.id,
        details={"count": len(users)},
    )
    return _out(db, group)
