"""Whole-My-Drive two-way sync engine.

This is the Google-Drive-for-Desktop model: one local folder mirrors all of
My Drive. Remote files are tracked by their full relative path (e.g.
"Documents/work/report.pdf"), and nested Drive folders are created on demand
when local files need to be uploaded.

Safety model:
- Local delete  -> remote file moved to Drive TRASH (recoverable 30 days).
- Remote delete -> local file removed.
- Conflicts     -> never auto-resolved; both copies kept, flagged for the user.
"""

import os
from typing import Dict, List, Optional

from app.comparator import SyncPlan, build_sync_plan
from app.drive_service import FOLDER_MIME, GoogleDriveService
from app.local_scanner import compute_md5, delete_local_file, scan_folder
from app.sync_state import SyncStateDB


class MyDriveSyncEngine:
    """Keep one local folder in two-way sync with the whole of My Drive."""

    def __init__(self, local_root: str, state_db: Optional[SyncStateDB] = None) -> None:
        self.local_root = local_root
        self.drive = GoogleDriveService()
        self.state_db = state_db or SyncStateDB()
        self._folder_id_cache: Dict[str, str] = {}

    # --- Remote tree ---------------------------------------------------

    def _build_remote_tree(self) -> Dict[str, Dict]:
        """Fetch all of My Drive and index files/folders by relative path."""
        items = self.drive.list_all_my_drive()
        by_id = {item["id"]: item for item in items}
        root_id = self.drive.get_root_id()

        def full_path(item: Dict) -> str:
            parts = [item["name"]]
            seen = {item["id"]}
            parent = item.get("parents", [None])[0]
            while parent and parent in by_id and parent not in seen:
                seen.add(parent)
                parts.append(by_id[parent]["name"])
                parent = by_id[parent].get("parents", [None])[0]
            return "/".join(reversed(parts))

        tree: Dict[str, Dict] = {}
        for item in items:
            if item.get("trashed"):
                continue
            path = full_path(item)
            if not path:
                continue
            item = dict(item)
            item["path"] = path
            tree[path] = item
            if item.get("mimeType") == FOLDER_MIME:
                self._folder_id_cache[path] = item["id"]
        return tree

    def _ensure_remote_folder(self, rel_dir: str) -> Optional[str]:
        """Return the Drive id for a nested folder path, creating it if needed."""
        if not rel_dir:
            return self.drive.get_root_id()
        if rel_dir in self._folder_id_cache:
            return self._folder_id_cache[rel_dir]

        parts = rel_dir.split("/")
        parent_id = self.drive.get_root_id()
        current = ""
        for part in parts:
            current = part if not current else f"{current}/{part}"
            if current in self._folder_id_cache:
                parent_id = self._folder_id_cache[current]
                continue
            folder = self.drive.find_or_create_folder(name=part, parent_id=parent_id)
            parent_id = folder["id"]
            self._folder_id_cache[current] = parent_id
        return parent_id

    # --- Planning ------------------------------------------------------

    def build_plan(self, execute_deletes: bool = True) -> SyncPlan:
        local_scan = scan_folder(self.local_root)
        tree = self._build_remote_tree()
        remote_files = [
            item for path, item in tree.items() if item.get("mimeType") != FOLDER_MIME
        ]
        # Comparator matches on the "name" field; feed it full relative paths.
        for item in remote_files:
            item["name"] = item["path"]
        stored = self.state_db.list_files()
        return build_sync_plan(local_scan, remote_files, stored, execute_deletes)

    # --- Sync ----------------------------------------------------------

    def sync(self, execute_deletes: bool = True) -> SyncPlan:
        plan = self.build_plan(execute_deletes=execute_deletes)
        tree = self._build_remote_tree()
        remote_files = {
            path: item
            for path, item in tree.items()
            if item.get("mimeType") != FOLDER_MIME
        }
        stored_by_path = {row["path"]: row for row in self.state_db.list_files()}

        for path in plan.uploads:
            self._upload(path, remote_files.get(path))

        for path in plan.downloads:
            remote_item = remote_files.get(path)
            if remote_item:
                self._download(path, remote_item)

        for path in plan.conflicts:
            self.state_db.set_status(path, "conflict")

        if execute_deletes:
            for path in plan.local_deletions:
                remote_item = remote_files.get(path)
                if remote_item:
                    self.drive.delete_file(remote_item["id"])  # to Drive trash
                self.state_db.remove_file(path)

            for path in plan.remote_deletions:
                local_path = os.path.join(self.local_root, path)
                if os.path.exists(local_path):
                    delete_local_file(self.local_root, path)
                self.state_db.remove_file(path)
        else:
            for path in plan.local_deletions + plan.remote_deletions:
                self.state_db.set_status(path, "pending_delete")

        for path in plan.stale_records:
            self.state_db.remove_file(path)

        return plan

    # --- File operations ----------------------------------------------

    def _upload(self, path: str, existing_remote: Optional[Dict]) -> None:
        local_path = os.path.join(self.local_root, path)
        if not os.path.exists(local_path):
            return
        stat = os.stat(local_path)
        parent_id = self._ensure_remote_folder(os.path.dirname(path))
        uploaded = self.drive.upload_local_file(
            local_path=local_path,
            remote_name=os.path.basename(path),
            parent_id=parent_id,
            file_id=existing_remote["id"] if existing_remote else None,
        )
        self.state_db.upsert_file(
            path=path,
            remote_id=uploaded.get("id"),
            size=stat.st_size,
            local_md5=compute_md5(local_path),
            remote_md5=uploaded.get("md5Checksum"),
            local_modified_at=stat.st_mtime,
            remote_modified_at=uploaded.get("modifiedTime"),
            sync_status="synced",
        )

    def _download(self, path: str, remote_item: Dict) -> None:
        local_path = os.path.join(self.local_root, path)
        os.makedirs(os.path.dirname(local_path) or self.local_root, exist_ok=True)
        self.drive.download_file(remote_item["id"], local_path)
        stat = os.stat(local_path)
        self.state_db.upsert_file(
            path=path,
            remote_id=remote_item["id"],
            size=stat.st_size,
            local_md5=compute_md5(local_path),
            remote_md5=remote_item.get("md5Checksum"),
            local_modified_at=stat.st_mtime,
            remote_modified_at=remote_item.get("modifiedTime"),
            sync_status="synced",
        )

    # --- Conflicts -----------------------------------------------------

    def list_conflicts(self) -> List[Dict]:
        return self.state_db.list_by_status("conflict")

    def resolve_conflict(self, path: str, keep: str) -> None:
        if keep not in ("local", "remote"):
            raise ValueError("keep must be 'local' or 'remote'")
        record = self.state_db.get_file(path)
        if record is None:
            raise ValueError(f"No tracked file at path: {path}")
        if record.get("sync_status") != "conflict":
            raise ValueError(f"File is not in conflict: {path}")

        tree = self._build_remote_tree()
        remote_item = tree.get(path)
        if keep == "local":
            self._upload(path, remote_item)
        else:
            if remote_item is None:
                raise FileNotFoundError(f"Remote file missing: {path}")
            self._download(path, remote_item)
