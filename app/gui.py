"""PySide6 system-tray application for GoogleSinc.

The app lives in the system tray: a left-click opens the main window,
the tray menu offers sync now / pause / quit. Sync runs in the Qt thread
(it is fast and serialized by the service); conflicts appear in the
Conflicts tab where the user decides which copy stays.
"""

import logging
import os
import sys
from datetime import datetime
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
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
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.settings import AppSettings, load_settings, save_settings
from app.sync_engine import SyncEngine
from app.sync_service import WatchSyncService
from app.sync_state import SyncStateDB

logger = logging.getLogger(__name__)


def make_icon(color: str) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(QColor("transparent"))
    painter = QPainter(pixmap)
    painter.setBrush(QColor(color))
    painter.setPen(QColor(color))
    painter.drawEllipse(4, 4, 24, 24)
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
        tabs.addTab(self._conflicts_tab(), "Conflicts")
        tabs.addTab(self._settings_tab(), "Settings")
        self.setCentralWidget(tabs)

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
            self.show_window()

    def show_window(self) -> None:
        self.window.refresh_conflicts()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def quit(self) -> None:
        if self.service is not None:
            self.service.stop()
        QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # tray app keeps running
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("System tray is not available on this desktop.", file=sys.stderr)
        return 1
    tray_app = TrayApp()
    tray_app.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
