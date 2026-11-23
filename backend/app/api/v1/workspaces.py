"""Workspaces: admins create and delete them; workspace managers rename them and manage the members.

Deleting a workspace keeps its dashboards and charts: they go back to being personal items of their owners.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_role
from app.db import get_db
from app.models import Chart, Dashboard, Group, Role, User, Workspace, WorkspaceMember
from app.schemas import WorkspaceIn, WorkspaceMemberIn, WorkspaceMemberOut, WorkspaceOut
from app.services import audit, workspaces

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
require_admin = require_role(Role.admin)


def _count(db: Session, model, workspace_id: int) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.workspace_id == workspace_id)) or 0


def _out(db: Session, w: Workspace, role: str) -> WorkspaceOut:
    return WorkspaceOut(
        id=w.id,
        name=w.name,
        description=w.description,
        my_role=role,
        dashboards=_count(db, Dashboard, w.id),
        charts=_count(db, Chart, w.id),
        members=db.scalar(
            select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.workspace_id == w.id)
        )
        or 0,
        created_at=w.created_at,
    )


def _get(db: Session, workspace_id: int, user: User, need: str = "viewer") -> tuple[Workspace, str]:
    w = db.get(Workspace, workspace_id)
    role = workspaces.role_in(db, user, workspace_id) if w else None
    if w is None or role is None:
        raise HTTPException(404, "Workspace not found")
    if workspaces.RANK[role] < workspaces.RANK[need]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only managers of this workspace can do this")
    return w, role


def _unique_name(db: Session, name: str, exclude: int | None = None) -> str:
    name = name.strip()
    clash = db.scalar(select(Workspace).where(func.lower(Workspace.name) == name.lower()))
    if clash is not None and clash.id != exclude:
        raise HTTPException(409, "A workspace with this name already exists")
    return name


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(db: Session = Depends(get_db), user: User = Depends(current_user)):
    roles = workspaces.roles_of(db, user)
    found = db.scalars(select(Workspace).where(Workspace.id.in_(list(roles))).order_by(Workspace.name))
    return [_out(db, w, roles[w.id]) for w in found]


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(
    body: WorkspaceIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    w = Workspace(name=_unique_name(db, body.name), description=body.description, created_by_id=user.id)
    db.add(w)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "A workspace with this name already exists") from exc
    audit.record(
        db,
        "workspace.create",
        user=user,
        request=request,
        object_type="workspace",
        object_id=w.id,
        details={"name": w.name},
        commit=False,
    )
    db.commit()
    return _out(db, w, "manager")


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    w, role = _get(db, workspace_id, user)
    return _out(db, w, role)


@router.put("/{workspace_id}", response_model=WorkspaceOut)
def update_workspace(
    workspace_id: int,
    body: WorkspaceIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    w, role = _get(db, workspace_id, user, "manager")
    w.name, w.description = _unique_name(db, body.name, w.id), body.description
    audit.record(
        db,
        "workspace.update",
        user=user,
        request=request,
        object_type="workspace",
        object_id=w.id,
        details={"name": w.name},
        commit=False,
    )
    db.commit()
    return _out(db, w, role)


@router.delete("/{workspace_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    workspace_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)
):
    w, _ = _get(db, workspace_id, user)
    name = w.name
    # Items stay, as personal items of their owners (the foreign keys also do this on PostgreSQL)
    for model in (Dashboard, Chart):
        for item in db.scalars(select(model).where(model.workspace_id == w.id)):
            item.workspace_id = None
    db.delete(w)
    audit.record(
        db,
        "workspace.delete",
        user=user,
        request=request,
        object_type="workspace",
        object_id=workspace_id,
        details={"name": name},
    )


def _members(db: Session, workspace_id: int) -> list[WorkspaceMemberOut]:
    out = []
    rows = db.scalars(
        select(WorkspaceMember)
        .where(WorkspaceMember.workspace_id == workspace_id)
        .order_by(WorkspaceMember.id)
    )
    for m in rows:
        if m.principal == "user":
            u = db.get(User, m.principal_id)
            if u is None:
                continue
            out.append(WorkspaceMemberOut(principal="user", id=u.id, role=m.role, name=u.name, email=u.email))
        else:
            g = db.get(Group, m.principal_id)
            if g is None:
                continue
            out.append(WorkspaceMemberOut(principal="group", id=g.id, role=m.role, name=g.name))
    return out


@router.get("/{workspace_id}/members", response_model=list[WorkspaceMemberOut])
def get_members(workspace_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    _get(db, workspace_id, user)
    return _members(db, workspace_id)


@router.put("/{workspace_id}/members", response_model=list[WorkspaceMemberOut])
def set_members(
    workspace_id: int,
    body: list[WorkspaceMemberIn],
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    w, _ = _get(db, workspace_id, user, "manager")
    if len(body) > 1000:
        raise HTTPException(422, "Too many members")
    wanted: dict[tuple[str, int], str] = {}
    for m in body:
        target = db.get(User if m.principal == "user" else Group, m.id)
        if target is None:
            raise HTTPException(422, f"Unknown {m.principal} {m.id}")
        wanted[(m.principal, m.id)] = m.role
    # A manager cannot lock themselves out by mistake; admins manage every workspace anyway
    if not user.has_role(Role.admin):
        group_ids = {g.id for g in user.groups}
        keeps = wanted.get(("user", user.id)) == "manager" or any(
            p == "group" and i in group_ids and r == "manager" for (p, i), r in wanted.items()
        )
        if not keeps:
            raise HTTPException(422, "Keep yourself as a manager, or ask an admin to change your role")
    for old in db.scalars(select(WorkspaceMember).where(WorkspaceMember.workspace_id == w.id)):
        db.delete(old)
    db.flush()
    for (principal, pid), role in wanted.items():
        db.add(WorkspaceMember(workspace_id=w.id, principal=principal, principal_id=pid, role=role))
    audit.record(
        db,
        "workspace.members",
        user=user,
        request=request,
        object_type="workspace",
        object_id=w.id,
        details={
            "name": w.name,
            "members": [{"principal": p, "id": i, "role": r} for (p, i), r in wanted.items()],
        },
        commit=False,
    )
    db.commit()
    return _members(db, w.id)
