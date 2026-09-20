"""Live Drive test: incremental sync using the SQLite metadata layer.

Verifies the real behaviour that matters:
1. First sync uploads new local files.
2. Second sync uploads nothing (unchanged files are skipped).
3. Modifying one local file re-uploads only that file.
"""

import os
import shutil
import tempfile

from app.sync_engine import SyncEngine
from app.sync_state import SyncStateDB

DRIVE_FOLDER_NAME = "GoogleSincTest"


def main() -> None:
    local_root = tempfile.mkdtemp(prefix="googlesinc_meta_")
    db_path = os.path.join(local_root, "..", "test_sync_state.db")
    db_path = os.path.abspath(db_path)

    try:
        with open(os.path.join(local_root, "file_one.txt"), "w", encoding="utf-8") as f:
            f.write("first file v1\n")
        with open(os.path.join(local_root, "file_two.txt"), "w", encoding="utf-8") as f:
            f.write("second file v1\n")

        db = SyncStateDB(db_path)
        engine = SyncEngine(local_root, DRIVE_FOLDER_NAME, state_db=db)

        plan1 = engine.sync()
        print(f"First sync:  {plan1.summary()}")
        assert set(plan1.uploads) == {"file_one.txt", "file_two.txt"}, plan1.summary()
        assert db.count() == 2

        plan2 = engine.sync()
        print(f"Second sync: {plan2.summary()}")
        assert not plan2.uploads, "second sync must not re-upload unchanged files"
        assert not plan2.downloads
        assert set(plan2.unchanged) == {"file_one.txt", "file_two.txt"}, plan2.summary()

        with open(os.path.join(local_root, "file_one.txt"), "a", encoding="utf-8") as f:
            f.write("local edit\n")

        plan3 = engine.sync()
        print(f"Third sync:  {plan3.summary()}")
        assert plan3.uploads == ["file_one.txt"], plan3.summary()
        assert plan3.unchanged == ["file_two.txt"], plan3.summary()

        print("PASS: incremental sync skips unchanged files and uploads only edits")
    finally:
        shutil.rmtree(local_root, ignore_errors=True)
        if os.path.exists(db_path):
            os.remove(db_path)


if __name__ == "__main__":
    main()
