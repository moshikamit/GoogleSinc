# Design Document: Linux Google Drive Sync Application

## 1. Objective

Build a Linux desktop application that syncs a local folder with Google Drive in a controlled, reliable, and user-friendly way. The initial scope is a practical desktop client that supports two-way sync for a chosen local directory and a single Google Drive root folder.

This project is intentionally scoped to be buildable by a single developer with Copilot assistance. The design aims for an MVP that is functional and maintainable, not a complete replacement for Google Drive for Desktop from day one.

## 2. Problem Statement

Linux users do not have a first-class Google Drive desktop sync client comparable to Google Drive for Desktop on Windows and macOS. Many users rely on browser uploads, manual copies, or third-party tools that are harder to trust, less polished, or limited in behavior.

The app should provide:
- a local folder that mirrors a Google Drive folder,
- automatic change detection,
- basic conflict handling,
- reliable OAuth-based authentication,
- a clear sync status and error reporting.

## 3. Goals

### Primary Goals
- Sync a local folder with Google Drive.
- Support both one-way and two-way sync modes.
- Detect local file changes and upload them.
- Detect remote file changes and download them.
- Handle common conflict cases consistently.
- Maintain a local metadata database for sync state.
- Offer a usable desktop UI for Linux.

### Secondary Goals
- Resume interrupted uploads/downloads.
- Keep a sync log.
- Support selective sync in a later phase.
- Support multiple accounts in a later phase.
- Offer configuration for sync intervals and policy.

## 4. Non-Goals

For the MVP, we intentionally do not include:
- full multi-account support,
- enterprise-level sharing controls,
- file version history browsing,
- remote editing integration,
- advanced conflict resolution UI,
- support for every edge-case cloud sync behavior.

## 5. Core Principles

1. Reliable sync over perfect feature breadth.
2. Local metadata is the source of truth for sync state.
3. Conflicts must be deterministic and explicit.
4. The app should be able to recover from temporary failures without losing track of work.
5. Linux first design, but architecture should be portable.

## 6. User Experience

### MVP Use Case
The user selects:
- a local sync folder,
- a Google Drive folder,
- a sync mode: one-way or two-way,
- a conflict policy.

The app then:
- authenticates with Google Drive,
- builds a sync state,
- watches the local folder,
- syncs changes to Drive,
- downloads remote updates,
- shows status, errors, and last sync time.

### UX Requirements
- Minimal setup friction.
- Clear indication of sync status.
- Easy way to inspect sync logs.
- Retry logic without manual intervention.
- Clear warning when a conflict occurs.

## 7. Functional Requirements

### Authentication
- Authenticate with Google OAuth2.
- Store tokens securely.
- Refresh tokens automatically.
- Support logout and re-authentication.

### Local Sync Folder
- Monitor local directory for file creation, deletion, rename, and modification.
- Support nested folders.
- Ignore temporary/editor cache files if configured.

### Drive Sync
- Sync files to a chosen Drive folder.
- Track remote file IDs, modified times, and sizes.
- Prevent duplicate file creation on repeated sync cycles.

### Sync Modes
- One-way local-to-drive
- One-way drive-to-local
- Two-way sync

### Conflict Handling
The MVP must support a clear default policy:
- local wins, or
- remote wins, or
- manual conflict marker

Recommended MVP default: local wins for local modifications; remote wins for remote modifications when no local change exists.

### Deletion Policy
- Delete local file when remote file is deleted if in two-way mode.
- Delete remote file when local file is deleted if allowed by policy.
- Provide a safe “trash” or “ignore” option in future versions.

## 8. Non-Functional Requirements

### Reliability
- Handle network failures gracefully.
- Retry transient errors.
- Preserve sync state across restarts.

### Performance
- Batch small file operations efficiently.
- Avoid full directory re-indexing on every cycle.
- Use file hashing or metadata checks only where necessary.

### Security
- Keep OAuth tokens in secure storage.
- Do not store secrets in plain text.
- Use HTTPS for all API traffic.

### Portability
- Run on Linux distributions with GTK/Qt support.
- Prefer standard system APIs and widely available libraries.

## 9. Proposed Architecture

### 9.1 High-Level Components

1. Desktop UI
   - GTK or Qt frontend
   - settings screen
   - sync status screen
   - log viewer

2. Sync Engine
   - responsible for deciding what changed and what to do
   - compares local state and remote state
   - enforces sync policy

3. Metadata Store
   - SQLite database storing tracked file records
   - local path, remote ID, hash, size, modified time, sync state

4. File Watcher
   - Linux inotify-based watcher for local folder changes
   - fallback polling if needed

5. Google Drive API Client
   - OAuth2 flow
   - file listing and metadata fetch
   - upload and download operations
   - folder creation and traversal

6. Background Scheduler
   - runs periodic sync cycles
   - handles startup sync and retry logic

### 9.2 Data Model

A file record should track at least:
- id
- local_path
- remote_id
- relative_path
- file_type
- last_seen_local_mtime
- last_seen_remote_mtime
- size
- checksum or fingerprint
- sync_status
- last_sync_time
- deleted_flag

This gives the engine enough information to detect whether a file changed locally, remotely, or both.

## 10. Recommended Tech Stack

### Frontend
- Qt with Qt Quick or GTK
- Reason: native-feeling Linux desktop app with good packaging support

### Backend / Sync Logic
- Python or Go
- Python is easier for quick iteration and Copilot support
- Go is excellent for concurrency and reliability

### Metadata / State
- SQLite
- simple, durable, and easy to debug

### File Watching
- inotify
- polling fallback for edge cases

### Google Integration
- Google Drive API v3
- OAuth2 client library

### Packaging
- AppImage, Flatpak, or native Debian package
- MVP should favor AppImage for easier testing

## 11. Sync Algorithm

### Normal Sync Flow
1. Load local sync state from the database.
2. Read current local filesystem state.
3. List remote folder contents from Drive.
4. Compare local and remote metadata.
5. Decide actions:
   - upload new file
   - download new file
   - update changed file
   - delete file if required
   - resolve conflict
6. Apply actions in a safe order.
7. Update metadata records.
8. Record sync log entries.

### Conflict Strategy
Conflicts should be handled deterministically:
- If both sides changed since last sync, apply rule:
  - local wins
  - remote wins
  - create conflict copy with suffix

Recommended MVP: create a conflict copy for ambiguous cases and log the event clearly.

## 12. Security and Privacy Considerations

- OAuth tokens must be stored securely.
- Only the selected Drive folder should be synced.
- User should be able to review permissions and revoke access.
- Avoid storing full file contents in logs.
- Use safe path handling to prevent path traversal and invalid file names.

## 13. Risks and Mitigations

### Risk: File conflict logic is too naive
Mitigation:
- implement explicit conflict policy
- create conflict copies if both sides changed

### Risk: Inotify misses events
Mitigation:
- add periodic polling scan
- detect missed file changes during validation loops

### Risk: Large file uploads/downloads fail midway
Mitigation:
- resume support and retry logic
- use chunked upload approach where necessary

### Risk: Remote folder structure becomes complex
Mitigation:
- start with a single root folder and a flat or shallow hierarchy

### Risk: UI becomes too ambitious
Mitigation:
- keep the first version minimal but polished enough to be usable

## 14. Proposed MVP Scope

The MVP should include:
- OAuth login
- local folder selection
- Google Drive folder selection
- one-way and two-way sync
- inotify + fallback polling
- conflict policy
- sync log
- basic status UI
- safe startup and reconnect handling

This is a realistic first release for an individual developer working with Copilot.

## 15. Milestone Plan

### Phase 1: Project Setup and Research (3-5 days)
- create repo structure
- define app skeleton
- confirm stack and toolchain
- build minimal Drive API integration test

### Phase 2: Authentication and API Basics (5-7 days)
- OAuth2 login flow
- token persistence
- list Drive files/folders
- CRUD test for a sample file

### Phase 3: Local Folder Watching and Metadata (5-7 days)
- inotify integration
- metadata database schema
- file event processing
- directory scan logic

### Phase 4: Sync Engine (7-10 days)
- compare local vs remote state
- decide actions
- implement upload/download/update/delete logic
- conflict detection

### Phase 5: Desktop UI and Polish (5-7 days)
- settings UI
- status panel
- sync log
- user feedback and error states

### Phase 6: Testing and Hardening (5-7 days)
- retry logic
- recovery from restarts
- edge-case testing
- packaging

## 16. Timeline Estimate

### Lightweight MVP with Copilot
For a single developer using Copilot actively, a realistic timeline is:

- 2 to 3 weeks for an initial working prototype
- 4 to 6 weeks for a solid MVP
- 8 to 12 weeks for a product-quality beta

### More realistic estimate by effort level

- Simple prototype: 2-3 weeks
- Usable MVP: 4-6 weeks
- Stable beta: 8-12 weeks
- Polished application: 3-5 months

This assumes a developer with good Linux and programming experience, but not necessarily extensive distributed sync background. Copilot can accelerate boilerplate, setup, and API integration but not replace true design and testing work.

## 17. Recommended Delivery Strategy

1. Build the prototype around a single sync folder.
2. Keep conflict policy simple and explicit.
3. Test with a small set of sample files before larger operations.
4. Add retries and restart recovery before refining UI.
5. Package early so real-world testing happens as soon as possible.

## 18. Recommended First Build Order

1. Google Drive API auth
2. list remote folder
3. local folder scan
4. metadata DB
5. local change watcher
6. sync diff engine
7. basic conflict handling
8. status UI
9. packaging and testing

This order reduces risk and keeps the early project grounded in something that is demonstrably useful.

## 19. Final Recommendation

This is a viable and worthwhile project for Linux. The key is to treat it as a sync engine problem first, not a desktop app problem first.

The most sensible MVP is:
- Qt or GTK frontend,
- Python or Go core,
- SQLite metadata,
- Google Drive API,
- inotify-based local watcher,
- deterministic conflict handling,
- simple but clear UX.

If the app is built with that architecture and an MVP-first mindset, it is realistic for a solo developer with Copilot support to produce a useful Linux Google Drive sync client in about 1 to 2 months, with a more robust beta in roughly 2 to 3 months.
