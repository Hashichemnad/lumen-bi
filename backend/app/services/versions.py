"""Earlier versions of dashboards and charts, to roll back to. Only the last KEEP are kept (owner's choice:
"1 or 2 versions to roll back, to avoid unwanted storage").

Dashboards save themselves while being edited, so a version is not made on every save: the state before a
change is kept when the last version is older than QUIET, or was made by someone else.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Chart, Dashboard, ItemVersion, User

KEEP = 2
QUIET = timedelta(minutes=15)


def _kind(item: Dashboard | Chart) -> str:
    return "chart" if isinstance(item, Chart) else "dashboard"


def history(db: Session, item: Dashboard | Chart) -> list[ItemVersion]:
    return list(
        db.scalars(
            select(ItemVersion)
            .where(ItemVersion.kind == _kind(item), ItemVersion.item_id == item.id)
            .order_by(ItemVersion.created_at.desc(), ItemVersion.id.desc())
        )
    )


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def keep_before_change(db: Session, item: Dashboard | Chart, user: User, force: bool = False) -> None:
    """Call before changing an item: keeps its current state as a version (see QUIET)."""
    versions = history(db, item)
    latest = versions[0] if versions else None
    now = datetime.now(UTC)
    if (
        not force
        and latest is not None
        and latest.saved_by_id == user.id
        and now - _aware(latest.created_at) < QUIET
    ):
        return
    db.add(
        ItemVersion(
            kind=_kind(item),
            item_id=item.id,
            name=item.name,
            description=item.description or "",
            config=item.config or {},
            query=item.query if isinstance(item, Chart) else None,
            dataset_id=item.dataset_id if isinstance(item, Chart) else None,
            saved_by_id=user.id,
            created_at=now,
        )
    )
    for old in versions[KEEP - 1 :]:
        db.delete(old)


def forget(db: Session, kind: str, item_id: int) -> None:
    for v in db.scalars(select(ItemVersion).where(ItemVersion.kind == kind, ItemVersion.item_id == item_id)):
        db.delete(v)
