"""Backups from the command line.

python -m app.backup create            write a backup to DATA_DIR/backups
python -m app.backup list
python -m app.backup restore FILE      replace everything with the backup (stop the API and workers first)
"""

import argparse
import sys
from pathlib import Path

from app.services import backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.backup")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--no-data", action="store_true", help="only the database, not the dataset snapshots")
    sub.add_parser("list")
    r = sub.add_parser("restore")
    r.add_argument("file")
    r.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    r.add_argument("--no-data", action="store_true")
    args = parser.parse_args(argv)
    if args.cmd == "create":
        print(backup.create(include_data=not args.no_data))
    elif args.cmd == "list":
        for b in backup.listing():
            print(f"{b['name']}  {b['size'] / 1e6:.1f} MB")
    else:
        path = Path(args.file)
        if not path.is_file():
            path = backup.folder() / args.file
        if not args.yes and input(f"Replace ALL Lumen data with {path.name}? Type yes: ").strip() != "yes":
            print("Cancelled")
            return 1
        try:
            out = backup.restore(path, data=not args.no_data)
        except backup.BackupError as exc:
            print(f"Restore failed: {exc}", file=sys.stderr)
            return 2
        print(f"Restored the backup made {out['created_at']}: {sum(out['tables'].values())} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
