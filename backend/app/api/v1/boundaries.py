"""Map boundary files (GeoJSON) for Geo Map charts. Anyone signed in can use them; Creators upload; the
uploader or an admin can rename, change the name property or delete."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_user, require_role
from app.core.config import get_settings
from app.db import get_db
from app.models import Chart, MapBoundary, Role, User
from app.services import audit

router = APIRouter(prefix="/boundaries", tags=["maps"])
require_creator = require_role(Role.creator)
MAX_BYTES = 25 * 1024 * 1024
NAME_GUESSES = (
    "name",
    "name_en",
    "NAME",
    "NAME_EN",
    "Name",
    "NAME_1",
    "name_1",
    "emirate",
    "district",
    "region",
)
GEOMETRIES = {"Polygon", "MultiPolygon"}


class BoundaryUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    name_property: str = Field(min_length=1, max_length=100)


def _dir() -> Path:
    path = get_settings().data_dir.resolve() / "boundaries"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _out(b: MapBoundary) -> dict:
    return {
        "id": b.id,
        "name": b.name,
        "name_property": b.name_property,
        "properties": b.properties,
        "features": b.feature_count,
        "size_bytes": b.size_bytes,
        "owner_id": b.owner_id,
        "created_at": b.created_at,
    }


def _get(db: Session, boundary_id: int) -> MapBoundary:
    b = db.get(MapBoundary, boundary_id)
    if b is None:
        raise HTTPException(404, "Map not found")
    return b


def _can_change(user: User, b: MapBoundary) -> None:
    if not (user.has_role(Role.admin) or b.owner_id == user.id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Only the person who uploaded it or an admin can change it"
        )


def _parse(raw: bytes) -> tuple[dict, list[str], int]:
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            422, "The file is not valid JSON. Upload a GeoJSON file (.geojson or .json)."
        ) from exc
    if (
        not isinstance(data, dict)
        or data.get("type") != "FeatureCollection"
        or not isinstance(data.get("features"), list)
    ):
        raise HTTPException(
            422,
            "Upload a GeoJSON FeatureCollection (convert TopoJSON or shapefiles with mapshaper.org)",
        )
    features = [
        f
        for f in data["features"]
        if isinstance(f, dict) and (f.get("geometry") or {}).get("type") in GEOMETRIES
    ]
    if not features:
        raise HTTPException(422, "The file has no areas (Polygon or MultiPolygon shapes)")
    keys: list[str] = []
    for f in features:
        for k, v in (f.get("properties") or {}).items():
            if k not in keys and isinstance(v, str | int | float):
                keys.append(str(k))
    clean = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": f.get("properties") or {}, "geometry": f["geometry"]}
            for f in features
        ],
    }
    return clean, keys, len(features)


@router.get("")
def list_boundaries(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return [_out(b) for b in db.scalars(select(MapBoundary).order_by(MapBoundary.name))]


@router.get("/{boundary_id}")
def get_boundary(boundary_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    b = _get(db, boundary_id)
    path = _dir() / f"{b.id}.geojson"
    if not path.is_file():
        raise HTTPException(404, "The map file is missing")
    geo = json.loads(path.read_bytes())
    return {"id": b.id, "name": b.name, "name_property": b.name_property, "geojson": geo}


@router.get("/{boundary_id}/file")
def download_boundary(boundary_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    b = _get(db, boundary_id)
    return FileResponse(
        _dir() / f"{b.id}.geojson", media_type="application/geo+json", filename=f"{b.name}.geojson"
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_boundary(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    name_property: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    raw = await file.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            413, "Map files can be at most 25 MB. Simplify the shapes (for example with mapshaper.org)."
        )
    geo, keys, count = _parse(raw)
    prop = name_property.strip() or next((k for k in NAME_GUESSES if k in keys), keys[0] if keys else "")
    if not prop or prop not in keys:
        raise HTTPException(
            422, f"Choose the property with the area names: {', '.join(keys) or 'none found'}"
        )
    body = json.dumps(geo, separators=(",", ":")).encode()
    b = MapBoundary(
        name=name.strip()[:200] or "Map",
        name_property=prop,
        properties=keys,
        feature_count=count,
        size_bytes=len(body),
        owner_id=user.id,
    )
    db.add(b)
    db.flush()
    (_dir() / f"{b.id}.geojson").write_bytes(body)
    audit.record(
        db,
        "map.upload",
        user=user,
        request=request,
        object_type="map",
        object_id=b.id,
        details={"name": b.name, "features": count},
        commit=False,
    )
    db.commit()
    return _out(b)


@router.patch("/{boundary_id}")
def update_boundary(
    boundary_id: int,
    body: BoundaryUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_creator),
):
    b = _get(db, boundary_id)
    _can_change(user, b)
    if body.name_property not in (b.properties or []):
        raise HTTPException(422, "That property is not in the file")
    b.name, b.name_property = body.name.strip(), body.name_property
    db.commit()
    return _out(b)


@router.delete("/{boundary_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_boundary(
    boundary_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_creator)
):
    b = _get(db, boundary_id)
    _can_change(user, b)
    used = [
        c.name
        for c in db.scalars(select(Chart))
        if (c.config or {}).get("options", {}).get("boundary") == b.id
    ]
    if used:
        raise HTTPException(
            409, f"Used by the chart{'s' if len(used) > 1 else ''} {', '.join(used[:3])}. Change those first."
        )
    name = b.name
    db.delete(b)
    (_dir() / f"{boundary_id}.geojson").unlink(missing_ok=True)
    audit.record(
        db,
        "map.delete",
        user=user,
        request=request,
        object_type="map",
        object_id=boundary_id,
        details={"name": name},
    )
