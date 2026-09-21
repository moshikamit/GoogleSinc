"""PySide6 system-tray application for GoogleSinc.

The app lives in the system tray: a left-click opens the main window,
the tray menu offers sync now / pause / quit. Sync runs in the Qt thread
(it is fast and serialized by the service); conflicts appear in the
Conflicts tab where the user decides which copy stays.
"""

import fcntl
import logging
import os
import signal
import subprocess
import sys
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.mirror_catalog import MirrorCatalog
from app.settings import AppSettings, load_settings, save_settings
from app.sync_engine import SyncEngine
from app.sync_service import WatchSyncService
from app.sync_state import SyncStateDB

logger = logging.getLogger(__name__)


def make_icon(color: str) -> QIcon:
    """Draw a colored circle with an up/down sync arrow glyph inside it."""
    size = 32
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor("transparent"))

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    # Colored status circle.
    painter.setBrush(QColor(color))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(2, 2, size - 4, size - 4)

    # White up/down arrows (the "sync" mark).
    glyph = QColor("white")
    painter.setBrush(glyph)
    painter.setPen(QPen(glyph, 2, Qt.SolidLine, Qt.RoundCap))

    up = QPainterPath()
    up.moveTo(12, 8)    # arrowhead tip
    up.lineTo(8, 14)    # left wing
    up.lineTo(11, 14)   # to shaft
    up.lineTo(11, 21)   # shaft down
    up.lineTo(13, 21)   # shaft width
    up.lineTo(13, 14)   # shaft up
    up.lineTo(16, 14)   # right wing
    up.closeSubpath()
    painter.drawPath(up)

    down = QPainterPath()
    down.moveTo(20, 24)  # arrowhead tip
    down.lineTo(16, 18)  # left wing
    down.lineTo(19, 18)  # to shaft
    down.lineTo(19, 11)  # shaft up
    down.lineTo(21, 11)  # shaft width
    down.lineTo(21, 18)  # shaft down
    down.lineTo(24, 18)  # right wing
    down.closeSubpath()
    painter.drawPath(down)

    painter.end()
    return QIcon(pixmap)


class Icons:
    """Lazily created status icons (must wait for QApplication to exist)."""

    _icons: dict = {}

    @classmethod
    def get(cls, name: str) -> QIcon:
        colors = {
            "idle": "#4CAF50",      # green  - in sync
            "active": "#2196F3",    # blue   - syncing
            "conflict": "#F44336",  # red    - needs attention
            "paused": "#9E9E9E",    # grey   - paused
        }
        if name not in cls._icons:
            cls._icons[name] = make_icon(colors[name])
        return cls._icons[name]


class GuiLogger(logging.Handler, QObject):
    """Route log records into the GUI log panel."""

    record = Signal(str)

    def __init__(self) -> None:
        logging.Handler.__init__(self)
        QObject.__init__(self)

    def emit(self, record: logging.LogRecord) -> None:
        self.record.emit(self.format(record))


class ConflictDialog(QDialog):
    """Show both versions of one conflict and let the user decide."""

    def __init__(self, info: dict, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.choice: Optional[str] = None
        self.setWindowTitle(f"Resolve conflict: {info['path']}")
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel(f"Both copies changed: {info['path']}"))

        form = QFormLayout()
        local = info["local"]
        remote = info["remote"]
        form.addRow("Local size:", QLabel(str(local.get("size", "-"))))
        form.addRow("Local modified:", QLabel(str(local.get("modified_at", "-"))))
        form.addRow("Drive size:", QLabel(str(remote.get("size", "-"))))
        form.addRow("Drive modified:", QLabel(str(remote.get("modified_at", "-"))))
        layout.addLayout(form)

        buttons = QDialogButtonBox()
        keep_local = buttons.addButton("Keep local copy", QDialogButtonBox.AcceptRole)
        keep_remote = buttons.addButton("Keep Drive copy", QDialogButtonBox.AcceptRole)
        buttons.addButton(QDialogButtonBox.Cancel)
        keep_local.clicked.connect(lambda: self._choose("local"))
        keep_remote.clicked.connect(lambda: self._choose("remote"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _choose(self, choice: str) -> None:
        self.choice = choice
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, app: "TrayApp") -> None:
        super().__init__()
        self.app = app
        self.setWindowTitle("GoogleSinc")
        self.resize(640, 480)

        tabs = QTabWidget()
        tabs.addTab(self._status_tab(), "Status")
        tabs.addTab(self._mirror_tab(), "Mirror")
        tabs.addTab(self._conflicts_tab(), "Conflicts")
        tabs.addTab(self._settings_tab(), "Settings")
        self.setCentralWidget(tabs)

        # Refresh the mirror progress every 10 seconds.
        self.mirror_timer = QTimer(self)
        self.mirror_timer.setInterval(10_000)
        self.mirror_timer.timeout.connect(self.refresh_mirror)
        self.mirror_timer.start()

    def _mirror_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Mirror all of Google Drive to this computer:"))

        self.mirror_state = QLabel("Daemon: not running")
        layout.addWidget(self.mirror_state)

        self.mirror_progress = QProgressBar()
        self.mirror_progress.setRange(0, 100)
        self.mirror_progress.setValue(0)
        layout.addWidget(self.mirror_progress)

        self.mirror_detail = QLabel("—")
        layout.addWidget(self.mirror_detail)

        controls = QHBoxLayout()
        self.mirror_start_btn = QPushButton("Start mirror")
        self.mirror_start_btn.clicked.connect(self.app.start_mirror)
        self.mirror_stop_btn = QPushButton("Stop mirror")
        self.mirror_stop_btn.clicked.connect(self.app.stop_mirror)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_mirror)
        controls.addWidget(self.mirror_start_btn)
        controls.addWidget(self.mirror_stop_btn)
        controls.addWidget(refresh_btn)
        layout.addLayout(controls)
        return widget

    def refresh_mirror(self) -> None:
        """Poll the mirror catalog and update the progress bar."""
        catalog = MirrorCatalog()
        counts = catalog.status_counts()
        running = self.app.mirror_running()
        complete = catalog.is_catalog_complete()

        state = "RUNNING" if running else "stopped"
        if not complete:
            state += " — building metadata catalog"
        self.mirror_state.setText(f"Daemon: {state}")

        done = counts.get("done", 0)
        total = catalog.catalog_size()
        pending = counts.get("pending", 0)
        failed = counts.get("failed", 0)
        if total:
            pct = int(100.0 * done / total)
            self.mirror_progress.setValue(pct)
            self.mirror_detail.setText(
                f"{done:,} of {total:,} files mirrored "
                f"({pending:,} pending, {failed:,} failed)"
            )
        else:
            self.mirror_progress.setValue(0)
            self.mirror_detail.setText("No files cataloged yet.")

        self.mirror_start_btn.setEnabled(not running)
        self.mirror_stop_btn.setEnabled(running)

    def _status_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.status_label = QLabel("Not syncing")
        layout.addWidget(self.status_label)

        controls = QHBoxLayout()
        sync_now = QPushButton("Sync now")
        sync_now.clicked.connect(self.app.sync_now)
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.app.toggle_pause)
        controls.addWidget(sync_now)
        controls.addWidget(self.pause_button)
        layout.addLayout(controls)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        layout.addWidget(self.log_view)
        return widget

    def _conflicts_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.addWidget(QLabel("Files changed on both sides - decide which copy stays:"))
        self.conflict_list = QListWidget()
        layout.addWidget(self.conflict_list)

        buttons = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_conflicts)
        resolve = QPushButton("Resolve selected...")
        resolve.clicked.connect(self._resolve_selected)
        buttons.addWidget(refresh)
        buttons.addWidget(resolve)
        layout.addLayout(buttons)
        return widget

    def _settings_tab(self) -> QWidget:
        widget = QWidget()
        form = QFormLayout(widget)

        self.local_edit = QLineEdit(self.app.settings.local_root)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_local)
        local_row = QHBoxLayout()
        local_row.addWidget(self.local_edit)
        local_row.addWidget(browse)
        form.addRow("Local folder:", local_row)

        self.drive_edit = QLineEdit(self.app.settings.drive_folder)
        form.addRow("Drive folder name:", self.drive_edit)

        self.deletes_check = QCheckBox("Mirror deletions to the other side")
        self.deletes_check.setChecked(self.app.settings.execute_deletes)
        form.addRow(self.deletes_check)

        self.poll_spin = QSpinBox()
        self.poll_spin.setRange(10, 3600)
        self.poll_spin.setValue(self.app.settings.poll_interval_seconds)
        form.addRow("Poll interval (seconds):", self.poll_spin)

        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self._apply_settings)
        form.addRow(apply_btn)
        return widget

    def _browse_local(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Choose local folder")
        if chosen:
            self.local_edit.setText(chosen)

    def _apply_settings(self) -> None:
        self.app.settings.local_root = self.local_edit.text()
        self.app.settings.drive_folder = self.drive_edit.text()
        self.app.settings.execute_deletes = self.deletes_check.isChecked()
        self.app.settings.poll_interval_seconds = self.poll_spin.value()
        save_settings(self.app.settings)
        self.app.restart_service()
        self.status_label.setText("Settings applied, sync restarted")

    def _resolve_selected(self) -> None:
        item = self.conflict_list.currentItem()
        if item is None:
            return
        self.app.resolve_conflict(item.text())
        self.refresh_conflicts()

    def refresh_conflicts(self) -> None:
        self.conflict_list.clear()
        for row in self.app.conflict_paths():
            QListWidgetItem(row, self.conflict_list)
        count = self.conflict_list.count()
        self.status_label.setText(
            f"{count} conflict(s) need your decision" if count else "In sync"
        )

    def append_log(self, line: str) -> None:
        self.log_view.append(line)


class TrayApp(QObject):
    conflict_detected = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.settings: AppSettings = load_settings()
        self.service: Optional[WatchSyncService] = None
        self.paused = False

        self.window = MainWindow(self)
        self.tray = QSystemTrayIcon(Icons.get("idle"))
        menu = QMenu()
        open_action = QAction("Open GoogleSinc")
        open_action.triggered.connect(self.show_window)
        sync_action = QAction("Sync now")
        sync_action.triggered.connect(self.sync_now)
        quit_action = QAction("Quit")
        quit_action.triggered.connect(self.quit)
        menu.addAction(open_action)
        menu.addAction(sync_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self._menu = menu
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.setToolTip("GoogleSinc - idle")

        self.gui_logger = GuiLogger()
        self.gui_logger.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
        )
        self.gui_logger.record.connect(self.window.append_log)
        logging.getLogger().addHandler(self.gui_logger)
        logging.getLogger().setLevel(logging.INFO)

        self.conflict_detected.connect(self._on_conflicts)

    def _engine(self) -> SyncEngine:
        return SyncEngine(
            self.settings.local_root,
            self.settings.drive_folder,
            state_db=SyncStateDB(),
        )

    def start(self) -> None:
        os.makedirs(self.settings.local_root, exist_ok=True)
        self.restart_service()
        self.tray.show()

    def restart_service(self) -> None:
        if self.service is not None:
            self.service.stop()
        os.makedirs(self.settings.local_root, exist_ok=True)
        self.service = WatchSyncService(
            self._engine(),
            poll_interval_seconds=self.settings.poll_interval_seconds,
            execute_deletes=self.settings.execute_deletes,
        )
        self.service.start()
        self._after_sync()

    def sync_now(self) -> None:
        if self.service is None or self.paused:
            return
        self.tray.setIcon(Icons.get("active"))
        plan = self.service.run_sync_once(trigger="manual")
        logger.info("manual sync: %s", plan.summary())
        self._after_sync()

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            if self.service is not None:
                self.service.stop()
            self.tray.setIcon(Icons.get("paused"))
            self.tray.setToolTip("GoogleSinc - paused")
            self.window.pause_button.setText("Resume")
        else:
            self.restart_service()
            self.window.pause_button.setText("Pause")

    def conflict_paths(self) -> list:
        try:
            return [row["path"] for row in self._engine().list_conflicts()]
        except Exception:
            logger.exception("failed to list conflicts")
            return []

    def resolve_conflict(self, path: str) -> None:
        engine = self._engine()
        try:
            info = engine.describe_conflict(path)
        except Exception:
            logger.exception("failed to describe conflict %s", path)
            return
        dialog = ConflictDialog(info, parent=self.window)
        if dialog.exec() == QDialog.Accepted and dialog.choice:
            try:
                engine.resolve_conflict(path, dialog.choice)
                logger.info("conflict resolved (%s kept): %s", dialog.choice, path)
            except (ValueError, FileNotFoundError) as exc:
                QMessageBox.warning(self.window, "Cannot resolve", str(exc))
        self._after_sync()

    def _after_sync(self) -> None:
        conflicts = self.conflict_paths()
        if conflicts:
            self.tray.setIcon(Icons.get("conflict"))
            self.tray.setToolTip(f"GoogleSinc - {len(conflicts)} conflict(s)")
            self.conflict_detected.emit()
        else:
            self.tray.setIcon(Icons.get("idle"))
            self.tray.setToolTip("GoogleSinc - in sync")
        self.window.refresh_conflicts()

    def _on_conflicts(self) -> None:
        self.tray.showMessage(
            "GoogleSinc",
            "A sync conflict needs your decision.",
            QSystemTrayIcon.Warning,
        )

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            # Left-click: open the main window.
            self.show_window()
        elif reason == QSystemTrayIcon.Context:
            # Right-click on Cinnamon/AppIndicator: show the menu ourselves.
            self.show_menu()

    def show_menu(self) -> None:
        from PySide6.QtGui import QCursor

        self._menu.popup(QCursor.pos())

    def show_window(self) -> None:
        self.window.refresh_conflicts()
        self.window.refresh_mirror()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    # --- Mirror daemon control (MMI) ----------------------------------

    _MIRROR_PID = os.path.expanduser("~/.config/googlesinc/mirror.pid")
    _MIRROR_STOP = os.path.expanduser("~/.config/googlesinc/mirror.stop")

    def _mirror_pid(self) -> Optional[int]:
        try:
            with open(self._MIRROR_PID, encoding="utf-8") as f:
                return int(f.read().strip())
        except (OSError, ValueError):
            return None

    def mirror_running(self) -> bool:
        pid = self._mirror_pid()
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def start_mirror(self) -> None:
        """Launch the mirror daemon in the background (detached)."""
        if self.mirror_running():
            return
        os.makedirs(os.path.dirname(self._MIRROR_PID), exist_ok=True)
        if os.path.exists(self._MIRROR_STOP):
            os.remove(self._MIRROR_STOP)
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log = open(os.path.expanduser("~/.config/googlesinc/mirror.log"), "a")
        subprocess.Popen(
            [sys.executable, os.path.join(project_root, "mirror_daemon.py"), "run"],
            cwd=project_root,
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # detach from our process group
        )
        logger.info("mirror daemon started")
        self.window.refresh_mirror()

    def stop_mirror(self) -> None:
        """Ask the mirror daemon to stop (writes the STOP flag it polls)."""
        if not self.mirror_running():
            return
        os.makedirs(os.path.dirname(self._MIRROR_STOP), exist_ok=True)
        with open(self._MIRROR_STOP, "w", encoding="utf-8") as f:
            f.write("stop\n")
        logger.info("mirror daemon stop requested")
        self.window.refresh_mirror()

    def quit(self) -> None:
        if self.service is not None:
            self.service.stop()
        QApplication.quit()


LOCK_PATH = os.path.join(
    os.path.expanduser("~"), ".config", "googlesinc", "googlesinc.lock"
)


def _acquire_single_instance_lock() -> Optional[int]:
    """Hold an exclusive lock so only one tray instance can run at a time.

    Returns the lock file descriptor, or None if another instance holds it.
    """
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    fd = os.open(LOCK_PATH, os.O_RDWR | os.O_CREAT)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode("ascii"))
    return fd


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # tray app keeps running
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("System tray is not available on this desktop.", file=sys.stderr)
        return 1

    lock_fd = _acquire_single_instance_lock()
    if lock_fd is None:
        print(
            "GoogleSinc is already running. Only one instance is allowed.\n"
            "Use the existing tray icon, or stop it with: pkill -f googlesinc.py",
            file=sys.stderr,
        )
        return 2

    tray_app = TrayApp()

    # Install the Ctrl+C (SIGINT) handler BEFORE starting any background
    # threads. Threads started earlier (the watchdog/inotify observer and the
    # polling loop) can mask SIGINT process-wide. Registering first, plus a
    # periodic timer that wakes the interpreter, lets Ctrl+C stop the app.
    # The handler calls quit() directly instead of raising, because a
    # KeyboardInterrupt raised inside a Qt timer callback is swallowed by the
    # event loop instead of unwinding app.exec().
    def _handle_sigint(_signum, _frame):
        print("\nCtrl+C received - shutting down GoogleSinc...", flush=True)
        tray_app.quit()

    signal.signal(signal.SIGINT, _handle_sigint)
    signal_timer = QTimer()
    signal_timer.start(200)
    signal_timer.timeout.connect(lambda: None)

    tray_app.start()

    try:
        return app.exec()
    except KeyboardInterrupt:
        tray_app.quit()
        return 0


if __name__ == "__main__":
    sys.exit(main())
