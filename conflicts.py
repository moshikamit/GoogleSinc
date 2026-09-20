"""Command-line interface for reviewing and resolving sync conflicts.

Conflicts are never resolved automatically: this tool lets the user see
which files conflict, inspect both versions, and decide which one stays.

Usage:
    python conflicts.py list
    python conflicts.py show <path>
    python conflicts.py resolve <path> local|remote
"""

import argparse
import os
import sys
from datetime import datetime

from app.sync_engine import SyncEngine

DEFAULT_LOCAL_ROOT = os.path.join(os.path.expanduser("~"), "GoogleSinc")
DEFAULT_DRIVE_FOLDER = "GoogleSinc"


def _fmt_time(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def cmd_list(engine: SyncEngine) -> int:
    conflicts = engine.list_conflicts()
    if not conflicts:
        print("No conflicts. Everything is in sync.")
        return 0
    print(f"{len(conflicts)} conflict(s) need your decision:\n")
    for row in conflicts:
        print(f"  {row['path']}")
        print(f"    last synced: {_fmt_time(row['last_synced_at'])}")
    print("\nRun: python conflicts.py show <path>   to inspect a conflict")
    print("Run: python conflicts.py resolve <path> local|remote   to decide")
    return 1


def cmd_show(engine: SyncEngine, path: str) -> int:
    info = engine.describe_conflict(path)
    if info["record"] is None:
        print(f"Not a tracked file: {path}")
        return 2
    print(f"Conflict: {path}\n")

    local = info["local"]
    remote = info["remote"]

    print("LOCAL copy:")
    if local["exists"]:
        print(f"  size:     {local['size']} bytes")
        print(f"  modified: {_fmt_time(local['modified_at'])}")
        print(f"  md5:      {local['md5']}")
    else:
        print("  (missing locally)")

    print("\nDRIVE copy:")
    if remote["exists"]:
        print(f"  size:     {remote['size']} bytes")
        print(f"  modified: {_fmt_time(remote['modified_at'])}")
        print(f"  md5:      {remote['md5']}")
    else:
        print("  (missing on Drive)")

    print("\nDecide which one stays:")
    print(f"  python conflicts.py resolve \"{path}\" local    -> overwrite Drive with local")
    print(f"  python conflicts.py resolve \"{path}\" remote   -> overwrite local with Drive")
    return 0


def cmd_resolve(engine: SyncEngine, path: str, keep: str) -> int:
    try:
        engine.resolve_conflict(path, keep)
    except (ValueError, FileNotFoundError) as exc:
        print(f"Cannot resolve: {exc}")
        return 2
    print(f"Resolved {path}: kept the {'local' if keep == 'local' else 'Drive'} copy.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Review and resolve sync conflicts.")
    parser.add_argument("--local-root", default=DEFAULT_LOCAL_ROOT)
    parser.add_argument("--drive-folder", default=DEFAULT_DRIVE_FOLDER)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List conflicting files")

    show_p = sub.add_parser("show", help="Show both versions of a conflict")
    show_p.add_argument("path")

    resolve_p = sub.add_parser("resolve", help="Decide which version stays")
    resolve_p.add_argument("path")
    resolve_p.add_argument("keep", choices=["local", "remote"])

    args = parser.parse_args()

    engine = SyncEngine(args.local_root, args.drive_folder)

    if args.command == "list":
        return cmd_list(engine)
    if args.command == "show":
        return cmd_show(engine, args.path)
    return cmd_resolve(engine, args.path, args.keep)


if __name__ == "__main__":
    sys.exit(main())
