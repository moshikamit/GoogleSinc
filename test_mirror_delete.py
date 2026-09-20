"""Live Drive test: mirror deletion between local folder and Drive.

Verifies:
1. Deleting a synced local file deletes it from Drive (when enabled).
2. Deleting a synced remote file deletes the local copy.
3. The sync metadata record is removed after a mirrored delete.
4. Untracked remote files are NEVER deleted by sync.
"""

import os
import shutil
import tempfile

from app.sync_engine import SyncEngine
from app.sync_state import SyncStateDB

DRIVE_FOLDER_NAME = "GoogleSincTest"


def _cleanup_drive_folder(engine: SyncEngine) -> None:
    """Remove every file from the test Drive folder (test isolation)."""
    engine.ensure_drive_folder()
    for item in engine.drive_service.list_files_in_folder(engine.drive_folder["id"]):
        engine.drive_service.delete_file(item["id"])


def main() -> None:
    local_root = tempfile.mkdtemp(prefix="googlesinc_delete_")
    db_path = os.path.abspath(os.path.join(local_root, "..", "test_delete_state.db"))

    try:
        db = SyncStateDB(db_path)
        engine = SyncEngine(local_root, DRIVE_FOLDER_NAME, state_db=db)
        _cleanup_drive_folder(engine)

        # Setup: one synced file on both sides.
        with open(os.path.join(local_root, "victim.txt"), "w", encoding="utf-8") as f:
            f.write("this file will be deleted locally\n")
        engine.sync()
        assert db.get_file("victim.txt") is not None

        # Test 1: local deletion -> Drive deletion (mirror)
        os.remove(os.path.join(local_root, "victim.txt"))
        plan = engine.sync(execute_deletes=True)
        print(f"After local delete: {plan.summary()}")
        assert plan.local_deletions == ["victim.txt"], plan.summary()
        assert db.get_file("victim.txt") is None, "metadata record must be removed"

        remaining_remote = engine.drive_service.list_files_in_folder(
            engine.drive_folder["id"]
        )
        assert not any(
            item["name"] == "victim.txt" for item in remaining_remote
        ), "victim.txt must be deleted from Drive"
        print("PASS: local deletion mirrored to Drive")

        # Test 2: remote deletion -> local deletion (mirror)
        with open(os.path.join(local_root, "victim2.txt"), "w", encoding="utf-8") as f:
            f.write("this file will be deleted on Drive\n")
        engine.sync()
        remote_item = next(
            item
            for item in engine.drive_service.list_files_in_folder(engine.drive_folder["id"])
            if item["name"] == "victim2.txt"
        )
        engine.drive_service.delete_file(remote_item["id"])

        plan = engine.sync(execute_deletes=True)
        print(f"After remote delete: {plan.summary()}")
        assert plan.remote_deletions == ["victim2.txt"], plan.summary()
        assert not os.path.exists(os.path.join(local_root, "victim2.txt"))
        assert db.get_file("victim2.txt") is None
        print("PASS: remote deletion mirrored to local")

        # Test 3: untracked remote file must survive sync untouched.
        engine.drive_service.upload_text_file(
            name="not_synced.txt",
            content="uploaded outside the sync engine",
            parent_id=engine.drive_folder["id"],
        )
        plan = engine.sync(execute_deletes=True)
        names = [
            item["name"]
            for item in engine.drive_service.list_files_in_folder(engine.drive_folder["id"])
        ]
        assert "not_synced.txt" in names, "untracked remote file must never be deleted"
        assert plan.downloads == ["not_synced.txt"], plan.summary()
        print("PASS: untracked remote files are never deleted")

        print("ALL MIRROR-DELETE TESTS PASSED")
    finally:
        try:
            _cleanup_drive_folder(engine)
        except Exception:
            pass
        shutil.rmtree(local_root, ignore_errors=True)
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    main()
