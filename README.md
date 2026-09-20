# GoogleSinc

A Linux desktop app for syncing a local folder with Google Drive.

## Goal

Build a practical first version that supports:
- Google account authentication
- folder selection
- local and remote sync
- basic conflict handling
- status and log reporting

## Stack

- Python 3.11+
- PySide6
- SQLite
- Google Drive API
- watchdog

## Initial milestones

1. Create the project skeleton
2. Authenticate with Google Drive
3. Sync a single test folder
4. Add local file watching
5. Add conflict handling
6. Package for Linux

## Running

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```
