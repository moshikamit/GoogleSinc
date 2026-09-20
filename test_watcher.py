"""Offline tests for the folder watcher (no network or Drive access).

Verifies real inotify behavior on the local filesystem:
1. Creating a file produces a change batch containing its relative path.
2. A burst of rapid changes is debounced into one batch.
3. Editor temp files and VCS noise are ignored.
"""

import os
import tempfile
import threading
import time

from app.watcher import FolderWatcher

DEBOUNCE = 0.3


def _wait_for_event(event: threading.Event, timeout: float = 5.0) -> None:
    assert event.wait(timeout), "watcher did not report changes in time"


def test_watcher_detects_create_modify_delete() -> None:
    with tempfile.TemporaryDirectory() as root:
        batches: list = []
        event = threading.Event()

        def on_batch(paths):
            batches.append(paths)
            event.set()

        watcher = FolderWatcher(root, on_batch, debounce_seconds=DEBOUNCE)
        watcher.start()
        try:
            target = os.path.join(root, "hello.txt")
            with open(target, "w", encoding="utf-8") as f:
                f.write("v1\n")
            _wait_for_event(event)
            time.sleep(DEBOUNCE * 3)  # let the debounce window close
            assert any("hello.txt" in b for b in batches), batches

            batches.clear()
            event.clear()
            with open(target, "a", encoding="utf-8") as f:
                f.write("v2\n")
            _wait_for_event(event)
            assert any("hello.txt" in b for b in batches), batches

            batches.clear()
            event.clear()
            os.remove(target)
            _wait_for_event(event)
            assert any("hello.txt" in b for b in batches), batches
        finally:
            watcher.stop()

    print("PASS: watcher detects create / modify / delete")


def test_watcher_debounces_bursts() -> None:
    with tempfile.TemporaryDirectory() as root:
        batches: list = []
        event = threading.Event()

        def on_batch(paths):
            batches.append(paths)
            event.set()

        watcher = FolderWatcher(root, on_batch, debounce_seconds=DEBOUNCE)
        watcher.start()
        try:
            target = os.path.join(root, "burst.txt")
            for i in range(5):
                with open(target, "a", encoding="utf-8") as f:
                    f.write(f"line {i}\n")
                time.sleep(0.05)
            _wait_for_event(event)
            time.sleep(DEBOUNCE * 3)
            assert len(batches) <= 2, f"expected debouncing, got {len(batches)} batches"
            assert any("burst.txt" in b for b in batches), batches
        finally:
            watcher.stop()

    print("PASS: rapid save bursts are debounced")


def test_watcher_ignores_temp_and_vcs_files() -> None:
    with tempfile.TemporaryDirectory() as root:
        batches: list = []
        watcher = FolderWatcher(root, lambda p: batches.append(p), debounce_seconds=DEBOUNCE)
        watcher.start()
        try:
            os.makedirs(os.path.join(root, ".git"))
            for name in (".git/index", "draft.swp", "notes.txt~", "x.tmp"):
                with open(os.path.join(root, name), "w", encoding="utf-8") as f:
                    f.write("noise\n")
            time.sleep(DEBOUNCE * 4)
            flat = {p for b in batches for p in b}
            assert not flat, f"ignored files must not be reported, got {flat}"
        finally:
            watcher.stop()

    print("PASS: temp/swap/VCS files are ignored")


if __name__ == "__main__":
    test_watcher_detects_create_modify_delete()
    test_watcher_debounces_bursts()
    test_watcher_ignores_temp_and_vcs_files()
    print("ALL WATCHER TESTS PASSED")
