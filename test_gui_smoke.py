"""Offscreen smoke test for the PySide6 tray application.

Runs with the Qt 'offscreen' platform: builds the full widget tree and
tray object without opening windows or touching Google Drive.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from app.gui import TrayApp


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    tray_app = TrayApp()

    # Widget tree must exist with the expected controls.
    assert tray_app.window is not None
    assert tray_app.window.conflict_list is not None
    assert tray_app.window.log_view is not None
    assert tray_app.window.local_edit.text(), "local folder field must be filled"

    # Tray icon created (context-menu introspection is unreliable offscreen,
    # so we check the actions exist via the trigger callbacks instead).
    assert tray_app.tray is not None
    assert callable(tray_app.sync_now)
    assert callable(tray_app.show_window)
    assert callable(tray_app.quit)

    # Service is not started in the smoke test (would hit Drive).
    assert tray_app.service is None

    tray_available = QSystemTrayIcon.isSystemTrayAvailable()
    print(f"Tray icon created. System tray available here: {tray_available}")
    print("PASS: tray app constructs correctly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
