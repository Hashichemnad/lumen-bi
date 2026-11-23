"""Usage analytics, the sync monitor, and lineage / impact.

GET /admin/usage?days=30                       who uses what (admins)
GET /admin/sync-monitor                        every dataset's syncs over the last 7 days (Creators and up)
GET /lineage/datasets/{id}                     where its data comes from and what depends on it (Creators)
GET /lineage/datasets/{id}/impact?field=Name   what changing or removing it (or a field) would affect
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_role
from app.db import get_db
from app.models import Dataset, Role, User
from app.services import lineage, usage

router = APIRouter(tags=["monitor"])
require_creator = require_role(Role.creator)


@router.get("/admin/usage")
def usage_report(days: int = 30, db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return usage.report(db, days)


@router.get("/admin/sync-monitor")
def sync_monitor(db: Session = Depends(get_db), _: User = Depends(require_creator)):
    return usage.sync_monitor(db)


def _dataset(db: Session, dataset_id: int) -> Dataset:
    ds = db.get(Dataset, dataset_id)
    if ds is None:
        raise HTTPException(404, "Dataset not found")
    return ds


@router.get("/lineage/datasets/{dataset_id}")
def dataset_lineage(dataset_id: int, db: Session = Depends(get_db), _: User = Depends(require_creator)):
    return lineage.lineage(db, _dataset(db, dataset_id))


@router.get("/lineage/datasets/{dataset_id}/impact")
def dataset_impact(
    dataset_id: int,
    field: str | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(require_creator),
):
    return lineage.impact(db, _dataset(db, dataset_id), field)
