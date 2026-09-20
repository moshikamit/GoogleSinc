# GoogleSinc Work Plan

This document tracks the implementation of the Linux Google Drive sync app. Use it as the living task list for the project. Mark items as complete as you finish them.

## Status legend
- [ ] Not started
- [~] In progress
- [x] Completed

---

## Phase 0: Project foundation

### 0.1 Project setup
- [x] Create workspace folder for the project
- [x] Create initial project files
- [x] Create README with overview and goals
- [x] Add environment and dependency notes
- [x] Create .gitignore for Python project
- [x] Install Git on the machine
- [x] Initialize Git repository locally
- [x] Create GitHub repository
- [x] Connect local repo to GitHub
- [x] Push initial scaffold to GitHub

### 0.2 Environment setup
- [x] Check Python version
- [x] Create virtual environment
- [x] Install required Python packages
- [x] Validate basic app startup
- [x] Confirm GitHub sync workflow is working

---

## Phase 1: Research and design validation

### 1.1 Confirm architecture choices
- [x] Confirm Python + PySide6 approach
- [x] Confirm Google Drive API auth strategy
- [x] Confirm SQLite storage model
- [x] Confirm file watcher approach for Linux
- [x] Confirm basic sync policy and conflict strategy

### 1.2 Documentation review
- [ ] Review Google Drive API quickstart documentation
- [ ] Review PySide6 app setup basics
- [ ] Review watchdog usage for file changes
- [ ] Review OAuth2 flow requirements
- [ ] Review Linux file-system event handling options

---

## Phase 2: Core authentication and API integration

### 2.1 Google Drive auth
- [x] Create Google Cloud project
- [x] Enable Google Drive API
- [x] Configure OAuth client ID for desktop app
- [x] Store client credentials securely
- [x] Implement OAuth login flow
- [x] Save and refresh access tokens
- [x] Test successful authentication

### 2.2 Drive client basics
- [x] Create Google Drive service client wrapper
- [x] List files in a test folder
- [x] Create a test folder in Drive
- [x] Upload a sample file
- [x] Download a sample file
- [ ] Delete a sample file
- [x] Confirm API wrapper works reliably

---

## Phase 3: Local sync state and file scanning

### 3.1 Metadata storage
- [x] Design SQLite schema for tracked files
- [x] Create database layer
- [x] Add file record model
- [x] Add sync status fields
- [x] Add metadata update logic
- [ ] Add database migration strategy if needed

### 3.2 Local folder scanning
- [x] Create folder scan utility
- [x] Recursively enumerate files
- [x] Compute relative paths
- [x] Save discovered files to metadata
- [x] Detect missing or deleted files
- [x] Validate path normalization and safety

---

## Phase 4: File watching and change detection

### 4.1 Local change detection
- [ ] Implement file watch using watchdog or inotify-compatible approach
- [ ] React to create, modify, rename, and delete events
- [ ] Debounce repeated events
- [ ] Queue file change actions
- [ ] Validate that changes are captured reliably

### 4.2 Polling fallback
- [ ] Add periodic scan fallback for missed events
- [ ] Compare scan results with metadata
- [ ] Merge event queue results with scan results
- [ ] Confirm no missed updates under repeated test conditions

---

## Phase 5: Sync engine

### 5.1 Comparison logic
- [x] Compare local vs remote file list
- [x] Detect added files
- [x] Detect removed files
- [x] Detect modified files
- [ ] Detect renamed files
- [x] Detect unchanged files

### 5.2 Upload and download actions
- [x] Implement upload of new or changed local files
- [x] Implement download of new or changed remote files
- [x] Implement delete propagation rules
- [ ] Handle large file transfer reliability
- [ ] Add retries for transient failures

### 5.3 Conflict resolution
- [x] Define conflict policy for both-side changes (user decides; never auto-resolve)
- [x] Implement local-wins rule (on explicit user choice)
- [x] Implement remote-wins rule (on explicit user choice)
- [ ] Implement conflict-copy naming strategy (not needed under user-decides policy)
- [x] Log conflict results clearly
- [x] Validate conflict behavior with test cases

---

## Phase 6: Desktop application foundation

### 6.1 App shell
- [ ] Create PySide6 application entry point
- [ ] Create main window skeleton
- [ ] Add settings panel
- [ ] Add status bar or sync status display
- [ ] Add log area
- [ ] Add preferences storage

### 6.2 UI flow
- [ ] Add Google login action
- [ ] Add local folder selection
- [ ] Add remote folder selection
- [ ] Add sync mode selection
- [ ] Add start/stop sync controls
- [ ] Add manual sync trigger
- [ ] Add logout action

---

## Phase 7: Integration and production hardening

### 7.1 Sync loop and scheduler
- [ ] Implement periodic sync loop
- [ ] Add startup sync pass
- [ ] Add background processing thread
- [ ] Prevent overlapping sync runs
- [ ] Add pause/resume handling

### 7.2 Reliability
- [ ] Handle network failures gracefully
- [ ] Retry failed uploads/downloads
- [ ] Recover from partial file writes
- [ ] Preserve metadata across restart
- [ ] Validate state consistency after crashes

### 7.3 Error reporting
- [ ] Log sync events in a human-readable format
- [ ] Surface key errors in UI
- [ ] Provide troubleshooting messages
- [ ] Include retry guidance

---

## Phase 8: Testing and validation

### 8.1 Unit tests
- [x] Test file comparison logic
- [x] Test conflict handling logic
- [x] Test metadata storage logic
- [ ] Test path normalization
- [ ] Test sync queue logic

### 8.2 Integration tests
- [x] Test upload path end to end
- [x] Test download path end to end
- [x] Test delete propagation
- [ ] Test file change event handling
- [ ] Test app startup and restoration

### 8.3 Real-world validation
- [ ] Test with a small folder of sample files
- [ ] Test with mixed file types
- [ ] Test with nested directories
- [ ] Test with network interruption
- [ ] Test with repeated sync cycles

---

## Phase 9: Packaging and release readiness

### 9.1 Packaging for Linux
- [ ] Choose packaging target: AppImage or Flatpak
- [ ] Build application package
- [ ] Validate app runs from package
- [ ] Validate dependencies are bundled correctly
- [ ] Verify app can authenticate in packaged form

### 9.2 Release documentation
- [ ] Write installation guide
- [ ] Write usage guide
- [ ] Write troubleshooting guide
- [ ] Write known limitations section
- [ ] Create release notes for MVP

---

## Phase 10: Future roadmap

### 10.1 Later improvements
- [ ] Selective sync support
- [ ] Multiple account support
- [ ] File version comparison
- [ ] More advanced conflict resolution UI
- [ ] Better remote-side notifications
- [ ] Desktop notifications
- [ ] Better Linux integration and tray app support

---

## Milestone checkpoints

### Milestone 1: Working auth and Drive access
- [ ] Google auth works
- [ ] Drive folder listing works
- [ ] Sample file upload/download works

### Milestone 2: Local folder sync works
- [ ] File scan works
- [ ] Metadata DB works
- [ ] Local file changes are detected
- [ ] Uploads and downloads happen correctly

### Milestone 3: Conflict handling is reliable
- [ ] Two-way sync works in realistic tests
- [ ] Conflicts are handled by policy
- [ ] Retry logic is stable

### Milestone 4: Desktop app is usable
- [ ] UI is stable
- [ ] User can set up sync without code knowledge
- [ ] Basic app workflow works end to end

### Milestone 5: First release candidate
- [ ] Packaging works on Linux
- [ ] Documentation is complete
- [ ] Known limitations are documented
- [ ] Release can be shared with a small test group

---

## Notes for future use

- Keep this file updated after every significant milestone.
- Add dates to completed items when you finish them.
- If a task is too large, split it into smaller subtasks and add a new checklist item.
- If you learn a better approach, revise the plan rather than forcing the original design.

---

## Suggested first tasks to start with

1. Install Git
2. Initialize repo
3. Create GitHub repository
4. Push initial scaffold
5. Create Python virtual environment
6. Install dependencies
7. Verify Python app starts
8. Build Google Cloud project and OAuth credentials
9. Implement Drive API testing script
10. Confirm Drive folder listing works

This is the first concrete batch of tasks. Start from the top and work from there.
