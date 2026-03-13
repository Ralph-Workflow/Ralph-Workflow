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
- [ ] Right section: notification bell with unread count, connection status indicator
      (green dot = connected, red dot = disconnected, with text label)
- [ ] Clicking the notification bell opens a notification history panel
- [ ] Run status summary updates in real-time as runs change state
- [ ] Connection status updates in real-time (connected/disconnected)

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
- [ ] `Ctrl+K` opens global search / command palette
- [ ] `Ctrl+F` activates search within current view (log viewer, session list, config)
- [ ] `Escape` closes any open modal or dialog
- [ ] Right-click context menus on list rows (sessions, worktrees) mirror the three-dot menu actions

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
- [ ] "Save as Template" to save current prompt for reuse
- [ ] Prompt history (last 10 prompts used in this workspace)
- [ ] Character/word count indicator

##### AI Prompt Assistant (Step 1 side panel)
- [ ] "[AI Assist ▾]" toggle button in the Step 1 toolbar opens/closes the AI Prompt
      Assistant panel. The button shows a chevron indicating open/closed state
- [ ] The panel is hidden by default; toggling it does not clear the prompt editor content
- [ ] Panel layout: prompt editor occupies the left or main area; assistant panel appears
      to the right (or below on narrow windows) when open. The editor remains fully usable
      while the panel is open
- [ ] Panel has two mode tabs: **"Describe what to build"** and **"Refine current prompt"**
- [ ] The active mode tab is visually highlighted (amber underline)
- [ ] "Describe what to build" mode — full definition of done:
  - [ ] Shows a conversational chat interface with a scrollable message history area
        and a text input at the bottom labeled "Describe the feature or problem…"
  - [ ] User types a natural-language description (e.g., "Add a dark mode toggle to the
        settings page, persisted in local storage") and submits via Enter or a send button
  - [ ] The assistant (using the Planning drain's first configured agent) responds with a
        structured, detailed prompt focused on end-result behavior, not implementation steps
  - [ ] The response appears as a "Suggested Prompt" card in the conversation, visually
        distinguished from the user's message (different background, label "Suggested Prompt")
  - [ ] Suggested Prompt card has two action buttons:
        **[Apply to Editor]** — replaces prompt editor content with the suggestion; panel
        remains open for further refinement.
        **[Edit & Apply]** — copies suggestion into editor AND closes the panel so the
        user can edit inline
  - [ ] Multiple back-and-forth exchanges are supported. Each exchange appends to the
        conversation history above. User can continue refining ("make it more concise",
        "focus on the API side") and receive updated suggestions
  - [ ] Conversation history persists for the duration of the wizard session (not saved
        between wizard opens)
- [ ] "Refine current prompt" mode — full definition of done:
  - [ ] On tab activation, the assistant immediately analyzes the current content of the
        prompt editor (no additional user input required to trigger analysis)
  - [ ] A loading indicator appears while analysis is in progress
  - [ ] The assistant returns a structured analysis card showing:
        — **Issues identified** (e.g., "Too much implementation detail", "Missing success
          criteria", "Focuses on how, not what")
        — **Suggested improved prompt** as a quoted/styled block
  - [ ] The analysis card has two action buttons:
        **[Apply Suggestion]** — replaces editor content with the improved prompt.
        **[Edit & Apply]** — copies suggestion into editor AND closes the panel
  - [ ] A "[Analyze again]" button triggers a fresh analysis of the current editor content
        (useful after manual edits)
  - [ ] If the editor is empty when the tab is activated, the assistant shows a prompt:
        "Write or load a prompt in the editor, then switch here to refine it"
- [ ] Agent used for both modes is the first agent in the Planning drain as configured
      in `~/.config/ralph-workflow.toml` or `.agent/ralph-workflow.toml`. If no Planning
      drain is configured, the panel shows: "No Planning agent configured. Set one up in
      Configuration to enable AI assistance." with a "[Go to Configuration →]" link and
      both mode tabs are disabled
- [ ] Ralph controls the system prompt sent to the AI agent. The user does not see and
      cannot modify the system prompt. The system prompt instructs the agent to return
      structured prompts focused on end-result behavior, not implementation details
- [ ] The panel displays the name of the agent being used (e.g., "Using: glm-agent via
      Planning drain") as a subtle label so the user knows which AI is assisting
- [ ] Closing the panel (toggle button or Escape) does not clear conversation history
      within the same wizard session. Reopening shows the prior conversation
- [ ] Panel has a minimum usable width; below a window width threshold, the panel
      stacks below the editor rather than side-by-side
- [ ] All assistant interactions are performed via the same Tauri/CLI mechanism used for
      running sessions. No separate API integration is required; the assistant call routes
      through the Planning drain agent's CLI tool

#### AC-4.3.2: Configuration (Step 2)
- [DONE] Developer iterations (numeric input with min/max)
- [DONE] Reviewer review passes (numeric input)

##### Smart Defaults — Prefill from configuration
- [ ] Step 2 reads effective configuration on open from `~/.config/ralph-workflow.toml`
      (global) and `.agent/ralph-workflow.toml` (project), merging them with project
      values taking precedence (matching Ralph's own merge order)
- [ ] All numeric fields (iterations, review passes) are prefilled with values from
      effective configuration, not hardcoded UI defaults
- [ ] Drain-to-chain bindings displayed in Step 2 reflect the bindings currently
      configured in the effective config — not placeholder or example values

##### Happy-path view (configured user — default state)
- [ ] When at least one agent chain and drain binding is configured, Step 2 opens in
      **collapsed/summary mode** showing a single line:
      `"[drain chain name] (agent1 → agent2 → agent3) · N iterations · M reviews · [review depth]"`
      Example: `"Developer (glm-agent → claude-opus → codex-o3) · 3 iterations · 2 reviews · standard"`
- [ ] Numeric spinners for **iterations** and **review passes** are directly editable inline
      in the summary row without needing to expand — these are the most commonly adjusted
      fields and must not require expansion to change
- [ ] A **[Customize ▾]** button appears to the right of the summary line. Clicking it
      expands the full configuration panel. Clicking again (now labeled **[Customize ▲]**)
      collapses it back to the summary
- [ ] The summary line updates in real-time as inline numeric spinners are changed
- [ ] If the user has not modified anything, they can proceed directly from the summary
      view to Preflight (Step 3) in one click — the wizard is optimized for experienced
      users running sessions frequently

##### Expanded view (after clicking [Customize ▾])
- [ ] The expanded panel shows all 6 drain dropdowns:
      Planning, Development, Analysis, Review, Fix, Commit — each populated with the
      names of all configured chains, with the currently configured chain pre-selected
- [ ] Iterations and review passes numeric inputs with min/max validation
- [ ] Review depth dropdown (standard, comprehensive, security, incremental)
- [ ] An **"Advanced"** collapsible sub-section for less common options (developer context,
      reviewer context, checkpoint enabled, isolation mode)
- [ ] A **"[Reset to defaults]"** button reverts all Step 2 overrides to the values read
      from effective configuration (not to hardcoded defaults)
- [ ] A subtle note is shown: *"Changes here apply to this session only and are not saved
      to your configuration files"*
- [ ] Collapsing back to summary after expanding preserves all changes made in the
      expanded view and reflects them in the summary line

##### Unconfigured user state (no agent chains configured)
- [ ] When no agent chains are configured (no `[chains]` entries in effective config),
      Step 2 shows a **"Setup Required"** callout in place of the summary/customize UI:
      — Amber/warning background with a distinct border
      — Heading: "Agent chains not configured"
      — Body: "You need at least one agent chain configured before launching a session.
        Set one up in Configuration, then return here."
      — Action link: **"[Go to Configuration →]"** (navigates to Configuration page,
        closing the wizard with a confirmation if the prompt is non-empty)
- [ ] The **[Next →]** button in the wizard footer is disabled when in the "Setup Required"
      state, with a tooltip: "Configure an agent chain before launching"
- [ ] The disabled next button is visually distinct (reduced opacity) but the tooltip
      explains why it is disabled — not a silent failure

##### Launch presets
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

### AC-5.7: Run Detail — State-Specific Views (UX-6.6, UX-3.9, UX-13.1)
- [ ] **Completed state:** Completion summary card with ✓ icon, total duration,
      and metric cards (iterations, reviews, files changed, tests). Phase timeline
      fully filled with all checkmarks. Changes tab selected by default
- [ ] **Failed state:** Error summary card with ✕ icon, plain-language error
      message, affected phase/iteration, and "What you can do" recovery guidance.
      Inline recovery actions: Resume, Retry from Beginning, Go to Config.
      Resume button also in page header. Log tab selected by default
- [ ] **Paused state:** Paused banner with ⏸ icon, checkpoint confirmation,
      which iteration will resume from, and time since pause. Resume as hero
      action (large amber button). Log tab selected by default
- [ ] Tab bar below phase timeline: [Log] [Changes] [Info] with amber underline
      on active tab. Default tab varies by run state (see above)

### AC-5.8: Changes Viewer (Diff View)
- [ ] Accessible from Run Detail as "Changes" tab alongside Log and Info
- [ ] Split layout: file tree (left, 240px) and diff panel (right, flex)
- [ ] File tree shows changed files in directory structure with +/- counts per file
- [ ] Syntax-highlighted unified diff with green-tinted added lines, red-tinted removed lines
- [ ] Summary bar: total files changed, total additions, total deletions
- [ ] Iteration filter dropdown: "All Iterations", "Iteration 1", "Iteration 2", etc.
- [ ] Per-iteration filtering shows only that iteration's diff
- [ ] Unified/side-by-side toggle
- [ ] For completed sessions: cumulative diff across all iterations shown by default
- [ ] "Copy as Patch" and "Open in Editor" actions on completed sessions
- [ ] Empty state when no changes yet: "Code changes will appear here as the AI develops"
- [ ] Clicking "Files Changed" count in Iteration History switches to Changes tab
      filtered to that iteration

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

#### Agent Chains & Drains
- [ ] **Chains subsection:** Named, ordered lists of agents displayed as drag-to-reorder pipelines
- [ ] Each chain shows: name, agent count, and agent cards in fallback order
- [ ] Each agent card in a chain shows: name, CLI tool, provider, model
- [ ] Create, rename, delete chains. Add/remove/reorder agents within chains
- [ ] **Drains subsection:** Six dropdown selectors binding pipeline phases to named chains:
      Planning, Development, Review, Fix, Commit, Analysis
- [ ] Each drain dropdown populated from configured chain names
- [ ] Helper text explaining drain semantics (matching agent strengths to pipeline phases)
- [ ] Multiple drains can share the same chain (e.g., Planning + Development → "developer")
- [ ] **Configured Agents subsection:** All defined agents as cards with Edit/Remove actions
- [ ] **Add Agent dialog — adaptive widget design:**
  - [ ] CLI Tool: radio group showing only installed tools (name, version, status). Auto-selects if only 1 installed
  - [ ] Provider: auto-filled read-only label for single-provider tools (Claude Code → Anthropic, Codex → OpenAI); dropdown with auth indicators for multi-provider tools (OpenCode)
  - [ ] Model (small list, ≤15): grouped dropdown by model family, showing context window size
  - [ ] Model (large list, >15): searchable combo box with type-ahead, grouped by family, showing context window + cost tier
  - [ ] Loading skeleton while models are fetched from provider API
  - [ ] Widget type adapts automatically based on the number of available models
- [ ] **Add Agent to Chain:** popover picker of existing agents not in chain, with "Create new agent..." inline option
- [ ] Agents can be shared across multiple chains

#### Agent Tools (Configuration Section)
- [ ] List of CLI tools Ralph delegates to (Claude Code, Claude Code Switch, Codex, OpenCode)
- [ ] Each tool shows: installed status, version, authentication status, health indicator
- [ ] Available models line summarizing models accessible through each tool
- [ ] "Open CLI Settings" button per tool (delegates auth/key management to each tool's own config)
- [ ] "Test Connection" button per tool (health check via trivial CLI invocation)
- [ ] "Install" action for tools not yet installed
- [ ] Note: Ralph Workflow does NOT manage API keys directly — each CLI tool handles its own authentication

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
- [ ] Notify on degraded condition detected (on/off)
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
- [ ] Step 2: Agent Tools check (auto-detects installed CLI tools; at least one
      must be installed and authenticated to proceed; tools can be installed or
      skipped individually)
- [ ] Step 3: Open first workspace (directory picker or drag-and-drop)
- [ ] Each step has Back/Next navigation
- [ ] Progress indicator showing current step (3 steps)

### AC-9.3: Post-Onboarding
- [ ] After completion, user lands on the Dashboard of their selected workspace
- [ ] A dismissible "Quick Tips" card appears at top of dashboard (shows once):
      keyboard navigation hints, first session guidance
- [ ] If skipped, user sees the welcome/empty state with prompts to get started

---

## AC-10: Search

### AC-10.1: Global Search / Command Palette
- [ ] Search input accessible via `Ctrl+K` or search icon in toolbar (following
      GitHub/Linear/Notion pattern — this is the app-wide command palette shortcut)
- [ ] Searches across: session descriptions, worktree names, run IDs
- [ ] Results grouped by type (sessions, worktrees, runs)
- [ ] Clicking a result navigates to the relevant detail page
- [ ] Search is scoped to the current workspace
- [ ] `Ctrl+F` is NOT used for global search — it is reserved for contextual/in-page
      search (per AC-2.4 and Jakob's Law UX-6.5)

### AC-10.2: Contextual Search
- [ ] `Ctrl+F` activates in-page search within the currently focused view
- [ ] In Sessions page: search filters the session list by description, worktree, or run ID
- [ ] In Log Viewer: search highlights matching lines, with next/prev match navigation
- [ ] In Configuration: search filters visible settings by label or description
- [ ] Pressing `Escape` dismisses the contextual search input and returns focus to the page

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

## AC-13: Help & In-App Documentation

### AC-13.1: Keyboard Shortcuts Overlay
- [DONE] `?` key opens keyboard shortcuts overlay from anywhere
- [ ] Shortcuts grouped by category: Navigation, Actions, Workspaces, General
- [ ] Dismissible via Escape or clicking outside

### AC-13.2: Contextual Help Tooltips
- [ ] `[?]` icon on every configuration field, wizard step, and drain binding
- [ ] Tooltip explains: what the setting does, when to change it, recommended values
- [ ] Analysis drain tooltip explains its role (checks code vs plan, GPT models recommended)
- [ ] Agent chain tooltip explains fallback behavior

### AC-13.3: Concepts Guide
- [ ] Accessible from `?` (Help) icon in activity bar and Help menu
- [ ] Collapsible sections covering: How It Works, The Pipeline, Agent Chains & Drains,
      Worktrees, Sessions & Runs, Configuration Scopes
- [ ] Plain-language explanations (no jargon assumed)
- [ ] Each concept links to its related page in the app (e.g., "Agent Chains" → Configuration)
- [ ] Drain descriptions include role-specific guidance:
      - Analysis: "Checks code against the plan after each dev iteration. GPT models recommended."
      - Planning: "Creates the implementation plan from your prompt."
      - Development: "Writes and modifies code to implement the plan."
      - Review: "Reviews code changes for quality and correctness."
      - Fix: "Addresses issues found during review."
      - Commit: "Generates commit messages for completed work."

### AC-13.4: Empty State Help
- [ ] Every empty state includes a brief explanation of the area's purpose
- [ ] Empty states include a link to the relevant Concepts Guide section
- [ ] First-time empty states (e.g., no sessions) include "[Learn how it works]" link

---

## AC-14: Agent Tools Manager

### AC-14.1: Tool Cards
- [ ] List of all CLI tools Ralph Workflow delegates to (Claude Code, Claude Code Switch, Codex, OpenCode)
- [ ] Each tool card shows: name, description, installed status, version, binary location,
      authentication status, health indicator (Ready / Needs setup / Not installed)
- [ ] Available models line showing models accessible through the tool (fetched dynamically)

### AC-14.2: Tool Actions
- [ ] "Test Connection" — runs health check via trivial CLI invocation, shows result
- [ ] "Open CLI Settings" — launches the tool's own configuration interface
- [ ] "Check for Updates" — compares installed version against latest; shows changelog if update available
- [ ] "Install" flow for uninstalled tools — platform-appropriate install method picker
      (npm, Homebrew, manual) with command preview before execution
- [ ] "Refresh Models" — refetches available models from all configured providers

### AC-14.3: Tool States
- [ ] Installed & configured: green health indicator, full details shown
- [ ] Installed but no auth: amber health indicator, prompt to open CLI settings
- [ ] Not installed: install button with description of tool's capabilities
- [ ] Update available: version comparison with "Update to vX.Y.Z" button and changelog

### AC-14.4: Access Points
- [ ] Accessible from Preferences menu
- [ ] Accessible from Configuration page's Agent Tools section via "Open Settings" links
- [ ] Shown during onboarding (Step 2) in simplified form

---

## AC-15: Non-Functional Requirements

### AC-15.1: Performance
- [ ] App startup to interactive in < 2 seconds
- [ ] Workspace switching in < 200ms
- [ ] Log viewer handles 10,000+ lines without lag (virtualized rendering)
- [ ] Session list handles 100+ sessions without lag
- [ ] No unnecessary re-renders (OnPush change detection throughout)

### AC-15.2: Reliability
- [ ] Graceful handling of CLI process crashes (detect and show error)
- [ ] Graceful handling of lost file system access (repo moved/deleted)
- [ ] Auto-reconnect for log streaming on connection drop
- [ ] No data loss on unexpected app close (preferences auto-saved)

### AC-15.3: Accessibility
- [ ] All interactive elements reachable via keyboard
- [ ] Visible focus indicators on all focusable elements
- [ ] ARIA labels on icon-only buttons and status indicators
- [ ] Minimum 4.5:1 contrast ratio for text
- [ ] Status conveyed by icon+text, not color alone
- [ ] `aria-live` regions for dynamic content (log stream, status updates)
- [ ] Respect `prefers-reduced-motion`

### AC-15.4: Security
- [ ] API key management delegated to each CLI tool's own configuration (not stored by Ralph)
- [ ] No API keys displayed, stored, or transmitted by the Ralph Workflow GUI
- [ ] No API keys in log output or error messages
- [ ] GUI preferences file has restricted permissions (600)

---

## Priority Summary

| Priority | Feature Area                          | Acceptance Criteria                   |
|----------|---------------------------------------|---------------------------------------|
| P0       | Multi-workspace tabs & switching      | AC-1                                  |
| P0       | Status bar                            | AC-2.3                                |
| P0       | Visual config forms                   | AC-7.2, AC-7.3                        |
| P1       | AI Prompt Assistant (wizard Step 1)   | AC-4.3.1 (AI Prompt Assistant)        |
| P1       | Smart wizard defaults (Step 2)        | AC-4.3.2 (Smart Defaults, Happy Path) |
| P1       | GUI preferences                       | AC-8                                  |
| P1       | Log streaming                         | AC-5.3                                |
| P1       | Enhanced run detail                   | AC-5.2, AC-5.4, AC-5.5               |
| P1       | Run detail state views                | AC-5.7                                |
| P1       | Changes viewer (diff)                 | AC-5.8                                |
| P1       | Session batch operations              | AC-4.2                                |
| P1       | Session search                        | AC-4.1 (search)                       |
| P1       | Help & in-app docs                    | AC-13                                 |
| P1       | Agent tools manager                   | AC-14                                 |
| P2       | Onboarding wizard                     | AC-9                                  |
| P2       | Prompt templates library              | AC-12                                 |
| P2       | Notification center                   | AC-11.2                               |
| P2       | Agent chain editor                    | AC-7.3 (agent section)                |
| P3       | Global search / command palette       | AC-10.1                               |
| P3       | Contextual in-page search             | AC-10.2                               |
| P3       | Run comparison                        | (future, not yet spec'd)              |
| P3       | Worktree deletion & diff              | AC-6.3                                |
