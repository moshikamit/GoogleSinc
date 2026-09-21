#!/usr/bin/env python3
"""GoogleSinc mirror daemon — runs in the background, with start/stop/status.

Commands:
    python mirror_daemon.py start     # launch in background (detached)
    python mirror_daemon.py run       # run in the foreground (for debugging)
    python mirror_daemon.py stop      # stop the background daemon
    python mirror_daemon.py status    # show catalog/download progress

The daemon writes a PID file and a STOP flag it polls, so `stop` works even
though the process is detached from your terminal.
"""

import logging
import os
import sys
import time

from app.mirror_catalog import MirrorCatalog
from app.mirror_engine import MirrorEngine
from app.settings import load_settings

LOG_PATH = os.path.expanduser("~/.config/googlesinc/mirror.log")
PID_PATH = os.path.expanduser("~/.config/googlesinc/mirror.pid")
STOP_PATH = os.path.expanduser("~/.config/googlesinc/mirror.stop")


def _ensure_config_dir() -> None:
    os.makedirs(os.path.dirname(PID_PATH), exist_ok=True)


def _read_pid():
    try:
        with open(PID_PATH, encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _pid_running(pid) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _progress(msg: str) -> None:
    logging.info("%s", msg)
    print(msg, flush=True)


def run_foreground() -> int:
    """Run the mirror in the foreground (stop with Ctrl+C or a STOP flag)."""
    _ensure_config_dir()
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    settings = load_settings()
    # Mirror root: the whole-My-Drive local folder.
    local_root = getattr(settings, "mirror_root", None) or os.path.join(
        os.path.expanduser("~"), "GoogleDrive", "My Drive"
    )

    if os.path.exists(STOP_PATH):
        os.remove(STOP_PATH)
    with open(PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

    engine = MirrorEngine(local_root, progress=_progress, stop_file=STOP_PATH)
    try:
        while not engine.stopped:
            if os.path.exists(STOP_PATH):
                _progress("Stop flag detected; shutting down.")
                engine.stop()
                break
            # run() catalogs and downloads concurrently; the catalog total
            # grows as new files are discovered.
            counts = engine.run()
            _progress(f"Pass complete: {counts}")
            if counts.get("pending", 0) == 0 and counts.get("failed", 0) == 0:
                _progress("All files mirrored. Idling; will re-check periodically.")
                # Idle loop: re-check for new remote files every 60s.
                for _ in range(60):
                    if engine.stopped or os.path.exists(STOP_PATH):
                        engine.stop()
                        break
                    time.sleep(1)
            else:
                # Requeue transient failures and take another pass.
                engine.catalog.requeue_failed()
    except KeyboardInterrupt:
        _progress("Ctrl+C; stopping.")
        engine.stop()
    finally:
        for p in (PID_PATH, STOP_PATH):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass
    return 0


def start_background() -> int:
    if _pid_running(_read_pid()):
        print("Mirror daemon is already running.")
        return 1
    _ensure_config_dir()
    log = open(LOG_PATH, "a", encoding="utf-8")
    pid = os.fork()
    if pid > 0:
        print(f"Mirror daemon started (PID {pid}). Log: {LOG_PATH}")
        return 0
    # Child: detach and run.
    os.setsid()
    os.chdir(os.path.expanduser("~"))
    sys.stdin = open(os.devnull, "r")
    sys.stdout = log
    sys.stderr = log
    sys.argv = [sys.argv[0], "run"]
    return run_foreground()


def stop() -> int:
    pid = _read_pid()
    if not _pid_running(pid):
        print("Mirror daemon is not running.")
        return 1
    _ensure_config_dir()
    with open(STOP_PATH, "w", encoding="utf-8") as f:
        f.write("stop\n")
    print(f"Sent stop to daemon (PID {pid}); it will finish current downloads.")
    return 0


def status() -> int:
    catalog = MirrorCatalog()
    counts = catalog.status_counts()
    complete = catalog.is_catalog_complete()
    total = catalog.get_meta("catalog_total")
    pid = _read_pid()
    running = _pid_running(pid)

    print(f"Daemon:        {'RUNNING (pid %s)' % pid if running else 'not running'}")
    print(f"Metadata:      {'COMPLETE (%s items)' % total if complete else 'incomplete / in progress'}")
    print(f"Catalog rows:  {catalog.catalog_size()}")
    for state in ("done", "pending", "downloading", "failed", "skipped"):
        if counts.get(state):
            print(f"  {state:12} {counts[state]}")
    done = counts.get("done", 0)
    total_rows = catalog.catalog_size()
    if total_rows:
        print(f"Progress:      {done}/{total_rows} files done "
              f"({100.0 * done / total_rows:.1f}%)")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    command = sys.argv[1]
    if command == "run":
        return run_foreground()
    if command == "start":
        return start_background()
    if command == "stop":
        return stop()
    if command == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
