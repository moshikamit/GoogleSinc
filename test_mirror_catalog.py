"""Offline tests for the mirror catalog (no network / Drive access)."""

import os
import tempfile

from app.mirror_catalog import MirrorCatalog


def make_db(tmpdir: str) -> MirrorCatalog:
    return MirrorCatalog(os.path.join(tmpdir, "catalog.db"))


def test_catalog_complete_flag() -> None:
    with tempfile.TemporaryDirectory() as t:
        db = make_db(t)
        assert not db.is_catalog_complete()
        db.mark_catalog_complete(1234)
        assert db.is_catalog_complete()
        assert db.get_meta("catalog_total") == "1234"
        db.mark_catalog_incomplete()
        assert not db.is_catalog_complete()
    print("PASS: catalog-complete flag tracks metadata phase")


def test_queue_and_resume() -> None:
    with tempfile.TemporaryDirectory() as t:
        db = make_db(t)
        for i in range(5):
            db.upsert_item(f"folder/file{i}.txt", f"rid{i}", is_folder=False, size=10 + i)

        # Claim 2 -> they become 'downloading'
        batch = db.claim_batch(2)
        assert len(batch) == 2
        assert db.status_counts().get("downloading") == 2
        assert db.status_counts().get("pending") == 3

        # Simulate a crash: restart must requeue in-flight items.
        recovered = db.reset_in_flight()
        assert recovered == 2
        assert db.status_counts().get("pending") == 5

        # Mark some done/failed, verify counts.
        db.mark_done("folder/file0.txt")
        db.mark_failed("folder/file1.txt", "boom")
        counts = db.status_counts()
        assert counts["done"] == 1 and counts["failed"] == 1

        # Requeue transient failures, then finish everything.
        assert db.requeue_failed() == 1
        remaining = db.claim_batch(10)
        assert len(remaining) == 4  # 3 pending + 1 requeued
        for item in remaining:
            db.mark_done(item["path"])
        assert db.status_counts().get("done") == 5
    print("PASS: download queue claims, resumes after crash, completes")


def test_folders_not_downloaded() -> None:
    with tempfile.TemporaryDirectory() as t:
        db = make_db(t)
        db.upsert_item("Documents", "rid_dir", is_folder=True)
        db.upsert_item("Documents/a.txt", "rid_a", is_folder=False, size=5)
        batch = db.claim_batch(10)
        # Only the file is claimed, not the folder.
        assert [i["path"] for i in batch] == ["Documents/a.txt"]
    print("PASS: folders are cataloged but never queued for download")


if __name__ == "__main__":
    test_catalog_complete_flag()
    test_queue_and_resume()
    test_folders_not_downloaded()
    print("ALL MIRROR CATALOG TESTS PASSED")
