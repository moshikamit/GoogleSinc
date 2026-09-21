"""SQLite catalog for the whole-My-Drive mirror.

Separate from the incremental sync_state DB: this tracks the full Drive
listing (so we know when the metadata catalog is complete) plus a download
queue (so a stopped run resumes exactly where it left off).
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

DEFAULT_DB_NAME = "mirror_catalog.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS catalog (
    path TEXT PRIMARY KEY,
    remote_id TEXT NOT NULL,
    is_folder INTEGER NOT NULL DEFAULT 0,
    size INTEGER,
    remote_md5 TEXT,
    remote_modified_at TEXT,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|downloading|done|failed|skipped
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_catalog_status ON catalog(status);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MirrorCatalog:
    """Persistent catalog + download queue for the mirror."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(project_root, DEFAULT_DB_NAME)
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # safe for concurrent readers
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # --- catalog-complete flag (metadata phase tracking) ---------------

    def get_meta(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def is_catalog_complete(self) -> bool:
        return self.get_meta("catalog_complete") == "1"

    def mark_catalog_complete(self, total: int) -> None:
        self.set_meta("catalog_complete", "1")
        self.set_meta("catalog_total", str(total))
        self.set_meta("catalog_finished_at", _now())

    def mark_catalog_incomplete(self) -> None:
        self.set_meta("catalog_complete", "0")

    # --- catalog population (resumable) --------------------------------

    def upsert_item(
        self,
        path: str,
        remote_id: str,
        is_folder: bool,
        size: Optional[int] = None,
        remote_md5: Optional[str] = None,
        remote_modified_at: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO catalog (
                    path, remote_id, is_folder, size, remote_md5,
                    remote_modified_at, status, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(path) DO UPDATE SET
                    remote_id = excluded.remote_id,
                    is_folder = excluded.is_folder,
                    size = excluded.size,
                    remote_md5 = excluded.remote_md5,
                    remote_modified_at = excluded.remote_modified_at,
                    updated_at = excluded.updated_at
                """,
                (path, remote_id, 1 if is_folder else 0, size,
                 remote_md5, remote_modified_at, _now()),
            )

    def catalog_size(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM catalog").fetchone()
        return int(row[0])

    def folder_map(self) -> Dict[str, Dict]:
        """Rebuild the {remote_id: {id, name, parents}} map for all cataloged
        folders, so path resolution works after a resume without re-listing."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path, remote_id FROM catalog WHERE is_folder = 1"
            ).fetchall()
        path_to_id = {r[0]: r[1] for r in rows}
        result: Dict[str, Dict] = {}
        for path, remote_id in rows:
            name = path.rsplit("/", 1)[-1]
            parent_path = path.rsplit("/", 1)[0] if "/" in path else ""
            parent_id = path_to_id.get(parent_path) if parent_path else None
            result[remote_id] = {
                "id": remote_id,
                "name": name,
                "parents": [parent_id] if parent_id else [],
            }
        return result

    def get_path_by_remote_id(self, remote_id: str) -> Optional[str]:
        """Look up a cataloged item's full path by its Drive file/folder ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT path FROM catalog WHERE remote_id = ?", (remote_id,)
            ).fetchone()
        return row[0] if row else None

    def get_path_by_remote_id(self, remote_id: str) -> Optional[str]:
        """Return the cataloged path for a Drive file/folder id, or None."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT path FROM catalog WHERE remote_id = ?", (remote_id,)
            ).fetchone()
        return row[0] if row else None

    # --- download queue -------------------------------------------------

    def claim_batch(self, limit: int) -> List[Dict]:
        """Atomically mark up to `limit` pending files as 'downloading'.

        Uses a transaction so concurrent workers never grab the same file.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path FROM catalog WHERE status = 'pending' AND is_folder = 0 "
                "ORDER BY size IS NULL, size ASC LIMIT ?",
                (limit,),
            ).fetchall()
            paths = [r[0] for r in rows]
            if paths:
                conn.execute(
                    f"UPDATE catalog SET status = 'downloading' "
                    f"WHERE path IN ({','.join('?' * len(paths))})",
                    paths,
                )
        return [self.get_item(p) for p in paths]

    def get_item(self, path: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM catalog WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None

    def mark_done(self, path: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE catalog SET status = 'done', error = NULL, updated_at = ? "
                "WHERE path = ?",
                (_now(), path),
            )

    def mark_failed(self, path: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE catalog SET status = 'failed', attempts = attempts + 1, "
                "error = ?, updated_at = ? WHERE path = ?",
                (error, _now(), path),
            )

    def requeue_failed(self, max_attempts: int = 3) -> int:
        """Move failed items back to pending if under the attempt cap."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE catalog SET status = 'pending' "
                "WHERE status = 'failed' AND attempts < ?",
                (max_attempts,),
            )
            return cur.rowcount

    def reset_in_flight(self) -> int:
        """On startup, return any 'downloading' items to 'pending'
        (a previous run may have died mid-download)."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE catalog SET status = 'pending' WHERE status = 'downloading'"
            )
            return cur.rowcount

    def status_counts(self) -> Dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM catalog GROUP BY status"
            ).fetchall()
        return {r[0]: r[1] for r in rows}
