"""Backups of everything Lumen keeps: the metadata database (as JSON lines, table by table) and the data
directory (current dataset snapshots and uploaded files).

A backup is one file, DATA_DIR/backups/lumen-YYYYmmdd-HHMMSS.tar.gz. It is made every night (02:00,
organisation time; Admin → Settings → Backups), on demand, or with `python -m app.backup create`. The newest
N are kept. Secrets inside stay encrypted with ENCRYPTION_KEY: keep that key safe, a backup is useless
without it.

Restoring replaces everything: `python -m app.backup restore FILE` with the API and workers stopped, into a
database migrated to the same version.
"""

import gzip
import io
import json
import logging
import shutil
import tarfile
import tempfile
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import AppSetting, User
from app.services import snapshots

log = logging.getLogger(__name__)
NAME_FMT = "lumen-%Y%m%d-%H%M%S.tar.gz"
FORMAT = 1


class BackupError(Exception):
    pass


def folder() -> Path:
    p = get_settings().data_dir / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def settings(db: Session) -> dict:
    row = db.get(AppSetting, "backup")
    v = row.value if row else {}
    return {
        "enabled": bool(v.get("enabled", True)),
        "keep": int(v.get("keep", 7)),
        "hour": int(v.get("hour", 2)),
    }


def save_settings(db: Session, enabled: bool, keep: int, hour: int, user: User) -> dict:
    row = db.get(AppSetting, "backup")
    if row is None:
        row = AppSetting(key="backup", value={})
        db.add(row)
    row.value = {"enabled": enabled, "keep": keep, "hour": hour}
    row.updated_by_id = user.id
    return row.value


def _newest_first() -> list[Path]:
    return sorted(folder().glob("lumen-*.tar.gz"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)


def listing() -> list[dict]:
    out = []
    for p in _newest_first():
        st = p.stat()
        out.append(
            {"name": p.name, "size": st.st_size, "created_at": datetime.fromtimestamp(st.st_mtime, UTC)}
        )
    return out


def path_of(name: str) -> Path:
    p = folder() / name
    if (
        "/" in name
        or "\\" in name
        or not name.startswith("lumen-")
        or not name.endswith(".tar.gz")
        or not p.is_file()
    ):
        raise BackupError("Backup not found")
    return p


# ------------------------------------------------------------------ create
def _json(v: Any) -> Any:
    if isinstance(v, datetime | date | time):
        return {"$t": v.isoformat()}
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        return {"$b": v.hex()}
    return v


def _revision() -> str:
    with engine.connect() as conn:
        try:
            return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar() or "")
        except Exception:  # noqa: BLE001 - tests create tables without Alembic
            return ""


def create(include_data: bool = True) -> Path:
    """Writes a new backup and returns its path. Safe to run while Lumen is in use."""
    s = get_settings()
    name = datetime.now(UTC).strftime(NAME_FMT)
    final = folder() / name
    n = 2
    while final.exists():  # two in the same second
        final = folder() / name.replace(".tar.gz", f"-{n}.tar.gz")
        n += 1
    tmp = final.with_suffix(".tmp")
    tables = Base.metadata.sorted_tables
    with tarfile.open(tmp, "w:gz") as tar:
        counts = {}
        with engine.connect() as conn:
            for table in tables:
                buf = io.BytesIO()
                n = 0
                with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
                    for row in conn.execute(table.select()).mappings():
                        gz.write(
                            json.dumps({k: _json(v) for k, v in row.items()}, default=str).encode() + b"\n"
                        )
                        n += 1
                counts[table.name] = n
                info = tarfile.TarInfo(f"db/{table.name}.jsonl.gz")
                info.size = buf.tell()
                buf.seek(0)
                tar.addfile(info, buf)
        manifest = {"format": FORMAT, "created_at": datetime.now(UTC).isoformat(), "revision": _revision(),
                    "tables": counts, "data": include_data}  # fmt: skip
        raw = json.dumps(manifest, indent=1).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(raw)
        tar.addfile(info, io.BytesIO(raw))
        if include_data:
            with SessionLocal() as db:
                from app.models import Dataset

                for ds in db.scalars(select(Dataset)):
                    snap = snapshots.dataset_dir(ds.id) / f"v{ds.current_version}"
                    if ds.current_version and snap.exists():
                        tar.add(snap, arcname=f"data/datasets/{ds.id}/v{ds.current_version}")
            uploads = s.data_dir / "uploads"
            if uploads.is_dir():
                tar.add(uploads, arcname="data/uploads")
    tmp.rename(final)
    log.info("Backup written: %s", final)
    return final


def prune(keep: int) -> int:
    files = _newest_first()
    for p in files[max(1, keep) :]:
        p.unlink(missing_ok=True)
    return max(0, len(files) - max(1, keep))


def nightly(now: datetime | None = None) -> str | None:
    """Called by the housekeeping job: makes today's backup once it is past the hour, then prunes."""
    tz = ZoneInfo(get_settings().timezone)
    local = (now or datetime.now(tz)).astimezone(tz)
    with SessionLocal() as db:
        cfg = settings(db)
    if not cfg["enabled"] or local.hour < cfg["hour"]:
        return None
    today = local.strftime("%Y%m%d")
    # Names are in UTC; "made today" means made after today's backup hour, local time
    start = datetime.combine(local.date(), time(cfg["hour"]), tz)
    for b in listing():
        if b["created_at"] >= start:
            return None
    path = create()
    prune(cfg["keep"])
    log.info("Nightly backup %s (%s)", path.name, today)
    return path.name


# ------------------------------------------------------------------ restore
def _value(column, v: Any) -> Any:
    if isinstance(v, dict) and "$t" in v and len(v) == 1:
        raw = v["$t"]
        try:
            kind = column.type.python_type
        except NotImplementedError:
            kind = datetime
        if kind is date:
            return date.fromisoformat(raw)
        if kind is time:
            return time.fromisoformat(raw)
        return datetime.fromisoformat(raw)
    if isinstance(v, dict) and "$b" in v and len(v) == 1:
        return bytes.fromhex(v["$b"])
    return v


def restore(path: Path, *, data: bool = True) -> dict:
    """Replaces every table's rows (and the data directory) with the backup's. Run with Lumen stopped."""
    s = get_settings()
    with tarfile.open(path, "r:gz") as tar:
        manifest = json.loads(tar.extractfile("manifest.json").read())
        if manifest.get("format") != FORMAT:
            raise BackupError("This backup was made by a different version of Lumen")
        current = _revision()
        if current and manifest.get("revision") and manifest["revision"] != current:
            raise BackupError(
                f"The backup is from database version {manifest['revision']} but this database is {current}. "
                "Run the same Lumen version (alembic upgrade/downgrade) first."
            )
        tables = Base.metadata.sorted_tables
        counts = {}
        with engine.begin() as conn:
            for table in reversed(tables):
                conn.execute(table.delete())
            for table in tables:
                member = f"db/{table.name}.jsonl.gz"
                try:
                    fh = tar.extractfile(member)
                except KeyError:
                    continue
                cols = {c.name: c for c in table.columns}
                batch: list[dict] = []
                n = 0
                with gzip.GzipFile(fileobj=fh) as gz:
                    for line in gz:
                        row = json.loads(line)
                        batch.append({k: _value(cols[k], v) for k, v in row.items() if k in cols})
                        if len(batch) >= 1000:
                            conn.execute(table.insert(), batch)
                            n += len(batch)
                            batch = []
                if batch:
                    conn.execute(table.insert(), batch)
                    n += len(batch)
                counts[table.name] = n
            if conn.dialect.name == "postgresql":
                # Make new ids continue after the restored ones
                for table in tables:
                    pk = [c for c in table.primary_key.columns]
                    if (
                        len(pk) == 1
                        and pk[0].autoincrement in (True, "auto")
                        and str(pk[0].type) == "INTEGER"
                    ):
                        conn.execute(
                            text(
                                f"SELECT setval(pg_get_serial_sequence('{table.name}', '{pk[0].name}'), "
                                f"COALESCE((SELECT MAX({pk[0].name}) FROM {table.name}), 0) + 1, false)"
                            )
                        )
        if data and manifest.get("data"):
            target = s.data_dir
            with tempfile.TemporaryDirectory(dir=target) as tmp:
                for m in tar.getmembers():
                    if m.name.startswith("data/") and (m.isfile() or m.isdir()):
                        tar.extract(m, tmp, filter="data")
                src = Path(tmp) / "data"
                for sub in ("datasets", "uploads"):
                    if (src / sub).exists():
                        shutil.rmtree(target / sub, ignore_errors=True)
                        shutil.move(str(src / sub), str(target / sub))
    from app.query import cache

    cache.clear()
    return {"tables": counts, "created_at": manifest.get("created_at")}
