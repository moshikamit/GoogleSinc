"""Local folder change detection built on watchdog (inotify on Linux).

File-system events are noisy: saving a file often produces several events,
and editors may write via temporary files. This module collects raw events,
normalizes them to project-relative paths, and debounces them so a burst of
events for one file becomes a single queued change.
"""

import os
import threading
import time
from typing import Callable, Dict, Optional, Set

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

EventCallback = Callable[[Set[str]], None]

IGNORED_NAMES = {".git", "__pycache__"}
IGNORED_SUFFIXES = (".pyc", ".tmp", ".swp", "~")


def _is_ignored(relative_path: str) -> bool:
    parts = relative_path.split("/")
    if any(part in IGNORED_NAMES for part in parts):
        return True
    return relative_path.endswith(IGNORED_SUFFIXES)


class _EventCollector(FileSystemEventHandler):
    """Translate watchdog events into a set of changed relative paths."""

    def __init__(self, local_root: str, on_change: EventCallback) -> None:
        super().__init__()
        self.local_root = os.path.abspath(local_root)
        self.on_change = on_change

    def _enqueue(self, src_path: str, dest_path: Optional[str] = None) -> None:
        changed: Set[str] = set()
        for raw in (src_path, dest_path):
            if not raw:
                continue
            absolute = os.path.abspath(raw)
            if not absolute.startswith(self.local_root + os.sep):
                continue  # outside the synced folder
            relative = os.path.relpath(absolute, self.local_root).replace(os.sep, "/")
            if _is_ignored(relative):
                continue
            changed.add(relative)
        if changed:
            self.on_change(changed)

    def on_created(self, event: FileSystemEvent) -> None:
        self._enqueue(event.src_path)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._enqueue(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._enqueue(event.src_path, getattr(event, "dest_path", None))


class FolderWatcher:
    """Watch a local folder and report debounced batches of changed paths."""

    def __init__(
        self,
        local_root: str,
        on_batch: EventCallback,
        debounce_seconds: float = 1.0,
    ) -> None:
        self.local_root = local_root
        self.on_batch = on_batch
        self.debounce_seconds = debounce_seconds

        self._pending: Set[str] = set()
        self._lock = threading.Lock()
        self._timer: Optional[threading.Timer] = None
        self._observer: Optional[Observer] = None
        self._running = False

    def _collect(self, paths: Set[str]) -> None:
        with self._lock:
            self._pending.update(paths)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce_seconds, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            batch = set(self._pending)
            self._pending.clear()
            self._timer = None
        if batch:
            self.on_batch(batch)

    def start(self) -> None:
        if self._running:
            return
        handler = _EventCollector(self.local_root, self._collect)
        self._observer = Observer()
        self._observer.schedule(handler, self.local_root, recursive=True)
        self._observer.daemon = True
        self._observer.start()
        self._running = True

    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=timeout)
            self._observer = None

    @property
    def running(self) -> bool:
        return self._running
