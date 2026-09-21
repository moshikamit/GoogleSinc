"""Resumable whole-My-Drive mirror.

Two phases:
  1. METADATA — catalog every file/folder in My Drive into SQLite. Resumable
     and flagged complete only when the full listing succeeds, so we always
     know whether the metadata is finished.
  2. DOWNLOAD — a thread pool pulls pending files from the catalog queue.
     Each item's status is tracked, so stopping and restarting resumes
     exactly where it left off.
"""

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

from app.drive_service import FOLDER_MIME, GoogleDriveService
from app.mirror_catalog import MirrorCatalog

logger = logging.getLogger(__name__)

# Google-native formats cannot be downloaded as-is; skip them in the mirror.
GOOGLE_NATIVE_PREFIX = "application/vnd.google-apps."


class MirrorEngine:
    """Build the metadata catalog, then download files with a thread pool."""

    def __init__(
        self,
        local_root: str,
        catalog: Optional[MirrorCatalog] = None,
        max_workers: int = 8,
        progress: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.local_root = local_root
        self.catalog = catalog or MirrorCatalog()
        self.max_workers = max_workers
        self._progress = progress or (lambda msg: None)
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    # --- Phase 1: metadata catalog (resumable, completeness-flagged) ---

    def build_catalog(self) -> int:
        """Catalog all of My Drive. Resumable; marks complete at the end.

        If a previous run finished the catalog, we reuse it. Otherwise we
        (re)build. The catalog_complete flag is only set after the full
        listing succeeds, so we always know whether metadata is done.
        """
        if self.catalog.is_catalog_complete():
            total = int(self.catalog.get_meta("catalog_total") or 0)
            self._progress(f"Metadata catalog already complete ({total} items).")
            return total

        self._progress("Building metadata catalog from Google Drive...")
        items, next_token, completed = self.drive_list_all()
        if not completed:
            # Interrupted: persist the page token so the next run resumes
            # the metadata phase instead of starting over.
            if next_token:
                self.catalog.set_meta("list_page_token", next_token)
            self.catalog.mark_catalog_incomplete()
            self._progress(
                f"Stopped during metadata build ({len(items)} items fetched); "
                "will resume next run."
            )
            return self.catalog.catalog_size()

        tree = self._index_by_path(items)
        count = 0
        for path, item in tree.items():
            if self.stopped:
                break
            self.catalog.upsert_item(
                path=path,
                remote_id=item["id"],
                is_folder=(item.get("mimeType") == FOLDER_MIME),
                size=_safe_int(item.get("size")),
                remote_md5=item.get("md5Checksum"),
                remote_modified_at=item.get("modifiedTime"),
            )
            count += 1
            if count % 500 == 0:
                self._progress(f"  cataloged {count} items...")

        if self.stopped:
            self.catalog.mark_catalog_incomplete()
            self._progress(f"Stopped; partial catalog has {count} items.")
            return count

        self.catalog.set_meta("list_page_token", "")  # finished: clear resume point
        self.catalog.mark_catalog_complete(count)
        self._progress(f"Metadata catalog COMPLETE: {count} items.")
        return count

    def drive_list_all(self):
        """Resumable, interruptible whole-Drive listing with progress."""
        svc = GoogleDriveService()
        start_token = self.catalog.get_meta("list_page_token") or None

        def on_page(pages: int, total: int) -> None:
            if pages % 10 == 0:
                self._progress(f"  listing Drive... {total} items so far")

        return svc.list_all_my_drive(
            should_stop=lambda: self.stopped,
            on_page=on_page,
            start_page_token=start_token,
        )

    def _index_by_path(self, items) -> Dict[str, Dict]:
        by_id = {item["id"]: item for item in items}

        def full_path(item) -> str:
            parts = [item["name"]]
            seen = {item["id"]}
            parent = (item.get("parents") or [None])[0]
            while parent and parent in by_id and parent not in seen:
                seen.add(parent)
                parts.append(by_id[parent]["name"])
                parent = (by_id[parent].get("parents") or [None])[0]
            return "/".join(reversed(parts))

        tree: Dict[str, Dict] = {}
        for item in items:
            if item.get("trashed"):
                continue
            path = full_path(item)
            if path:
                item = dict(item)
                item["path"] = path
                tree[path] = item
        return tree

    # --- Phase 2: threaded downloads (resumable queue) ------------------

    def download_pending(self) -> Dict[str, int]:
        """Download all pending catalog files using a worker pool."""
        if not self.catalog.is_catalog_complete():
            self._progress("Metadata not complete; finishing catalog first.")
            self.build_catalog()
            if self.stopped:
                return self.catalog.status_counts()

        # Recover anything a previous run left mid-download.
        recovered = self.catalog.reset_in_flight()
        if recovered:
            self._progress(f"Requeued {recovered} interrupted downloads.")

        counts = self.catalog.status_counts()
        pending = counts.get("pending", 0)
        self._progress(
            f"Starting downloads: {pending} pending, "
            f"{counts.get('done', 0)} already done."
        )

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not self.stopped:
                batch = self.catalog.claim_batch(self.max_workers)
                if not batch:
                    break
                list(pool.map(self._download_one, batch))

        if self.stopped:
            self._progress("Stop requested; finishing in-flight downloads...")
        return self.catalog.status_counts()

    def _download_one(self, item: Dict) -> None:
        path = item["path"]
        try:
            if self._is_skippable(item):
                self.catalog.mark_done(path)
                return
            local_path = os.path.join(self.local_root, path)
            os.makedirs(os.path.dirname(local_path) or self.local_root, exist_ok=True)

            if self._already_current(local_path, item):
                self.catalog.mark_done(path)
                return

            svc = GoogleDriveService()  # per-thread service (thread-safe)
            svc.download_file(item["remote_id"], local_path)
            self.catalog.mark_done(path)
            logger.info("downloaded: %s", path)
        except Exception as exc:  # noqa: BLE001 - record and continue
            self.catalog.mark_failed(path, str(exc)[:300])
            logger.warning("failed: %s (%s)", path, exc)

    def _is_skippable(self, item: Dict) -> bool:
        mime = item.get("mimeType") or ""
        return mime.startswith(GOOGLE_NATIVE_PREFIX)

    def _already_current(self, local_path: str, item: Dict) -> bool:
        if not os.path.exists(local_path):
            return False
        remote_md5 = item.get("remote_md5")
        if not remote_md5:
            return False
        from app.local_scanner import compute_md5

        return compute_md5(local_path) == remote_md5


def _safe_int(value) -> Optional[int]:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
