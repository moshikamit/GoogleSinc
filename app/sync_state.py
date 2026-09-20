import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional

DEFAULT_DB_NAME = "sync_state.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path TEXT PRIMARY KEY,
    remote_id TEXT,
    size INTEGER,
    local_md5 TEXT,
    remote_md5 TEXT,
    local_modified_at REAL,
    remote_modified_at TEXT,
    sync_status TEXT NOT NULL DEFAULT 'pending',
    last_synced_at TEXT
);
"""


class SyncStateDB:
    """SQLite-backed store of per-file sync metadata.

    Tracks one row per synced relative path so the sync engine can decide
    what changed locally or remotely since the last successful sync.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            db_path = os.path.join(project_root, DEFAULT_DB_NAME)
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(SCHEMA)

    def upsert_file(
        self,
        path: str,
        remote_id: Optional[str] = None,
        size: Optional[int] = None,
        local_md5: Optional[str] = None,
        remote_md5: Optional[str] = None,
        local_modified_at: Optional[float] = None,
        remote_modified_at: Optional[str] = None,
        sync_status: str = "synced",
    ) -> None:
        """Insert or update the tracked state of a file after a sync action."""
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files (
                    path, remote_id, size, local_md5, remote_md5,
                    local_modified_at, remote_modified_at,
                    sync_status, last_synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    remote_id = COALESCE(excluded.remote_id, files.remote_id),
                    size = COALESCE(excluded.size, files.size),
                    local_md5 = COALESCE(excluded.local_md5, files.local_md5),
                    remote_md5 = COALESCE(excluded.remote_md5, files.remote_md5),
                    local_modified_at = COALESCE(
                        excluded.local_modified_at, files.local_modified_at
                    ),
                    remote_modified_at = COALESCE(
                        excluded.remote_modified_at, files.remote_modified_at
                    ),
                    sync_status = excluded.sync_status,
                    last_synced_at = excluded.last_synced_at
                """,
                (
                    path,
                    remote_id,
                    size,
                    local_md5,
                    remote_md5,
                    local_modified_at,
                    remote_modified_at,
                    sync_status,
                    now,
                ),
            )

    def get_file(self, path: str) -> Optional[Dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM files WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None

    def list_files(self) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM files ORDER BY path").fetchall()
        return [dict(row) for row in rows]

    def list_by_status(self, status: str) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM files WHERE sync_status = ? ORDER BY path",
                (status,),
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_file(self, path: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM files WHERE path = ?", (path,))

    def set_status(self, path: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE files SET sync_status = ? WHERE path = ?",
                (status, path),
            )

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM files").fetchone()
        return int(row[0])
