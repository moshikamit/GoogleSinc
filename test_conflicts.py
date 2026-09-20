"""Live Drive test: conflicts are surfaced, never auto-resolved.

Verifies the chosen policy:
1. A both-sides change produces a conflict and leaves BOTH files untouched.
2. The conflict appears in list_conflicts() until the user decides.
3. resolve_conflict(..., 'local') uploads the local copy and clears the flag.
4. resolve_conflict(..., 'remote') downloads the Drive copy and clears it.
5. Resolving a non-conflicted file is rejected.
"""

import os
import shutil
import tempfile

from app.sync_engine import SyncEngine
from app.sync_state import SyncStateDB

DRIVE_FOLDER_NAME = "GoogleSincTest"


def _cleanup(engine: SyncEngine) -> None:
    engine.ensure_drive_folder()
    for item in engine.drive_service.list_files_in_folder(engine.drive_folder["id"]):
        engine.drive_service.delete_file(item["id"])


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def main() -> None:
    local_root = tempfile.mkdtemp(prefix="googlesinc_conflict_")
    db_path = os.path.abspath(os.path.join(local_root, "..", "test_conflict_state.db"))
    engine = None

    try:
        db = SyncStateDB(db_path)
        engine = SyncEngine(local_root, DRIVE_FOLDER_NAME, state_db=db)
        _cleanup(engine)

        # Setup: a synced file.
        local_file = os.path.join(local_root, "shared.txt")
        with open(local_file, "w", encoding="utf-8") as f:
            f.write("version 1\n")
        engine.sync()

        # Change BOTH sides: edit locally, then overwrite Drive with other content.
        with open(local_file, "w", encoding="utf-8") as f:
            f.write("local version 2\n")
        engine.drive_service.upload_text_file(
            name="shared.txt",
            content="drive version 2\n",
            parent_id=engine.drive_folder["id"],
        )
        # Delete the old tracked remote file so only the new "drive version 2"
        # remains (simulates another device updating the file).
        stored = db.get_file("shared.txt")
        engine.drive_service.delete_file(stored["remote_id"])

        # Test 1: sync detects the conflict and touches NOTHING.
        plan = engine.sync(execute_deletes=True)
        print(f"Conflict sync: {plan.summary()}")
        assert plan.conflicts == ["shared.txt"], plan.summary()
        assert _read(local_file) == "local version 2\n", "local file must be untouched"
        assert db.get_file("shared.txt")["sync_status"] == "conflict"

        # Test 2: conflict is listed for the user.
        conflicts = engine.list_conflicts()
        assert [c["path"] for c in conflicts] == ["shared.txt"]
        print("PASS: conflict detected, both files untouched, flagged for user")

        # Test 3: user decides to keep the LOCAL copy.
        engine.resolve_conflict("shared.txt", "local")
        assert db.get_file("shared.txt")["sync_status"] == "synced"
        assert engine.list_conflicts() == []
        print("PASS: resolve keep-local uploads local copy and clears conflict")

        # Test 4: create another conflict, user keeps the REMOTE copy.
        with open(local_file, "w", encoding="utf-8") as f:
            f.write("local version 3\n")
        engine.drive_service.upload_text_file(
            name="shared.txt",
            content="drive version 3\n",
            parent_id=engine.drive_folder["id"],
        )
        engine.drive_service.delete_file(db.get_file("shared.txt")["remote_id"])
        plan = engine.sync(execute_deletes=True)
        assert plan.conflicts == ["shared.txt"], plan.summary()

        engine.resolve_conflict("shared.txt", "remote")
        assert _read(local_file) == "drive version 3\n", "local must be overwritten"
        assert db.get_file("shared.txt")["sync_status"] == "synced"
        print("PASS: resolve keep-remote downloads Drive copy and clears conflict")

        # Test 5: resolving a non-conflicted file is rejected.
        try:
            engine.resolve_conflict("shared.txt", "local")
        except ValueError:
            print("PASS: resolving a non-conflicted file is rejected")
        else:
            raise AssertionError("expected ValueError for non-conflicted file")

        print("ALL CONFLICT TESTS PASSED")
    finally:
        if engine is not None:
            try:
                _cleanup(engine)
            except Exception:
                pass
        shutil.rmtree(local_root, ignore_errors=True)
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    main()
