"""Continuous sync service: file watcher + periodic polling fallback.

The watcher reacts instantly to local changes; a polling loop runs a full
sync every so often to catch anything the watcher missed (and remote-side
changes, which inotify cannot see). Sync runs are serialized so a poll and
a watcher-triggered sync can never overlap.
"""

import logging
import threading
import time
from typing import Optional, Set

from app.comparator import SyncPlan
from app.sync_engine import SyncEngine
from app.watcher import FolderWatcher

logger = logging.getLogger(__name__)


class WatchSyncService:
    """Keep a local folder and a Drive folder continuously in sync."""

    def __init__(
        self,
        engine: SyncEngine,
        poll_interval_seconds: float = 60.0,
        debounce_seconds: float = 1.0,
        execute_deletes: bool = True,
    ) -> None:
        self.engine = engine
        self.poll_interval_seconds = poll_interval_seconds
        self.execute_deletes = execute_deletes

        self._sync_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._poll_thread: Optional[threading.Thread] = None
        self._watcher = FolderWatcher(
            engine.local_root,
            on_batch=self._on_local_changes,
            debounce_seconds=debounce_seconds,
        )
        self.last_plan: Optional[SyncPlan] = None
        self.sync_count = 0

    def run_sync_once(self, trigger: str = "manual") -> SyncPlan:
        """Run a single serialized sync pass."""
        with self._sync_lock:
            plan = self.engine.sync(execute_deletes=self.execute_deletes)
            self.last_plan = plan
            self.sync_count += 1
            logger.info("sync #%d (%s): %s", self.sync_count, trigger, plan.summary())
            return plan

    def _on_local_changes(self, paths: Set[str]) -> None:
        logger.info("local changes detected: %s", ", ".join(sorted(paths)))
        self.run_sync_once(trigger="watcher")

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(self.poll_interval_seconds):
            try:
                self.run_sync_once(trigger="poll")
            except Exception:  # keep the loop alive on transient failures
                logger.exception("polling sync failed")

    def start(self) -> None:
        """Start watching and polling. Also runs one initial sync."""
        self._stop_event.clear()
        self.run_sync_once(trigger="startup")
        self._watcher.start()
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._watcher.stop()
        if self._poll_thread is not None:
            self._poll_thread.join(timeout=5.0)
            self._poll_thread = None

    @property
    def running(self) -> bool:
        return self._watcher.running
