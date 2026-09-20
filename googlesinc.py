#!/usr/bin/env python3
"""GoogleSinc entry point: launches the system-tray application."""

import sys

from app.gui import main

if __name__ == "__main__":
    sys.exit(main())
