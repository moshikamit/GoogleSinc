import os
from typing import Dict, List, Optional

from app.comparator import SyncPlan, build_sync_plan
from app.drive_service import GoogleDriveService
from app.local_scanner import compute_md5, scan_folder
from app.sync_state import SyncStateDB


class SyncEngine:
    """Simple local-to-Drive sync engine for a single folder pair."""

    def __init__(
        self,
        local_root: str,
        drive_folder_name: str,
        state_db: Optional[SyncStateDB] = None,
    ) -> None:
        self.local_root = local_root
        self.drive_service = GoogleDriveService()
        self.drive_folder_name = drive_folder_name
        self.drive_folder: Optional[Dict[str, str]] = None
        self.state_db = state_db or SyncStateDB()

    def ensure_drive_folder(self) -> Dict[str, str]:
        folder = self.drive_service.find_or_create_folder(self.drive_folder_name)
        self.drive_folder = folder
        return folder

    def list_local_files(self) -> List[str]:
        all_files: List[str] = []
        for root, _, files in os.walk(self.local_root):
            for file_name in files:
                full_path = os.path.join(root, file_name)
                all_files.append(full_path)
        return all_files

    def _local_rel_path(self, local_path: str) -> str:
        return os.path.relpath(local_path, self.local_root).replace(os.sep, "/")

    def sync_local_to_drive(self) -> List[str]:
        if not self.drive_folder:
            self.ensure_drive_folder()

        uploaded: List[str] = []
        for local_path in self.list_local_files():
            relative_path = self._local_rel_path(local_path)
            self.drive_service.upload_local_file(
                local_path=local_path,
                remote_name=relative_path,
                parent_id=self.drive_folder["id"],
            )
            uploaded.append(relative_path)
        return uploaded

    def sync_drive_to_local(self, destination_dir: str) -> List[str]:
        if not self.drive_folder:
            self.ensure_drive_folder()

        downloaded: List[str] = []
        for item in self.drive_service.list_files_in_folder(self.drive_folder["id"]):
            relative_name = item.get("name")
            if not relative_name:
                continue

            local_path = os.path.join(destination_dir, relative_name)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            self.drive_service.download_file(item["id"], local_path)
            downloaded.append(relative_name)
        return downloaded

    def build_plan(self) -> SyncPlan:
        """Scan both sides and compare them against stored sync metadata."""
        if not self.drive_folder:
            self.ensure_drive_folder()

        local_scan = scan_folder(self.local_root)
        remote_files = self.drive_service.list_files_in_folder(self.drive_folder["id"])
        stored_state = self.state_db.list_files()
        return build_sync_plan(local_scan, remote_files, stored_state)

    def sync(self) -> SyncPlan:
        """Run one metadata-driven sync pass.

        Uploads new or locally changed files, downloads new or remotely
        changed files, skips unchanged files, and records the resulting
        state in SQLite. Conflicts and deletions are reported but never
        executed automatically.
        """
        plan = self.build_plan()
        remote_by_name = {
            item["name"]: item
            for item in self.drive_service.list_files_in_folder(self.drive_folder["id"])
        }

        for path in plan.uploads:
            local_path = os.path.join(self.local_root, path)
            stat = os.stat(local_path)
            existing_remote = remote_by_name.get(path)
            uploaded = self.drive_service.upload_local_file(
                local_path=local_path,
                remote_name=path,
                parent_id=self.drive_folder["id"],
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

        for path in plan.downloads:
            remote_item = remote_by_name.get(path)
            if not remote_item:
                continue
            local_path = os.path.join(self.local_root, path)
            os.makedirs(os.path.dirname(local_path) or self.local_root, exist_ok=True)
            self.drive_service.download_file(remote_item["id"], local_path)
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

        for path in plan.conflicts:
            self.state_db.set_status(path, "conflict")

        for path in plan.local_deletions:
            self.state_db.set_status(path, "pending_delete")

        for path in plan.remote_deletions:
            self.state_db.set_status(path, "pending_delete")

        for path in plan.stale_records:
            self.state_db.remove_file(path)

        return plan
