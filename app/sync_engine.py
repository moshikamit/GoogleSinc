import os
from typing import Dict, List, Optional

from app.comparator import SyncPlan, build_sync_plan
from app.drive_service import GoogleDriveService
from app.local_scanner import compute_md5, delete_local_file, scan_folder
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

    def build_plan(self, execute_deletes: bool = False) -> SyncPlan:
        """Scan both sides and compare them against stored sync metadata."""
        if not self.drive_folder:
            self.ensure_drive_folder()

        local_scan = scan_folder(self.local_root)
        remote_files = self.drive_service.list_files_in_folder(self.drive_folder["id"])
        stored_state = self.state_db.list_files()
        return build_sync_plan(local_scan, remote_files, stored_state, execute_deletes)

    def sync(self, execute_deletes: bool = False) -> SyncPlan:
        """Run one metadata-driven sync pass.

        Uploads new or locally changed files, downloads new or remotely
        changed files, skips unchanged files, and records the resulting
        state in SQLite. Conflicts are reported but never executed.

        When execute_deletes is True, mirror deletion is active: a file
        deleted locally is deleted from Drive and vice versa, then its
        metadata record is removed. When False, deletions are only
        reported in the plan and marked pending_delete in the database.
        """
        plan = self.build_plan(execute_deletes=execute_deletes)
        remote_items = self.drive_service.list_files_in_folder(self.drive_folder["id"])
        stored_by_path = {row["path"]: row for row in self.state_db.list_files()}

        # Map each remote file name to its tracked item when possible, so
        # duplicate Drive files with the same name don't hide the tracked one.
        remote_by_name: Dict[str, Dict] = {}
        for item in remote_items:
            stored = stored_by_path.get(item["name"])
            existing = remote_by_name.get(item["name"])
            if existing is None:
                remote_by_name[item["name"]] = item
            elif stored and stored.get("remote_id") == item["id"]:
                remote_by_name[item["name"]] = item

        stale_remote_ids = [
            item["id"]
            for item in remote_items
            if (stored := stored_by_path.get(item["name"])) is not None
            and stored.get("remote_id")
            and item["id"] != stored["remote_id"]
        ]

        # Same-name Drive files that are not tracked in metadata are leftover
        # duplicates (e.g. from older pre-metadata uploads). Clean them up
        # instead of treating them as new remote content.
        untracked_duplicate_ids = [
            item["id"]
            for item in remote_items
            if item["name"] in stored_by_path
            and remote_by_name.get(item["name"], {}).get("id") != item["id"]
            and item["id"] not in stale_remote_ids
        ]
        if execute_deletes:
            for file_id in untracked_duplicate_ids:
                self.drive_service.delete_file(file_id)

        # A mirrored delete must not be undone by a leftover Drive file that
        # shares the name but has a different file id. Remove such stale
        # duplicates and convert the phantom download into a real deletion.
        if execute_deletes:
            delete_candidates = set(plan.local_deletions) | set(plan.downloads)
            for path in sorted(delete_candidates):
                stored = stored_by_path.get(path)
                if not stored or not stored.get("remote_id"):
                    continue
                same_name = [i for i in remote_items if i["name"] == path]
                if any(i["id"] == stored["remote_id"] for i in same_name):
                    continue  # tracked file still exists: not a mirrored delete
                for item in same_name:
                    self.drive_service.delete_file(item["id"])
                    stale_remote_ids.append(item["id"])
                if path in plan.downloads:
                    plan.downloads.remove(path)
                if path not in plan.local_deletions:
                    plan.local_deletions.append(path)

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

        if execute_deletes:
            # Mirror deletion: file removed locally -> delete from Drive.
            for path in plan.local_deletions:
                for item in remote_items:
                    if item["name"] == path and item["id"] not in stale_remote_ids:
                        self.drive_service.delete_file(item["id"])
                self.state_db.remove_file(path)

            # File removed on Drive -> delete the local copy.
            for path in plan.remote_deletions:
                delete_local_file(self.local_root, path)
                self.state_db.remove_file(path)
        else:
            for path in plan.local_deletions:
                self.state_db.set_status(path, "pending_delete")

            for path in plan.remote_deletions:
                self.state_db.set_status(path, "pending_delete")

        for path in plan.stale_records:
            self.state_db.remove_file(path)

        return plan

    # --- Conflict handling ---------------------------------------------
    # Policy: conflicts are NEVER resolved automatically. The conflicting
    # files are left exactly as they are on both sides; the record is
    # flagged 'conflict' so the user can inspect and decide explicitly.

    def list_conflicts(self) -> List[Dict]:
        """Return all files currently flagged as conflicting."""
        return self.state_db.list_by_status("conflict")

    def describe_conflict(self, path: str) -> Dict:
        """Gather local and remote facts for one conflicting file."""
        if not self.drive_folder:
            self.ensure_drive_folder()

        record = self.state_db.get_file(path)
        local_path = os.path.join(self.local_root, path)
        local_info = None
        if os.path.exists(local_path):
            stat = os.stat(local_path)
            local_info = {
                "exists": True,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "md5": compute_md5(local_path),
            }
        else:
            local_info = {"exists": False}

        remote_info = None
        for item in self.drive_service.list_files_in_folder(self.drive_folder["id"]):
            if item["name"] == path:
                remote_info = {
                    "exists": True,
                    "id": item["id"],
                    "size": item.get("size"),
                    "modified_at": item.get("modifiedTime"),
                    "md5": item.get("md5Checksum"),
                }
                break
        if remote_info is None:
            remote_info = {"exists": False}

        return {"path": path, "record": record, "local": local_info, "remote": remote_info}

    def resolve_conflict(self, path: str, keep: str) -> None:
        """Apply the user's decision for one conflicting file.

        keep='local'  uploads the local copy over the Drive copy.
        keep='remote' downloads the Drive copy over the local copy.
        Raises ValueError for an unknown choice or a non-conflicted file.
        """
        if keep not in ("local", "remote"):
            raise ValueError("keep must be 'local' or 'remote'")

        record = self.state_db.get_file(path)
        if record is None:
            raise ValueError(f"No tracked file at path: {path}")
        if record.get("sync_status") != "conflict":
            raise ValueError(f"File is not in conflict: {path}")

        if not self.drive_folder:
            self.ensure_drive_folder()

        remote_item = None
        for item in self.drive_service.list_files_in_folder(self.drive_folder["id"]):
            if item["name"] == path:
                remote_item = item
                break

        if keep == "local":
            local_path = os.path.join(self.local_root, path)
            if not os.path.exists(local_path):
                raise FileNotFoundError(f"Local file missing, cannot keep local: {path}")
            stat = os.stat(local_path)
            uploaded = self.drive_service.upload_local_file(
                local_path=local_path,
                remote_name=path,
                parent_id=self.drive_folder["id"],
                file_id=remote_item["id"] if remote_item else None,
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
        else:
            if remote_item is None:
                raise FileNotFoundError(f"Remote file missing, cannot keep remote: {path}")
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
