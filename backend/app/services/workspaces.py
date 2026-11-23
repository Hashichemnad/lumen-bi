"""Workspaces: who may see or change what is in them.

A member (a user, or everyone in a group) is a viewer, editor or manager of a workspace:
- viewer: opens its dashboards and charts;
- editor: also changes them and adds new ones;
- manager: also deletes and shares them, and manages the members.
Admins manage every workspace. People with the Viewer role are viewers whatever they were given.
Membership never gives access to data: charts still run with each person's row-level security.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Role, User, Workspace, WorkspaceMember

ROLES = ("viewer", "editor", "manager")
RANK = {None: 0, "viewer": 1, "editor": 2, "manager": 3}
LEVEL = {"viewer": "view", "editor": "edit", "manager": "manage"}


def roles_of(db: Session, user: User) -> dict[int, str]:
    """Workspace id -> this person's role, for every workspace they belong to (admins: all)."""
    if user.has_role(Role.admin):
        return {w: "manager" for w in db.scalars(select(Workspace.id))}
    group_ids = {g.id for g in user.groups}
    out: dict[int, str] = {}
    for m in db.scalars(select(WorkspaceMember)):
        hit = (m.principal == "user" and m.principal_id == user.id) or (
            m.principal == "group" and m.principal_id in group_ids
        )
        if hit and RANK[m.role] > RANK[out.get(m.workspace_id)]:
            out[m.workspace_id] = m.role
    if not user.has_role(Role.explorer):
        out = {w: "viewer" for w in out}
    return out


def role_in(db: Session, user: User, workspace_id: int | None) -> str | None:
    if workspace_id is None:
        return None
    return roles_of(db, user).get(workspace_id)


def level_in(db: Session, user: User, workspace_id: int | None) -> str | None:
    """The dashboard/chart level ('view' | 'edit' | 'manage') a workspace role gives, or None."""
    role = role_in(db, user, workspace_id)
    return LEVEL[role] if role else None


def best_level(*levels: str | None) -> str | None:
    order = {None: 0, "view": 1, "edit": 2, "manage": 3}
    return max(levels, key=lambda lv: order[lv])


def can_add_to(db: Session, user: User, workspace_id: int) -> bool:
    """Editors and managers put new dashboards and charts in a workspace."""
    return RANK[role_in(db, user, workspace_id)] >= RANK["editor"]


def forget_principal(db: Session, principal: str, principal_id: int) -> None:
    for m in db.scalars(
        select(WorkspaceMember).where(
            WorkspaceMember.principal == principal, WorkspaceMember.principal_id == principal_id
        )
    ):
        db.delete(m)
