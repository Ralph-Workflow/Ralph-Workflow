# Ralph Workflow - Acceptance Criteria

This document defines the complete acceptance criteria for the Ralph Workflow application.
Items marked with [DONE] are already implemented. All others are outstanding.

---

## AC-1: Multi-Workspace Management

### AC-1.1: Workspace Tab Bar
- [ ] A horizontal tab bar is displayed below the title bar showing all open workspaces
- [ ] Each tab displays the repository name (last path segment)
- [ ] Each tab shows a badge with the count of active runs (if > 0)
- [ ] Clicking a tab switches the active workspace
- [ ] Middle-clicking (or close button on tab) closes a workspace
- [ ] Tabs can be reordered via drag-and-drop
- [ ] When no workspaces are open, a welcome/onboarding view is shown

### AC-1.2: Workspace Lifecycle
- [ ] Users can open a workspace via File > Open Workspace (native directory picker)
- [ ] Users can open a workspace via a "+" button on the tab bar
- [ ] The selected directory must be a valid git repository; display an error if not
- [ ] Opening an already-open workspace switches to its tab (no duplicate)
- [ ] Each workspace maintains independent navigation state (sidebar selection, page)
- [ ] Switching workspaces restores the last-viewed page for that workspace
- [ ] Closing a workspace with active runs shows a confirmation dialog

### AC-1.3: Workspace Persistence
- [ ] Open workspaces are saved to GUI preferences on close
- [ ] On startup with "Restore Last Workspaces" enabled, all previous workspaces reopen
- [ ] A "Recent Workspaces" list is maintained (last 10) and shown in the welcome view
- [ ] Recent workspaces are accessible from File menu

---

## AC-2: Application Shell

### AC-2.1: Activity Bar
- [DONE] Vertical icon bar on the far left with navigation icons
- [DONE] Icons for: Home, Sessions, Worktrees, Configuration
- [ ] Additional icon for GUI Preferences (gear icon, bottom of bar)
- [ ] Badge indicators on icons (e.g., active run count on Sessions)
- [DONE] Active icon is visually highlighted with accent color
- [ ] Tooltip on hover showing the page name and keyboard shortcut

### AC-2.2: Sidebar
- [DONE] Collapsible sidebar panel (220px default width)
- [DONE] Content changes based on active activity bar item
- [DONE] Workspace/repo context switcher at the top
- [ ] Sidebar width is adjustable via drag handle
- [ ] Sidebar collapse state persists across sessions

### AC-2.3: Status Bar
- [ ] Fixed bar at the bottom of the window (28px height)
- [ ] Left section: active workspace name and current branch
- [ ] Center section: aggregated run status summary (e.g., "2 running, 1 paused")
- [ ] Right section: notification bell with unread count
- [ ] Clicking the notification bell opens a notification history panel
- [ ] Run status summary updates in real-time as runs change state

### AC-2.4: Keyboard Navigation
- [DONE] `g` then `h` navigates to Home
- [DONE] `g` then `s` navigates to Sessions
- [DONE] `g` then `w` navigates to Worktrees
- [DONE] `g` then `c` navigates to Configuration
- [ ] `g` then `p` navigates to GUI Preferences
- [DONE] `?` shows keyboard shortcuts help
- [ ] `Ctrl+N` opens new session wizard
- [ ] `Ctrl+,` opens GUI Preferences
- [ ] `Ctrl+Tab` switches to next workspace
- [ ] `Ctrl+Shift+Tab` switches to previous workspace
- [ ] `Ctrl+W` closes current workspace (with confirmation if runs active)
- [ ] `Ctrl+F` activates search in current context
- [ ] `Escape` closes any open modal or dialog

---

## AC-3: Home / Dashboard

### AC-3.1: Stats Overview
- [DONE] Displays stat cards: active worktrees, resumable runs
- [ ] Stat cards show trend indicators (up/down/flat vs previous period)
- [ ] Additional stat card: "Completed Today" with success rate percentage
- [ ] Cards use bento grid layout, responsive to window width

### AC-3.2: Active Runs List
- [DONE] Shows currently running sessions with status
- [ ] Each entry shows: worktree name, current phase with progress (e.g., "Dev 3/5"),
      agent name, elapsed time
- [ ] Clicking an entry navigates to the Run Detail page
- [ ] Live-updating (phase and iteration count update without page refresh)

### AC-3.3: Needs Attention Section
- [DONE] Shows interrupted/failed runs with resume action
- [ ] Each entry shows: worktree name, failure reason or pause cause, time since failure
- [ ] "Resume" button directly resumes the run without navigating away
- [ ] Failed entries show the last error message inline

### AC-3.4: Recent Completions
- [ ] Shows last 5-10 completed runs
- [ ] Each entry shows: worktree name, iteration count, review count, completion time
- [ ] Clicking navigates to the run detail (read-only completed view)

### AC-3.5: Quick Actions
- [DONE] "New Session" button prominently placed
- [DONE] Quick action cards for common operations
- [ ] Quick actions include: "New Session", "Create Worktree", "Open Configuration"

---

## AC-4: Session Management

### AC-4.1: Session List
- [DONE] Lists all sessions for the current workspace
- [DONE] Sessions show: run ID, status badge, worktree, phase, agent, timestamp
- [DONE] Filter by status (running, paused, completed, failed)
- [ ] Filter by worktree
- [ ] Search field that filters by description, worktree name, or run ID
- [ ] Sortable columns (click header to sort)
- [ ] Multi-select via checkboxes

### AC-4.2: Batch Operations
- [ ] When sessions are selected, a batch action bar appears
- [ ] Batch actions: Resume (for paused/failed), Cancel (for running), Delete
- [ ] Batch resume only applies to sessions that are paused or failed
- [ ] Confirmation dialog before batch cancel or delete
- [ ] Progress indicator during batch operations

### AC-4.3: New Session Wizard
- [DONE] Multi-step wizard: Prompt -> Configure -> Preflight -> Launch
- [DONE] Step 1: Prompt template selection and customization
- [DONE] Step 2: Agent selection, iteration count, review passes
- [DONE] Step 3: Preflight summary with launch confirmation

#### AC-4.3.1: Prompt Editor (Step 1)
- [DONE] Text area for writing/editing the prompt
- [DONE] Load from template picker
- [ ] Markdown preview toggle
- [DONE] AI-assisted prompt review with suggestions
- [ ] "Save as Template" to save current prompt for reuse
- [ ] Prompt history (last 10 prompts used in this workspace)
- [ ] Character/word count indicator

#### AC-4.3.2: Configuration (Step 2)
- [DONE] Developer agent selection dropdown
- [DONE] Reviewer agent selection dropdown
- [DONE] Developer iterations (numeric input with min/max)
- [DONE] Reviewer review passes (numeric input)
- [ ] Review depth dropdown (standard, comprehensive, security, incremental)
- [ ] Developer/Reviewer context level toggles (minimal vs normal)
- [ ] Verbosity slider (0-4 with labels: quiet, normal, verbose, full, debug)
- [ ] "Advanced" collapsible section for less common options
- [DONE] Launch presets (save/load/delete named configurations)

#### AC-4.3.3: Preflight (Step 3)
- [DONE] Summary of all launch parameters
- [ ] Effective configuration preview (merged from global + project + wizard overrides)
- [ ] Estimated resource usage indicator
- [DONE] Launch button with loading state

### AC-4.4: Session Launch
- [DONE] Launches Ralph Workflow CLI as background process via Tauri
- [DONE] Returns run ID and navigates to run detail
- [ ] Error handling: if CLI fails to start, show error with diagnostics
- [ ] The session appears immediately in the session list as "Starting"

---

## AC-5: Run Monitoring

### AC-5.1: Run Detail Page
- [DONE] Displays run metadata (ID, status, agent, worktree, timestamps)
- [DONE] Phase timeline visualization
- [DONE] Run log viewer
- [DONE] Resume button for paused/failed runs

### AC-5.2: Phase Timeline
- [DONE] Shows pipeline phases: Plan -> Develop -> Review -> Commit
- [ ] Active phase has animated indicator (pulse or progress animation)
- [ ] Each phase shows: status (pending/active/done/skipped), duration
- [ ] Clicking a completed phase shows its summary/output
- [ ] Phase-specific colors: Plan=purple, Develop=blue, Review=amber, Commit=green

### AC-5.3: Log Viewer
- [DONE] Displays run log output
- [ ] Real-time streaming via Tauri events (not just polling)
- [ ] Auto-scroll toggle (on by default, disables when user scrolls up)
- [ ] Search/filter within log output
- [ ] Log level filtering (info, warning, error)
- [ ] Download full log as file
- [ ] Monospace font (JetBrains Mono)
- [ ] ANSI color code support for colored log output
- [ ] Virtualized rendering for large logs (5000+ lines)

### AC-5.4: Iteration & Review Tracking
- [ ] Iteration history panel showing each dev iteration
- [ ] Per-iteration metrics: duration, files changed, test results
- [ ] Review cycle tracking: each review pass with findings count
- [ ] Visual indication of which iteration/review is current

### AC-5.5: Degraded State
- [DONE] Banner when run is in degraded state (retries/fallback agents)
- [ ] Shows which degradation occurred (retry count, fallback agent name)
- [ ] Links to relevant configuration to adjust retry/fallback settings

### AC-5.6: Run Lifecycle Actions
- [DONE] Resume button for paused or failed runs
- [ ] Cancel button for running sessions (with confirmation dialog)
- [ ] Retry button for failed runs (restarts from beginning)
- [ ] "Open in Terminal" to view raw CLI output
- [ ] "Open Worktree" to open the worktree directory in system file manager

---

## AC-6: Worktree Management

### AC-6.1: Worktree List
- [DONE] Lists all worktrees for the current repository
- [DONE] Shows: worktree name, branch, active run status
- [DONE] Visual distinction for main worktree
- [ ] Group worktrees by status: active (with runs), idle, main
- [ ] Show disk usage per worktree

### AC-6.2: Worktree Creation
- [DONE] Create worktree form with ticket number and short name
- [DONE] Auto-generates worktree name in `wt-N-name` format
- [DONE] Validates naming convention
- [ ] Option to base worktree on a specific branch or commit
- [ ] Option to immediately start a session after creation

### AC-6.3: Worktree Actions
- [DONE] "Start Session" button on idle worktrees
- [ ] "Open in File Manager" action
- [ ] "Delete Worktree" with confirmation (only if no active runs)
- [ ] "View Diff" showing changes in the worktree vs base branch

---

## AC-7: Configuration (Visual Settings)

### AC-7.1: Scope Tabs
- [DONE] Three tabs: Effective (read-only), Global, Project
- [ ] Effective tab shows merged configuration with source indicators
      (icon or label showing whether each value comes from default/global/project)
- [DONE] Global tab edits `~/.config/ralph-workflow.toml`
- [DONE] Project tab edits `.agent/ralph-workflow.toml`

### AC-7.2: Visual Form UI
- [ ] Settings displayed as structured form controls (not raw TOML)
- [ ] Numeric settings use number inputs with increment/decrement and min/max validation
- [ ] Enum settings (review_depth, verbosity) use dropdown selects
- [ ] Boolean settings use toggle switches
- [ ] String settings use text inputs
- [ ] Each setting has a label, description tooltip, and default value indicator
- [ ] Settings that differ from their default are visually highlighted
- [ ] Form sections are collapsible (General, Execution, Retry & Fallback, Git, Agents, API Keys)

### AC-7.3: Form Sections

#### General
- [ ] Verbosity (slider or dropdown, 0-4 with labels)
- [ ] Developer Iterations (number, 1-20)
- [ ] Reviewer Reviews (number, 0-10)
- [ ] Max Dev Continuations (number, 1-10)
- [ ] Review Depth (dropdown: standard, comprehensive, security, incremental)
- [ ] Prompt Path (text input with file picker)
- [ ] Templates Directory (text input with directory picker)

#### Execution
- [ ] Checkpoint Enabled (toggle)
- [ ] Isolation Mode (toggle)
- [ ] Interactive Mode (toggle)
- [ ] Developer Context (dropdown: minimal, normal)
- [ ] Reviewer Context (dropdown: minimal, normal)
- [ ] Force Universal Prompt (toggle)
- [ ] Auto-detect Stack (toggle)

#### Retry & Fallback
- [ ] Max Retries (number, 1-10)
- [ ] Max Same-Agent Retries (number, 1-5)
- [ ] Retry Delay ms (number, 100-60000)
- [ ] Backoff Multiplier (number, 1.0-5.0, step 0.1)
- [ ] Max Backoff ms (number, 1000-120000)
- [ ] Max Fallback Cycles (number, 1-20)

#### Git
- [ ] User Name (text)
- [ ] User Email (text, email validation)

#### Agent Profiles
- [ ] List of configured agents as cards
- [ ] Each agent card shows: name, provider, model, parser
- [ ] Add/edit/remove agents via dialog
- [ ] Developer chain: drag-to-reorder list of agents
- [ ] Reviewer chain: drag-to-reorder list of agents

#### API Keys
- [DONE] API key input fields per provider
- [DONE] Show/hide toggle for key visibility
- [ ] Validation indicator (key format check)
- [ ] "Test Connection" button per provider

### AC-7.4: Save/Revert
- [DONE] Dirty tracking (unsaved changes detection)
- [DONE] Save and Revert buttons
- [ ] Warning dialog when navigating away with unsaved changes
- [DONE] Validation errors shown inline before save is allowed
- [ ] Success toast after save

### AC-7.5: Raw TOML Fallback
- [ ] "View as TOML" toggle to switch between form view and raw TOML editor
- [DONE] Raw TOML editing with validation
- [ ] Changes in form view reflect in TOML view and vice versa

---

## AC-8: GUI Preferences

### AC-8.1: Appearance
- [ ] Theme selection (currently only dark; future: light, system)
- [ ] Accent color picker (default: #f59e0b amber)
- [ ] Sidebar width setting (pixels, 180-400)
- [ ] Base font size setting (12-18px)
- [ ] Monospace font selection

### AC-8.2: Behavior
- [ ] Run polling interval (milliseconds, 1000-30000)
- [ ] Log auto-scroll default (on/off)
- [ ] Log buffer size (max lines to keep in memory, 1000-50000)
- [ ] Confirm before cancelling runs (on/off)
- [ ] Show phase change notifications (on/off)

### AC-8.3: Notifications
- [ ] Desktop notifications master toggle
- [ ] Notify on run completion (on/off)
- [ ] Notify on run failure (on/off)
- [ ] Notify on phase change (on/off)
- [ ] Notification sound selection

### AC-8.4: Startup
- [ ] Restore last workspaces on startup (on/off)
- [ ] Default view when opening a workspace (Dashboard, Sessions, etc.)
- [ ] Check for updates on startup (on/off)

### AC-8.5: Keyboard Shortcuts
- [ ] List all keyboard shortcuts with their current bindings
- [ ] Each shortcut has a "Rebind" button to change the key combination
- [ ] Conflict detection when rebinding (warn if key already assigned)
- [ ] "Reset to Defaults" for all shortcuts

### AC-8.6: Persistence
- [ ] GUI preferences stored in Tauri app data directory (not in repo)
- [ ] Preferences load on startup before first render
- [ ] Changes apply immediately (no restart required)
- [ ] "Reset All to Defaults" button with confirmation

---

## AC-9: Onboarding / First Run

### AC-9.1: First Run Detection
- [ ] On first launch (no preferences file exists), show onboarding wizard
- [ ] Onboarding can be skipped entirely
- [ ] Onboarding can be re-triggered from Help menu

### AC-9.2: Wizard Steps
- [ ] Step 1: Welcome screen explaining what Ralph Workflow does
- [ ] Step 2: API key configuration (at least one provider required to proceed,
      but can be skipped with a warning)
- [ ] Step 3: Open first workspace (directory picker)
- [ ] Step 4: Quick tips and keyboard shortcuts summary
- [ ] Each step has Back/Next navigation
- [ ] Progress indicator showing current step

### AC-9.3: Post-Onboarding
- [ ] After completion, user lands on the Dashboard of their selected workspace
- [ ] If skipped, user sees the welcome/empty state with prompts to get started

---

## AC-10: Search

### AC-10.1: Global Search
- [ ] Search input accessible via `Ctrl+F` or search icon in toolbar
- [ ] Searches across: session descriptions, worktree names, run IDs
- [ ] Results grouped by type (sessions, worktrees, runs)
- [ ] Clicking a result navigates to the relevant detail page
- [ ] Search is scoped to the current workspace

### AC-10.2: Contextual Search
- [ ] In Sessions page: search filters the session list
- [ ] In Log Viewer: search highlights matching lines
- [ ] In Configuration: search filters visible settings

---

## AC-11: Notifications

### AC-11.1: Desktop Notifications
- [DONE] System notifications on run status changes
- [ ] Notification preferences respected (which events trigger notifications)
- [ ] Clicking a notification focuses the app and navigates to the relevant run

### AC-11.2: Notification Center
- [ ] Bell icon in status bar with unread count badge
- [ ] Clicking opens a panel listing recent notifications (last 50)
- [ ] Each notification shows: type icon, message, timestamp
- [ ] Notifications can be dismissed individually or all at once
- [ ] "Mark all as read" action

---

## AC-12: Prompt Templates

### AC-12.1: Template Library
- [ ] Browsable list of saved prompt templates
- [ ] Templates have: name, description, content, tags
- [ ] Create template from current prompt ("Save as Template")
- [ ] Edit and delete existing templates
- [ ] Templates stored in configurable directory (default: `~/.ralph/templates/`)

### AC-12.2: Template Usage
- [DONE] Template picker in new session wizard
- [ ] Preview template content before selecting
- [ ] Template variables/placeholders (e.g., `{{feature_name}}`) with fill-in form
- [ ] "Recently Used" section at top of template picker

---

## AC-13: Non-Functional Requirements

### AC-13.1: Performance
- [ ] App startup to interactive in < 2 seconds
- [ ] Workspace switching in < 200ms
- [ ] Log viewer handles 10,000+ lines without lag (virtualized rendering)
- [ ] Session list handles 100+ sessions without lag
- [ ] No unnecessary re-renders (OnPush change detection throughout)

### AC-13.2: Reliability
- [ ] Graceful handling of CLI process crashes (detect and show error)
- [ ] Graceful handling of lost file system access (repo moved/deleted)
- [ ] Auto-reconnect for log streaming on connection drop
- [ ] No data loss on unexpected app close (preferences auto-saved)

### AC-13.3: Accessibility
- [ ] All interactive elements reachable via keyboard
- [ ] Visible focus indicators on all focusable elements
- [ ] ARIA labels on icon-only buttons and status indicators
- [ ] Minimum 4.5:1 contrast ratio for text
- [ ] Status conveyed by icon+text, not color alone
- [ ] `aria-live` regions for dynamic content (log stream, status updates)
- [ ] Respect `prefers-reduced-motion`

### AC-13.4: Security
- [ ] API keys stored securely (OS keychain via Tauri secure storage, not plaintext)
- [ ] API keys masked in UI by default (show/hide toggle)
- [ ] No API keys in log output or error messages
- [ ] GUI preferences file has restricted permissions (600)

---

## Priority Summary

| Priority | Feature Area                      | Acceptance Criteria    |
|----------|-----------------------------------|------------------------|
| P0       | Multi-workspace tabs & switching  | AC-1                   |
| P0       | Status bar                        | AC-2.3                 |
| P0       | Visual config forms               | AC-7.2, AC-7.3         |
| P1       | GUI preferences                   | AC-8                   |
| P1       | Log streaming                     | AC-5.3                 |
| P1       | Enhanced run detail               | AC-5.2, AC-5.4, AC-5.5|
| P1       | Session batch operations          | AC-4.2                 |
| P1       | Session search                    | AC-4.1 (search)        |
| P2       | Onboarding wizard                 | AC-9                   |
| P2       | Prompt templates library          | AC-12                  |
| P2       | Notification center               | AC-11.2                |
| P2       | Agent chain editor                | AC-7.3 (agent section) |
| P3       | Global search                     | AC-10                  |
| P3       | Run comparison                    | (future, not yet spec'd)|
| P3       | Worktree deletion & diff          | AC-6.3                 |
