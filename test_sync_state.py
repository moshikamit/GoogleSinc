"""Offline test for the SQLite metadata layer and the sync comparator.

No network or Google credentials needed: this exercises SyncStateDB and
build_sync_plan with fake local/remote state.
"""

import os
import tempfile

from app.comparator import build_sync_plan
from app.sync_state import SyncStateDB


def test_sync_state_db() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db = SyncStateDB(os.path.join(tmpdir, "state.db"))

        db.upsert_file(
            path="notes.txt",
            remote_id="abc123",
            size=42,
            local_md5="local-md5",
            remote_md5="remote-md5",
            local_modified_at=1700000000.0,
            remote_modified_at="2026-09-20T10:00:00.000Z",
        )
        assert db.count() == 1, "expected one tracked file"

        row = db.get_file("notes.txt")
        assert row is not None
        assert row["remote_id"] == "abc123"
        assert row["size"] == 42
        assert row["local_md5"] == "local-md5"
        assert row["remote_md5"] == "remote-md5"
        assert row["sync_status"] == "synced"
        assert row["last_synced_at"], "last_synced_at should be set"

        # Upsert again with new local md5: updates instead of duplicating.
        db.upsert_file(path="notes.txt", local_md5="new-md5")
        assert db.count() == 1, "upsert must not duplicate rows"
        row = db.get_file("notes.txt")
        assert row["local_md5"] == "new-md5"
        assert row["remote_id"] == "abc123", "unset fields must be preserved"

        db.set_status("notes.txt", "conflict")
        assert db.get_file("notes.txt")["sync_status"] == "conflict"

        db.remove_file("notes.txt")
        assert db.count() == 0
        assert db.get_file("notes.txt") is None

    print("PASS: SyncStateDB upsert/get/update/remove")


def _stored(path, local_md5="same", remote_md5="same", remote_id="rid"):
    return {
        "path": path,
        "remote_id": remote_id,
        "size": 10,
        "local_md5": local_md5,
        "remote_md5": remote_md5,
        "local_modified_at": 1.0,
        "remote_modified_at": "2026-09-20T10:00:00.000Z",
        "sync_status": "synced",
        "last_synced_at": "2026-09-20T10:00:00.000Z",
    }


def _local(md5):
    return {"full_path": "/tmp/x", "size": 10, "modified_at": 1.0, "md5": md5}


def _remote(name, md5):
    return {
        "id": "rid-" + name,
        "name": name,
        "mimeType": "text/plain",
        "md5Checksum": md5,
        "size": "10",
        "modifiedTime": "2026-09-20T10:00:00.000Z",
    }


def test_comparator() -> None:
    # 1. New local file -> upload
    plan = build_sync_plan({"new.txt": _local("m1")}, [], [])
    assert plan.uploads == ["new.txt"], plan.summary()

    # 2. Unchanged on both sides -> skip
    plan = build_sync_plan(
        {"a.txt": _local("same")},
        [_remote("a.txt", "same")],
        [_stored("a.txt")],
    )
    assert plan.unchanged == ["a.txt"], plan.summary()
    assert not plan.uploads and not plan.downloads, plan.summary()

    # 3. Local modified -> upload
    plan = build_sync_plan(
        {"a.txt": _local("changed-local")},
        [_remote("a.txt", "same")],
        [_stored("a.txt")],
    )
    assert plan.uploads == ["a.txt"], plan.summary()

    # 4. Remote modified -> download
    plan = build_sync_plan(
        {"a.txt": _local("same")},
        [_remote("a.txt", "changed-remote")],
        [_stored("a.txt")],
    )
    assert plan.downloads == ["a.txt"], plan.summary()

    # 5. Both sides modified -> conflict
    plan = build_sync_plan(
        {"a.txt": _local("changed-local")},
        [_remote("a.txt", "changed-remote")],
        [_stored("a.txt")],
    )
    assert plan.conflicts == ["a.txt"], plan.summary()

    # 6. New remote file -> download
    plan = build_sync_plan({}, [_remote("cloud.txt", "m9")], [])
    assert plan.downloads == ["cloud.txt"], plan.summary()

    # 7. Missing locally, remote unchanged -> local deletion (reported)
    plan = build_sync_plan({}, [_remote("gone.txt", "same")], [_stored("gone.txt")])
    assert plan.local_deletions == ["gone.txt"], plan.summary()

    # 8. Missing locally AND remote changed meanwhile -> conflict
    plan = build_sync_plan(
        {}, [_remote("gone.txt", "remote-moved-on")], [_stored("gone.txt")]
    )
    assert plan.conflicts == ["gone.txt"], plan.summary()

    # 9. Missing on both sides -> stale record cleanup
    plan = build_sync_plan({}, [], [_stored("old.txt")])
    assert plan.stale_records == ["old.txt"], plan.summary()

    print("PASS: comparator handles add/change/delete/conflict/unchanged")


if __name__ == "__main__":
    test_sync_state_db()
    test_comparator()
    print("ALL OFFLINE METADATA TESTS PASSED")
