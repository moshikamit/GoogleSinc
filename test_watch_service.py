"""Live end-to-end test of the watch-sync service against real Drive.

Verifies:
1. The service runs an initial sync on startup.
2. Creating a local file triggers an automatic upload (no manual sync).
3. The polling fallback also syncs remote-side changes.
"""

import os
import shutil
import tempfile
import time

from app.sync_engine import SyncEngine
from app.sync_service import WatchSyncService
from app.sync_state import SyncStateDB

DRIVE_FOLDER_NAME = "GoogleSincTest"
POLL_SECONDS = 5


def _wait_until(predicate, timeout: float, what: str) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for: {what}")


def _remote_names(engine: SyncEngine):
    return {
        item["name"]
        for item in engine.drive_service.list_files_in_folder(engine.drive_folder["id"])
    }


def main() -> None:
    local_root = tempfile.mkdtemp(prefix="googlesinc_watch_")
    db_path = os.path.abspath(os.path.join(local_root, "..", "test_watch_state.db"))
    engine = None
    service = None

    try:
        db = SyncStateDB(db_path)
        engine = SyncEngine(local_root, DRIVE_FOLDER_NAME, state_db=db)
        engine.ensure_drive_folder()
        for item in engine.drive_service.list_files_in_folder(engine.drive_folder["id"]):
            engine.drive_service.delete_file(item["id"])

        service = WatchSyncService(
            engine,
            poll_interval_seconds=POLL_SECONDS,
            debounce_seconds=0.5,
            execute_deletes=True,
        )
        service.start()
        assert service.sync_count >= 1, "startup sync must run immediately"
        print("PASS: service started with initial sync")

        # Test 1: local create -> automatic upload via watcher.
        watched = os.path.join(local_root, "auto.txt")
        with open(watched, "w", encoding="utf-8") as f:
            f.write("created while service is running\n")
        _wait_until(
            lambda: "auto.txt" in _remote_names(engine),
            timeout=15.0,
            what="watcher-triggered upload of auto.txt",
        )
        print("PASS: new local file uploaded automatically (watcher)")

        # Test 2: remote-side change -> pulled by polling fallback.
        engine.drive_service.upload_text_file(
            name="from_drive.txt",
            content="created directly on Drive\n",
            parent_id=engine.drive_folder["id"],
        )
        _wait_until(
            lambda: os.path.exists(os.path.join(local_root, "from_drive.txt")),
            timeout=POLL_SECONDS * 4,
            what="polling fallback download of from_drive.txt",
        )
        print("PASS: remote change synced by polling fallback")

        print("ALL WATCH-SERVICE TESTS PASSED")
    finally:
        if service is not None:
            service.stop()
        if engine is not None:
            try:
                engine.ensure_drive_folder()
                for item in engine.drive_service.list_files_in_folder(engine.drive_folder["id"]):
                    engine.drive_service.delete_file(item["id"])
            except Exception:
                pass
        shutil.rmtree(local_root, ignore_errors=True)
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    main()
