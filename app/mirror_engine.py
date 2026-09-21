"""Resumable whole-My-Drive mirror.

The metadata catalog and the downloads run CONCURRENTLY: the Drive listing
streams page-by-page, each discovered file is immediately queued, and a
worker pool downloads while more pages arrive. The metadata is flagged
complete only when the full listing succeeds. Every item's status is tracked
in SQLite, so stopping and restarting resumes exactly where it left off.
"""

import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, Optional

from app.drive_service import FILE_FIELDS, FOLDER_MIME, GoogleDriveService
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

        resumed = self.catalog.get_meta("list_page_token")
        self.catalog.set_meta("list_progress", "0")
        if resumed:
            self._progress("Resuming metadata catalog from where it stopped...")
        else:
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
        """Resumable, interruptible whole-Drive listing with live progress."""
        svc = GoogleDriveService()
        start_token = self.catalog.get_meta("list_page_token") or None

        def on_page(pages: int, total: int) -> None:
            # Record progress every page so the GUI shows activity even before
            # the catalog is written (the listing itself can take minutes).
            self.catalog.set_meta("list_progress", str(total))
            self.catalog.set_meta("list_progress_pages", str(pages))
            if pages % 5 == 0:
                self._progress(f"  listing Drive... {total} items so far")

        return svc.list_all_my_drive(
            should_stop=lambda: self.stopped,
            on_page=on_page,
            start_page_token=start_token,
        )

    def _stream_catalog_pages(self, page_size: int = 1000):
        """Yield (pages, total) after cataloging each page of the Drive listing.

        Unlike build_catalog(), this resolves paths and writes items into the
        catalog page-by-page, so downloads can start on the first page while
        the rest of the Drive is still being listed.
        """
        svc = GoogleDriveService()
        service = svc.get_service()
        root_id = svc.get_root_id()
        by_id: Dict[str, Dict] = {}
        page_token: Optional[str] = None
        pages = 0
        cataloged = 0

        def full_path(item) -> str:
            parts = [item["name"]]
            seen = {item["id"]}
            parent = (item.get("parents") or [None])[0]
            while parent and parent != root_id and parent in by_id and parent not in seen:
                seen.add(parent)
                parts.append(by_id[parent]["name"])
                parent = (by_id[parent].get("parents") or [None])[0]
            return "/".join(reversed(parts))

        fields = f"nextPageToken, files({FILE_FIELDS})"
        while not self.stopped:
            for attempt in range(4):
                try:
                    results = service.files().list(
                        q="trashed = false",
                        pageSize=page_size,
                        spaces="drive",
                        fields=fields,
                        pageToken=page_token,
                    ).execute()
                    break
                except (socket.timeout, TimeoutError, ConnectionError):
                    if attempt == 3:
                        raise
                    time.sleep(2 * (attempt + 1))
            for item in results.get("files", []):
                if item.get("trashed"):
                    continue
                by_id[item["id"]] = item
                path = full_path(item)
                if not path:
                    continue
                self.catalog.upsert_item(
                    path=path,
                    remote_id=item["id"],
                    is_folder=(item.get("mimeType") == FOLDER_MIME),
                    size=_safe_int(item.get("size")),
                    remote_md5=item.get("md5Checksum"),
                    remote_modified_at=item.get("modifiedTime"),
                )
                cataloged += 1
            pages += 1
            self.catalog.set_meta("list_progress", str(cataloged))
            yield pages, cataloged
            page_token = results.get("nextPageToken")
            if not page_token:
                break

        if not self.stopped:
            self.catalog.mark_catalog_complete(cataloged)

    def run(self) -> Dict[str, int]:
        """Catalog and download CONCURRENTLY.

        The metadata listing streams page-by-page; each discovered file is
        immediately queued, and the worker pool downloads while more pages
        arrive. The progress total is 'known so far' and grows as new files
        are discovered (e.g. 150 of 20,000, still counting).
        """
        self.catalog.reset_in_flight()

        if self.catalog.is_catalog_complete():
            self._progress("Metadata already complete; downloading.")
        else:
            self._progress("Reading Drive and downloading as files are found...")

        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            in_flight = []

            def drain():
                nonlocal in_flight
                in_flight = [f for f in in_flight if not f.done()]
                while not self.stopped and len(in_flight) < self.max_workers * 2:
                    batch = self.catalog.claim_batch(self.max_workers)
                    if not batch:
                        break
                    for item in batch:
                        in_flight.append(pool.submit(self._download_one, item))

            # Catalog (streams + queues files) while downloads proceed.
            if not self.catalog.is_catalog_complete():
                for _pages, _total in self._stream_catalog_pages():
                    drain()
                    if self.stopped:
                        break
                if self.stopped:
                    self.catalog.mark_catalog_incomplete()

            # Finish draining the queue after the catalog is complete.
            while not self.stopped:
                drain()
                counts = self.catalog.status_counts()
                if counts.get("pending", 0) == 0 and counts.get("downloading", 0) == 0:
                    break
                if not in_flight:
                    break
                in_flight[0].result(timeout=5)

        return self.catalog.status_counts()

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
