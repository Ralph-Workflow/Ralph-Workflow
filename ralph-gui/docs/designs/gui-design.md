# Ralph Workflow — Design Document

## 1. Vision

Ralph Workflow is an IDE-like desktop application for managing unattended AI
development pipelines. Users open repositories as workspaces, write prompts
describing features, and Ralph Workflow automatically plans, develops, reviews,
and commits code changes using AI agents.

The interface gives users a command center for this process: launch sessions,
monitor runs in real-time, investigate failures, manage parallel worktrees, and
configure the pipeline — all through a polished dark interface designed for
developers who want visibility and control over their AI development workflow.

**Design references:** VS Code, JetBrains Fleet, Warp terminal, Linear

---

## 2. Design System

### 2.1 Style

**Primary:** Dark Mode (OLED) + Minimalism
**Secondary:** Flat Design, Bento Grid (dashboard cards)
**Dashboard Style:** Real-Time Monitor + Terminal aesthetic

### 2.2 Color Palette

| Role             | Token                 | Hex       | Usage                                        |
|------------------|-----------------------|-----------|----------------------------------------------|
| Background       | `--bg-base`           | `#0f0f14` | App background, deepest layer                |
| Surface          | `--bg-surface`        | `#1a1a1e` | Cards, panels, sidebar                       |
| Surface Raised   | `--bg-raised`         | `#242428` | Hovered cards, active list items              |
| Surface Overlay  | `--bg-overlay`        | `#2a2a30` | Dropdowns, popovers, modals                  |
| Border           | `--border-default`    | `#333338` | Subtle dividers                               |
| Border Strong    | `--border-strong`     | `#47474f` | Focused input borders                         |
| Text Primary     | `--text-primary`      | `#f0f0f2` | Headings, primary content                     |
| Text Secondary   | `--text-secondary`    | `#94a3b8` | Labels, secondary content                     |
| Text Muted       | `--text-muted`        | `#64748b` | Placeholders, disabled text                   |
| Accent           | `--accent`            | `#f59e0b` | Active nav, primary buttons, highlights       |
| Accent Hover     | `--accent-hover`      | `#d97706` | Button hover state                            |
| Accent Subtle    | `--accent-subtle`     | `rgba(245,158,11,0.12)` | Active nav background            |
| Success          | `--status-success`    | `#22c55e` | Completed runs, passed checks                 |
| Warning          | `--status-warning`    | `#eab308` | Degraded runs, retries active                 |
| Error            | `--status-error`      | `#ef4444` | Failed runs, validation errors                |
| Info             | `--status-info`       | `#3b82f6` | Running state, informational                  |
| Phase Plan       | `--phase-plan`        | `#8b5cf6` | Planning phase indicator                      |
| Phase Develop    | `--phase-develop`     | `#3b82f6` | Development phase indicator                   |
| Phase Review     | `--phase-review`      | `#f59e0b` | Review phase indicator                        |
| Phase Commit     | `--phase-commit`      | `#22c55e` | Commit phase indicator                        |

### 2.3 Typography

| Role         | Font    | Weight  | Size    |
|--------------|---------|---------|---------|
| Display      | Inter   | 600     | 24px    |
| Heading      | Inter   | 600     | 18px    |
| Subheading   | Inter   | 500     | 14px    |
| Body         | Inter   | 400     | 14px    |
| Caption      | Inter   | 400     | 12px    |
| Monospace    | JetBrains Mono | 400 | 13px |

Line height: 1.5 for body, 1.3 for headings.

### 2.4 Spacing

4px base unit, 8px grid system. Standard spacing scale:
`4 | 8 | 12 | 16 | 24 | 32 | 48 | 64`

### 2.5 Elevation

| Level   | Usage                | Shadow                                    |
|---------|----------------------|-------------------------------------------|
| 0       | Flat (base bg)       | none                                      |
| 1       | Cards, sidebar       | `0 1px 3px rgba(0,0,0,0.4)`              |
| 2       | Dropdowns, popovers  | `0 4px 12px rgba(0,0,0,0.5)`             |
| 3       | Modals, dialogs      | `0 8px 24px rgba(0,0,0,0.6)`             |

### 2.6 Icons

Lucide icon set (consistent stroke weight, MIT licensed). No emojis as
structural icons.

### 2.7 Motion

- Micro-interactions: 150ms ease-out
- Panel transitions: 200ms ease-out
- Page transitions: 250ms ease-out
- Phase progress animation: continuous subtle pulse on active phase
- Respect `prefers-reduced-motion`

### 2.8 Status Badge System

Every status is conveyed through **color + icon + label** — never color alone.

| Status     | Color     | Icon              | Label        |
|------------|-----------|-------------------|--------------|
| Running    | `--info`  | Spinning circle   | "Running"    |
| Completed  | `--success` | Check circle    | "Completed"  |
| Failed     | `--error` | X circle          | "Failed"     |
| Paused     | `--warning` | Pause circle    | "Paused"     |
| Degraded   | `--warning` | Alert triangle  | "Degraded"   |
| Queued     | `--muted` | Clock             | "Queued"     |

The "Running" badge pulses subtly to indicate live activity.

---

## 3. Application Shell

### 3.1 Window Structure

```
+----------------------------------------------------------------------+
| [R] Ralph Workflow         [my-repo] [api-service] [+]        [_ □ X]|
+------+-----+---------------------------------------------------------+
|      |     |                                                         |
| [R]  | W   |  Main Content Area                                     |
|      | o   |                                                         |
| ──── | r   |                                                         |
|      | k   |                                                         |
| [⌂]  | s   |                                                         |
| [▶]  | p   |                                                         |
| [⎇]  | a   |                                                         |
| [⚙]  | c   |                                                         |
|      | e   |                                                         |
|      |     |                                                         |
|      | S   |                                                         |
|      | i   |                                                         |
|      | d   |                                                         |
|      | e   |                                                         |
|      | b   |                                                         |
|      | a   |                                                         |
|      | r   |                                                         |
|      |     |                                                         |
| ──── +-----+                                                         |
| [?]  | [+] |                                                         |
| [⚙]  |     |                                                         |
+------+-----+---------------------------------------------------------+
| ● my-repo · main     2 running, 1 attention    🔔 2    ● Connected  |
+----------------------------------------------------------------------+
```

### 3.2 Title Bar

The title bar is a single horizontal strip at the very top of the window:

- **Left:** The Ralph Workflow logo (the branded "R" from `logo.svg`) rendered
  in amber, followed by the text "Ralph Workflow" in primary text color
- **Center:** Workspace tabs — one tab per open repository. Each tab shows the
  repository folder name and a small badge if that workspace has active runs.
  The active tab is visually distinct (amber underline). Tabs can be reordered
  by dragging. Middle-click closes a tab. A `[+]` button at the end of the
  tab strip opens a new workspace
- **Right:** Standard window controls (minimize, maximize, close) styled to
  match the dark theme

### 3.3 Activity Bar

A narrow vertical strip (48px) along the left edge. Contains icon-only
navigation with tooltips on hover:

| Position | Icon         | Tooltip            | Shortcut    |
|----------|--------------|--------------------|-------------|
| Top      | Ralph "R"    | (brand mark, no action — the app identity) | — |
| —        | *(divider)*  | —                  | —           |
| Nav      | House        | "Home"             | `g` then `h` |
| Nav      | Play circle  | "Sessions"         | `g` then `s` |
| Nav      | Git branch   | "Worktrees"        | `g` then `w` |
| Nav      | Sliders      | "Configuration"    | `g` then `c` |
| —        | *(spacer)*   | —                  | —           |
| Bottom   | Circle help  | "Help & Shortcuts" | `?`         |
| Bottom   | Settings     | "Preferences"      | `g` then `p` |

The active item has an amber left border indicator and amber-tinted background.

Badge dots appear on icons when they have actionable items:
- Sessions icon: shows count of runs needing attention (failed/paused)
- The badge is a small amber dot with white number, positioned top-right

### 3.4 Workspace Sidebar

A 220px panel (collapsible) between the activity bar and main content. Its
content changes based on which activity bar item is selected:

**When Home is selected:**
- Header: workspace name in heading weight
- Quick stats: tiny inline counts (sessions, worktrees, active runs)
- Quick actions: "New Session" button (amber, prominent), "New Worktree" button
  (outlined)

**When Sessions is selected:**
- Header: "Sessions"
- Search input field
- Filter chips: All | Running | Paused | Completed | Failed
- Scrollable session list (name, status badge, time ago) — clicking a session
  opens its detail in the main content area

**When Worktrees is selected:**
- Header: "Worktrees"
- List of worktrees grouped: Active (with running sessions), Idle (no sessions)
- Each shows worktree name, branch, and status badge if a session is running
- "New Worktree" button at bottom

**When Configuration is selected:**
- Header: "Configuration"
- Section list: General, Execution, Retry & Fallback, Git Identity, Agent Chains & Drains, Agent Tools
- Clicking a section scrolls the main content to that section
- Small indicator dot on sections with values overridden from defaults

**Footer (always visible):**
- "Add Workspace" button at the bottom of the sidebar

### 3.5 Status Bar

A 28px strip at the very bottom of the window. Always visible.

```
| ● my-repo · main        2 running, 1 needs attention       🔔 2  ● Connected |
```

- **Left zone:** Active workspace name, current git branch
- **Center zone:** Run summary — count of running sessions and count needing
  attention. Clicking navigates to the Sessions page with the relevant filter
  active
- **Right zone:** Notification bell with unread count (clicking opens
  notification center panel), connection status indicator (green dot =
  connected, red dot = disconnected, with text label)

The status bar updates in real time. When a run completes or fails, the counts
change immediately and the notification bell badge increments.

### 3.6 Multi-Workspace Model

Each workspace is a repository root path. Workspaces are managed through:
- The workspace tab bar in the title bar
- "Add Workspace" button which opens the native file picker
- Recent workspaces in the welcome/onboarding screen
- Dragging a repository folder onto the application window

Each workspace maintains independent state:
- Its own navigation position (if you're viewing Sessions in workspace A and
  switch to workspace B which was on Worktrees, switching back to A returns
  to Sessions)
- Its own session list, worktree list, and configuration
- Its own sidebar scroll position and filter state

Switching workspaces feels instant — no loading spinner, no blank screen.

---

## 4. Pages & Screens

### 4.1 Home / Dashboard

**Purpose:** At-a-glance command center for the current workspace.
**Posture:** "Glance and go" — the user gets full situational awareness without
interacting. (UX-4.3)

```
+------------------------------------------------------------------+
|  Home                                          [+ New Session]    |
+------------------------------------------------------------------+
|                                                                   |
|  +--- Stats Row (bento grid) --------------------------------+   |
|  |                                                            |   |
|  | +--Active Runs--+ +--Worktrees--+ +--Today------+ +--SR--+|   |
|  | |               | |             | |             | |       ||   |
|  | |    3          | |    5        | |   12         | | 95%  ||   |
|  | | ● ● ●        | | 2 active    | | completed   | | pass ||   |
|  | | running now   | | 3 idle      | | ↑ 4 vs yday | | rate ||   |
|  | +---------------+ +-------------+ +-------------+ +------+|   |
|  +------------------------------------------------------------+   |
|                                                                   |
|  Active Runs                                      [View all →]    |
|  +--------------------------------------------------------------+ |
|  |  ● add-auth          Developing   iter 3/5   claude   12m   | |
|  |    ├─ [████████░░] progress                                  | |
|  |                                                               | |
|  |  ● fix-api-routes    Reviewing    pass 1/2   codex     5m   | |
|  |    ├─ [██████████] complete, awaiting commit                  | |
|  |                                                               | |
|  |  ● update-deps       Committing              claude    1m   | |
|  |    ├─ [██████████] finalizing                                 | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  Needs Attention                                                  |
|  +--------------------------------------------------------------+ |
|  |  ✕ login-flow        Failed       "API rate limit exceeded"  | |
|  |                       2h ago       [View Details] [Resume]    | |
|  |                                                               | |
|  |  ⏸ cache-layer       Paused       checkpoint saved           | |
|  |                       1d ago       [View Details] [Resume]    | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  Recent Completions                                               |
|  +--------------------------------------------------------------+ |
|  |  ✓ perf-optimize     3 iterations, 2 reviews       30m ago  | |
|  |  ✓ add-unit-tests    5 iterations, 1 review         2h ago  | |
|  |  ✓ fix-typos         1 iteration, 0 reviews         5h ago  | |
|  +--------------------------------------------------------------+ |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Stat cards** show not just a number but context: "3 running now" with dots
  for each, "↑ 4 vs yesterday" for trend, percentage with label. Each card is
  clickable — navigates to the relevant filtered view
- **Active runs** show the worktree's descriptive name (e.g., "add-auth") as
  the primary identifier — never a UUID. Current phase shown as a word
  ("Developing"), iteration progress as a fraction ("iter 3/5"), agent name,
  and time since start. A small progress bar underneath each run shows overall
  pipeline progress
- **Needs Attention** uses red (✕) for failed and yellow (⏸) for paused,
  with the *reason* shown inline ("API rate limit exceeded", "checkpoint saved").
  Recovery actions (Resume, View Details) are directly on the row — the user
  doesn't have to navigate somewhere else to act
- **Recent Completions** are lower visual priority (smaller, no action buttons).
  They confirm that work has been done

The page title "Home" is displayed in Display typography (24px, weight 600) at
the top left. The "New Session" button is amber, always visible in the top
right corner of the page.

### 4.2 Sessions List

**Purpose:** Full lifecycle management of all sessions in the workspace.
**Posture:** Management view — designed for scanning, filtering, and bulk
actions. (UX-4.3)

```
+------------------------------------------------------------------+
|  Sessions                          [Search...]  [+ New Session]   |
+------------------------------------------------------------------+
|  [All (23)] [Running (3)] [Paused (2)] [Completed (15)] [Failed (3)]
+------------------------------------------------------------------+
|                                                                   |
|  [ ] Name               Status      Phase    Agent   Age    ...  |
|  ────────────────────────────────────────────────────────────────  |
|  [ ] add-auth           ● Running   Dev 3/5  claude  12m    ⋮    |
|  [ ] fix-api-routes     ● Running   Rev 1/2  codex    5m    ⋮    |
|  [ ] update-deps        ● Running   Commit   claude   1m    ⋮    |
|  ────────────────────────────────────────────────────────────────  |
|  [ ] login-flow         ✕ Failed    Dev 2/5  claude   2h    ⋮    |
|  [ ] cache-layer        ⏸ Paused    Rev 1/2  codex    1d    ⋮    |
|  ────────────────────────────────────────────────────────────────  |
|  [ ] perf-optimize      ✓ Complete  —        claude  30m    ⋮    |
|  [ ] add-unit-tests     ✓ Complete  —        codex    2h    ⋮    |
|  [ ] fix-typos          ✓ Complete  —        claude   5h    ⋮    |
|                                                                   |
+------------------------------------------------------------------+
|  2 selected                        [Resume]  [Cancel]  [Delete]   |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Name column shows the worktree/feature name** as the primary identifier
  (e.g., "add-auth"), not a UUID or run ID. The run ID is available in the
  detail view but never used as a primary label in lists
- **Status filter tabs** show counts in parentheses so the user knows the
  distribution at a glance. The active tab is amber-underlined. Tabs act as
  quick filters — clicking "Failed (3)" shows only the 3 failed sessions
- **Column headers are sortable** — clicking toggles ascending/descending with
  a small arrow indicator
- **Checkbox column** enables multi-select for batch operations. When items are
  selected, a bottom action bar appears with contextual actions (Resume, Cancel,
  Delete). Only valid actions for the selection are enabled
- **Three-dot menu (⋮)** on each row provides row-level actions: View Details,
  Resume, Cancel, Copy Run ID, Delete
- **Search** filters across session names, worktree names, and agent names.
  The search field is always visible, not hidden behind a button
- **Visual grouping** uses subtle horizontal dividers between status groups
  when "All" tab is active, so running sessions are visually separated from
  completed ones
- Rows have hover state (raised surface color). Clicking a row navigates to
  the Run Detail page for that session

### 4.3 New Session Wizard

**Purpose:** Guide the user through launching a new AI development session.
**Posture:** Focused task view — one decision at a time, minimal distraction.
(UX-4.3, UX-6.1)

The wizard uses a stepped progress bar at the top and fills the main content
area. Three steps: **Prompt → Configure → Review & Launch**.

**Step 1: Prompt**

```
+------------------------------------------------------------------+
|  New Session                                                      |
|  [1. Prompt ●━━━━━━━2. Configure ━━━━━━━3. Review & Launch]      |
+------------------------------------------------------------------+
|                                                                   |
|  What should Ralph Workflow build?          [(sparkle) AI Assist] |
|                                                                   |
|  +--------------------------------------------------------------+|
|  |                                                               ||
|  |  # Feature: Add user authentication                          ||
|  |                                                               ||
|  |  Add authentication to the API so users can register, log     ||
|  |  in, and access protected endpoints with token-based auth.    ||
|  |                                                               ||
|  |  Requirements:                                                ||
|  |  - Registration with email and password                       ||
|  |  - Secure password storage                                    ||
|  |  - Token-based login with automatic refresh                   ||
|  |  - Protected endpoint middleware                              ||
|  |                                                               ||
|  |  Acceptance criteria:                                         ||
|  |  - New users can register and log in successfully             ||
|  |  - Protected routes reject unauthenticated requests (401)     ||
|  |  - Tokens refresh transparently before expiry                 ||
|  |  - All auth endpoints have test coverage                      ||
|  |                                                               ||
|  +--------------------------------------------------------------+|
|                                                                   |
|  +-- Quick Start ---------+  +-- Or Load Template ---+           |
|  |                         |  |                       |           |
|  |  [Browse Templates...]  |  |  Recent:              |           |
|  |                         |  |  · Bug fix template   |           |
|  |  [Save as Template]     |  |  · Feature template   |           |
|  |                         |  |  · Refactor template  |           |
|  +-------------------------+  +-----------------------+           |
|                                                                   |
|                                            [Cancel]  [Next →]     |
+------------------------------------------------------------------+
```

When the user clicks `(sparkle) AI Assist`, the editor shrinks to 60% and a
right panel (40%) opens with the AI Prompt Assistant:

```
+------------------------------------------------------------------+
|  New Session                                                      |
|  [1. Prompt ●━━━━━━━2. Configure ━━━━━━━3. Review & Launch]      |
+------------------------------------------------------------------+
|                                                                   |
|  What should Ralph Workflow build?       [(sparkle) AI Assist ●]  |
|                                                                   |
|  +--- Editor (60%) --------+--- AI Assistant (40%) --------------+|
|  |                          |                                     ||
|  |  # Feature: Add user    |  AI Prompt Assistant                 ||
|  |  authentication          |  Using: planner (claude-opus)       ||
|  |                          |                          [Change ▾] ||
|  |  Add authentication...   |                                     ||
|  |                          |  How can I help with your prompt?   ||
|  |  Requirements:           |                                     ||
|  |  - Registration...       |  [Describe what to build]           ||
|  |  - Secure password...    |    Tell me about the feature and    ||
|  |  - Token-based login...  |    I'll write a structured prompt.  ||
|  |                          |                                     ||
|  |  Acceptance criteria:    |  [Refine current prompt]            ||
|  |  - New users can...      |    I'll review your prompt and      ||
|  |                          |    suggest improvements.            ||
|  |                          |                                     ||
|  |                          |  ──────────────────────────         ||
|  |                          |                                     ||
|  |                          |  [Type a message...]          [↵]  ||
|  +--------------------------+-------------------------------------+|
|                                                                   |
|  Templates: [Browse...] [Save as Template]                        |
|                                            [Cancel]  [Next →]     |
+------------------------------------------------------------------+
```

**"Describe what to build" flow** — The user describes their feature in natural
language. The AI asks clarifying questions, then generates a structured prompt:

```
|  AI Assistant                                                     |
|                                                                   |
|  AI: Tell me about the feature you want to build.                |
|  What should it do? What's the expected behavior                 |
|  when it's working?                                              |
|                                                                   |
|  You: We need auth on our Rust/Axum API. Users                   |
|  register, log in, and use tokens to hit protected               |
|  endpoints. Tokens should refresh automatically.                 |
|                                                                   |
|  AI: A few clarifying questions:                                 |
|  1. What should happen on invalid credentials?                   |
|  2. Do you need role-based access control?                       |
|  3. Any password requirements (minimum length)?                  |
|                                                                   |
|  You: Return 401 on bad creds. No roles for now.                 |
|  Standard rules — 8+ chars.                                      |
|                                                                   |
|  AI: Here's a prompt based on your description:                  |
|                                                                   |
|  +-- Suggested Prompt ────────────────────────────+              |
|  |  # Add API Authentication                      |              |
|  |                                                |              |
|  |  Add token-based authentication to the API     |              |
|  |  so users can register, log in, and access     |              |
|  |  protected endpoints.                          |              |
|  |                                                |              |
|  |  Requirements:                                 |              |
|  |  - User registration (email + password)        |              |
|  |  - Login returning auth token                  |              |
|  |  - Automatic token refresh before expiry       |              |
|  |  - Protected endpoint middleware               |              |
|  |  - Passwords must be 8+ characters             |              |
|  |                                                |              |
|  |  Acceptance criteria:                          |              |
|  |  - New users can register and log in           |              |
|  |  - Invalid credentials return 401              |              |
|  |  - Expired tokens refresh transparently        |              |
|  |  - Unauthenticated requests return 401         |              |
|  |  - All auth endpoints have tests               |              |
|  |                                                |              |
|  |  [Apply to Editor]  [Edit & Apply]             |              |
|  +------------------------------------------------+              |
|                                                                   |
|  [Type a message...]                                        [↵]  |
```

**"Refine current prompt" flow** — The AI analyzes the existing prompt and
identifies issues:

```
|  AI Assistant                                                     |
|                                                                   |
|  AI: I've reviewed your prompt. Here's my analysis:              |
|                                                                   |
|  Quality:  ★★★☆☆  Needs Refinement                              |
|                                                                   |
|  ⚠ Implementation details detected                              |
|    Your prompt specifies "bcrypt" and "JWT" — these              |
|    are implementation choices the AI agent should                |
|    make. Describe the desired behavior instead.                  |
|                                                                   |
|  ⚠ Missing acceptance criteria                                  |
|    Add specific, testable outcomes. What should                  |
|    pass when this feature is done?                               |
|                                                                   |
|  ✓ Clear feature description                                    |
|  ✓ Structured with bullet points                                |
|                                                                   |
|  +-- Suggested Revision ──────────────────────────+              |
|  |  (revised prompt with issues addressed)        |              |
|  |                                                |              |
|  |  [Apply to Editor]  [Edit & Apply]             |              |
|  +------------------------------------------------+              |
|                                                                   |
|  You can continue the conversation to refine further.            |
|  [Type a message...]                                        [↵]  |
```

The prompt editor is a tall, focused textarea. It supports Markdown preview
toggle. Below the editor, the user can:
- Browse the template library to start from a template
- See their recent templates for quick reuse
- Save the current prompt as a new template

There is no configuration on this step — just the prompt. The AI assistant
panel is the only addition, and it's toggled off by default so the simple
path remains uncluttered. (UX-6.1: one decision per step, UX-5.1: progressive
disclosure)

**Prompt philosophy:** The AI Prompt Assistant is trained to guide users toward
outcome-focused prompts. Good prompts describe *what the feature should do when
it's working*, not *how to implement it*. Implementation details (which library,
which algorithm, which pattern) should be left to the AI development agents
unless the user has a specific constraint. The more detail about the desired
behavior and acceptance criteria, the better the development result.

**How it works under the hood:** Ralph controls the system prompt sent to the
AI agent. The user's input is wrapped in instructions that direct the AI to:
- Focus on desired outcomes, not implementation specifics
- Include acceptance criteria (testable outcomes)
- Ask clarifying questions when the description is vague
- Structure the response with clear sections (title, description, requirements,
  acceptance criteria)
- Return the prompt in a structured format that Ralph can parse and present

The agent used defaults to the Planning drain's first agent (since prompt
writing is closest to the planning role). The user can change this via a
`[Change ▾]` dropdown showing all configured agents. The conversation is
ephemeral — it exists only during the wizard session and is not saved. The
resulting prompt text in the editor is what matters.

**Step 2: Configure**

The wizard reads defaults from `~/.config/ralph-workflow.toml` (global) and
`.agent/ralph-workflow.toml` (project). If the user already has their pipeline
configured, Step 2 should be nearly zero-friction — pick a worktree, confirm
defaults, move on. The full configuration is only shown when the user asks
for it or when something is missing.

**Default view — configured user (happy path):**

```
+------------------------------------------------------------------+
|  New Session                                                      |
|  [1. Prompt ━━━━━━━●2. Configure ━━━━━━━3. Review & Launch]      |
+------------------------------------------------------------------+
|                                                                   |
|  Where & How                                                      |
|                                                                   |
|  Worktree        [add-auth                          ▾]            |
|                   Or: [+ Create New Worktree]                     |
|                                                                   |
|  Using your configured defaults:                                  |
|  5 iterations · 2 reviews · Standard depth                        |
|  6 drains → 5 chains · 4 agents configured                       |
|                                                 [Customize ▾]    |
|                                                                   |
|                                    [← Back]  [Cancel]  [Next →]   |
+------------------------------------------------------------------+
```

For an experienced user with everything configured, Step 2 is just: **pick a
worktree, confirm the one-line summary looks right, hit Next.** All values come
from the existing config. The user shouldn't have to re-specify iterations,
agents, or drains every single session — that's what the Configuration page is
for. (UX-4.4 Eliminate Excise: don't ask for information the system already
knows)

When `[Customize ▾]` is clicked, the full options expand:

```
+------------------------------------------------------------------+
|  New Session                                                      |
|  [1. Prompt ━━━━━━━●2. Configure ━━━━━━━3. Review & Launch]      |
+------------------------------------------------------------------+
|                                                                   |
|  Where & How                                                      |
|                                                                   |
|  Worktree        [add-auth                          ▾]            |
|                   Or: [+ Create New Worktree]                     |
|                                                                   |
|  ── Customize (overrides for this session only) ────  [Collapse ▴]|
|                                                                   |
|  Iterations      [5        ]  How many develop cycles             |
|  Reviews         [2        ]  Review passes per iteration         |
|                                                                   |
|  Preset:  [● Standard]  [ ] Thorough  [ ] Quick  [ ] Custom      |
|                                                                   |
|  ── Agent Chains ────────────────────────────────────             |
|                                                                   |
|  Using configured drain bindings:                                 |
|  Planning:    planner   → claude-opus → opencode-gpt4             |
|  Development: developer → glm-agent → claude-opus → codex-o3     |
|  Analysis:    analyzer  → opencode-gpt4                           |
|  Review:      reviewer  → opencode-gpt4 → claude-opus            |
|  Fix:         developer → glm-agent → claude-opus → codex-o3     |
|  Commit:      commit    → claude-opus                              |
|                                      [Customize for this session] |
|                                                                   |
|  ⓘ These values come from your workspace configuration.           |
|    Changes here apply to this session only.                        |
|                                      [Reset to configured ↺]     |
|                                                                   |
|                                    [← Back]  [Cancel]  [Next →]   |
+------------------------------------------------------------------+
```

When "Customize for this session" is clicked within the expanded Agent Chains
section, the drain bindings become editable dropdowns allowing the user to
override which chain is used for each drain (does not change the global config):

```
|  ── Agent Chains (customized for this session) ──────            |
|                                                                   |
|  Planning       [planner             ▾]                           |
|  Development    [developer           ▾]                           |
|  Review         [reviewer            ▾]                           |
|  Fix            [developer           ▾]                           |
|  Commit         [commit              ▾]                           |
|  Analysis       [analyzer            ▾]                           |
|                                                                   |
|  ⓘ These overrides apply to this session only.                   |
|    Edit chains in Configuration → Agent Chains & Drains.          |
|                                      [Reset to configured ↺]     |
```

**Unconfigured user — first time or missing config:**

If no agent chains are configured (empty `[agent_chains]` in the TOML), Step 2
shows a prominent setup callout instead of the compact summary:

```
+------------------------------------------------------------------+
|  New Session                                                      |
|  [1. Prompt ━━━━━━━●2. Configure ━━━━━━━3. Review & Launch]      |
+------------------------------------------------------------------+
|                                                                   |
|  Where & How                                                      |
|                                                                   |
|  Worktree        [add-auth                          ▾]            |
|                   Or: [+ Create New Worktree]                     |
|                                                                   |
|  +-- Setup Required ──────────────────────────────────+          |
|  |                                                     |          |
|  |  ⚠ No agent chains configured                      |          |
|  |                                                     |          |
|  |  Ralph Workflow needs at least one agent chain to   |          |
|  |  run sessions. Set up your agents and drain         |          |
|  |  bindings in Configuration first.                   |          |
|  |                                                     |          |
|  |  [Go to Configuration →]                            |          |
|  +-----------------------------------------------------+          |
|                                                                   |
|                                    [← Back]  [Cancel]             |
+------------------------------------------------------------------+
```

The "Next →" button is disabled until configuration is complete. This prevents
launching sessions that would immediately fail. (UX-3.5 Error Prevention)

**Key design decisions for Step 2:**

- **Smart defaults over manual entry:** All values are pre-filled from the
  workspace configuration. The user only changes what they need to change.
  For most sessions, the answer is "nothing" — just pick a worktree and go
- **Two-tier disclosure:** Compact summary by default (one line), full
  customization on demand. This means the common case (configured user
  starting a new session) requires exactly one selection (worktree) and two
  clicks (Next, then Launch on Step 3). Three steps, ~5 seconds
- **Configuration, not the wizard, is where you set up agents:** The wizard
  is for per-session overrides only. If the user wants to change their default
  iterations from 5 to 3, they do it in Configuration once — not in the wizard
  every time
- **Fail fast on missing config:** If agents aren't configured, the wizard
  catches it here with a clear path to fix it. The user never reaches Step 3
  and hits a confusing launch failure
- **No mandatory fields beyond worktree:** Everything else has a default. The
  user can literally click Next without touching anything (UX-4.4)

This is a significant reduction from dumping all options on one screen.
Advanced options (retry settings, context levels, etc.) use workspace-level
configuration — they don't belong in the per-session wizard. (UX-5.1, UX-6.1)

**Step 3: Review & Launch**

```
+------------------------------------------------------------------+
|  New Session                                                      |
|  [1. Prompt ━━━━━━━2. Configure ━━━━━━●3. Review & Launch]       |
+------------------------------------------------------------------+
|                                                                   |
|  Review your session before launching:                            |
|                                                                   |
|  +-- Prompt Summary -------------------------------------------+ |
|  |  "Add user authentication with JWT, login/register,         | |
|  |   bcrypt hashing, and token refresh"                         | |
|  |                                          [Edit Prompt]       | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +-- Configuration Summary ------------------------------------+ |
|  |  Worktree:      add-auth (branch: wt-62-auth)               | |
|  |  Planning:      planner (claude-opus + 1 more)              | |
|  |  Dev & Fix:     developer (glm-agent + 2 more)              | |
|  |  Analysis:      analyzer (opencode-gpt4)                    | |
|  |  Review:        reviewer (opencode-gpt4 + 1 more)           | |
|  |  Commit:        commit (claude-opus)                         | |
|  |  Iterations:    5                                            | |
|  |  Reviews:       2 per iteration                              | |
|  |  Preset:        Standard                                     | |
|  |                                          [Edit Config]       | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +-- AI Prompt Review -----------------------------------------+ |
|  |  Clarity:     ★★★★☆  Good                                   | |
|  |  Suggestions:                                                | |
|  |   · Consider specifying the auth middleware location         | |
|  |   · Add acceptance criteria for token expiry                 | |
|  |                                          [Review Again]      | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|                             [← Back]  [Cancel]  [▶ Launch]      |
+------------------------------------------------------------------+
```

The preflight summary shows everything the user chose across steps 1 and 2,
with "Edit" links that jump back to the relevant step (preserving all state).
The AI prompt review runs automatically and shows suggestions the user can
act on or ignore.

The launch button is the most visually prominent element on this page — amber
background, larger than other buttons. After clicking, the user is immediately
taken to the Run Detail page for the new session. (UX-4.2: continuous flow)

### 4.4 Run Detail

**Purpose:** Deep monitoring of a single session's execution.
**Posture:** Monitoring view — designed for extended passive watching with
occasional interaction. (UX-4.3)

```
+------------------------------------------------------------------+
|  ← Sessions         add-auth                                     |
|                      "Add user authentication"         [Actions ▾]|
+------------------------------------------------------------------+
|                                                                   |
|  +-- Phase Timeline -------------------------------------------+ |
|  |                                                              | |
|  |  [Plan]━━━━━━[Develop]━━━━━━[Review]━━━━━━[Commit]          | |
|  |    ✓           ◉ 3/5          ○              ○               | |
|  |   1m 20s      Running       Waiting        Waiting           | |
|  |                                                              | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +-- Status Banner (only when degraded) -----------------------+ |
|  |  ⚠ Degraded: Switched to fallback agent after 2 retries.    | |
|  |    Original: claude → Current: codex    [What does this mean?]| |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +--------- Left Panel (60%) ---------+--- Right Panel (40%) ---+|
|  |                                     |                         ||
|  |  Run Log                            |  Session Info           ||
|  |  [● Live] [Search...] [↓ Download]  |                         ||
|  |                                     |  Name:     add-auth     ||
|  |  14:23:01  Starting iteration 3     |  Status:   ● Running   ||
|  |  14:23:02  Agent: claude-opus-4     |  Agent:    claude       ||
|  |  14:23:05  Reading PLAN.md          |  Worktree: wt-62-auth  ||
|  |  14:23:10  Modifying src/auth/...   |  Branch:   wt-62-auth  ||
|  |  14:23:15  Running test suite...    |  Started:  2:11 PM     ||
|  |  14:23:20  8 tests passing          |  Duration: 12m 34s     ||
|  |  14:23:25  Writing changes...       |  Iter:     3 of 5      ||
|  |  14:23:30  Iteration 3 complete     |  Reviews:  0 of 2      ||
|  |            ▒ (streaming cursor)     |  Degraded: No          ||
|  |                                     |  Checkpoint: Yes       ||
|  |  [↓ Scroll to bottom]              |                         ||
|  +-------------------------------------+-------------------------+|
|                                                                   |
|  Iteration History                                   [Collapse ▴] |
|  +--------------------------------------------------------------+ |
|  |  Iter  Duration  Files Changed  Tests       Result            | |
|  |  ────  ────────  ────────────   ─────       ──────            | |
|  |  1     4m 12s    3 files        8/8 pass    ✓ Complete        | |
|  |  2     3m 45s    2 files        10/10 pass  ✓ Complete        | |
|  |  3     Running...               —           ◉ In Progress     | |
|  +--------------------------------------------------------------+ |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Page title is the session name** ("add-auth"), not "Run Detail" or a UUID.
  Below the name, the prompt text appears as a subtitle in secondary color
  so the user always knows *what* this session is doing (UX-1.1, UX-8.2)
- **"← Sessions" back link** preserves list scroll position and filters
- **Phase Timeline** is the visual anchor of the page. Four phases displayed
  as a horizontal pipeline. Completed phases show a checkmark (✓) with
  elapsed time. The active phase shows a pulsing dot (◉) with progress
  ("3/5"). Future phases show an empty circle (○) with "Waiting" label.
  Connecting lines between phases fill in as the pipeline progresses
- **Degraded banner** only appears when the run has switched to fallback
  agents or is retrying. It's yellow-tinted and explains what happened in
  plain language, with a "What does this mean?" link to contextual help.
  When not degraded, this entire section is absent — no empty space (UX-8.1)
- **Tabbed content area** below the phase timeline has three tabs: **Log**
  (default, shown in wireframe above), **Changes** (syntax-highlighted diff
  viewer — see Section 4.12), and **Info** (session metadata). The Log tab is
  selected by default for running sessions. For completed sessions, the
  Changes tab is selected by default since that's what the user most wants to
  see after a run finishes
- **Log tab split panel:** The log viewer takes 60% of the width (this is a
  developer tool — logs matter). The info panel takes 40%
- **Log viewer** has a "Live" indicator (green dot + "Live" text) confirming
  the stream is active. A streaming cursor (▒) at the bottom shows new
  content is arriving. When the user scrolls up, auto-scroll pauses and a
  "Scroll to bottom" button appears. Search, download, and filter controls
  are in the log header
- **Session Info panel** shows all metadata with clear labels and values. Uses
  the session name and worktree name (never UUIDs as primary display).
  Duration updates live
- **Iteration History** is a collapsible table at the bottom, showing per-
  iteration metrics. The "Files Changed" count is a link — clicking it
  switches to the Changes tab filtered to that iteration's diff, so the
  user can see exactly what each cycle produced. This is secondary
  information — important but not the primary focus of the page (UX-5.1)
- **Actions dropdown** (top right) contains: Resume, Pause, Cancel, Copy Run
  ID, Open in Terminal. The primary action (e.g., Resume for paused runs) is
  also available as a prominent button
- **Tab bar** below the phase timeline: **[Log]  [Changes]  [Info]**. The
  active tab has an amber underline. Default tab depends on run state: Log
  for running sessions, Changes for completed sessions, Log for failed/paused
  sessions. This ensures the user sees the most relevant content first

#### 4.4.1 Run Detail — Completed State (UX-6.6)

When a run completes successfully, the Run Detail page celebrates the
accomplishment and surfaces the results — not just "status: done".

```
+------------------------------------------------------------------+
|  ← Sessions         add-auth                                     |
|                      "Add user authentication"         [Actions ▾]|
+------------------------------------------------------------------+
|                                                                   |
|  +-- Phase Timeline -------------------------------------------+ |
|  |                                                              | |
|  |  [Plan]━━━━━━[Develop]━━━━━━[Review]━━━━━━[Commit]          | |
|  |    ✓           ✓              ✓              ✓               | |
|  |   1m 20s      18m 45s        4m 30s         0m 55s           | |
|  |                                                              | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +-- Completion Summary ----------------------------------------+ |
|  |                                                               | |
|  |  ✓  Session completed successfully                 25m 30s    | |
|  |                                                               | |
|  |  +--Iterations--+ +--Reviews----+ +--Files------+ +--Tests-+ | |
|  |  |      3       | |     2       | |     7       | |   18   | | |
|  |  | dev cycles   | | passes      | | changed     | | passed | | |
|  |  | all passed   | | 0 findings  | | +142 -38    | | 0 fail | | |
|  |  +----------- --+ +-------------+ +-------------+ +--------+ | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  [Log]  [Changes ●]  [Info]                                      |
|                                                                   |
|  Session complete · 3 iterations · 7 files changed  +142  -38     |
|  [View by Iteration ▾]  [Copy as Patch]  [Open in Editor]        |
|                                                                   |
|  (Changes tab shown by default — see Section 4.12)               |
|                                                                   |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Completion Summary card** replaces the degraded banner area. Uses a green-
  tinted left border and the ✓ icon at large size. The total duration is
  prominent. This is the "peak-end" moment (UX-6.6) — the user's last
  impression of a run should feel satisfying, not anticlimactic
- **Metric cards** show the four most important outcomes: iterations completed,
  review passes, files changed (with +/- counts), and test results. These give
  the user a complete picture at a glance without reading logs
- **Phase timeline** shows all four phases completed with checkmarks and
  individual durations. The connecting lines are fully filled in green. This
  visual completion reinforces "everything succeeded"
- **Changes tab is selected by default** because after a run completes, the
  user's primary goal is "what did it produce?" — the code diff answers this
  directly (UX-4.1)
- **No recovery actions needed** — the Actions dropdown still offers "Copy Run
  ID", "Open in Terminal", "Start New Session" (pre-filled with same worktree)

#### 4.4.2 Run Detail — Failed State (UX-3.9, UX-11.2)

When a run fails, the page must clearly communicate what went wrong, why,
and what the user can do about it — never a dead end.

```
+------------------------------------------------------------------+
|  ← Sessions         login-flow                                   |
|                      "Add login flow"          [Resume]  [Actions ▾]|
+------------------------------------------------------------------+
|                                                                   |
|  +-- Phase Timeline -------------------------------------------+ |
|  |                                                              | |
|  |  [Plan]━━━━━━[Develop]━━━━━━[Review]      [Commit]          | |
|  |    ✓           ✕ 2/5          ○              ○               | |
|  |   1m 20s      Failed        Skipped        Skipped           | |
|  |                                                              | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +-- Error Summary (red-tinted) --------------------------------+ |
|  |                                                               | |
|  |  ✕  Session failed during Development (iteration 2 of 5)     | |
|  |                                                               | |
|  |  Error:   API rate limit exceeded                             | |
|  |  Agent:   claude (claude-opus-4-6)                            | |
|  |  When:    2 hours ago (failed at iteration 2 of 5)            | |
|  |  Duration: 8m 15s before failure                              | |
|  |                                                               | |
|  |  What you can do:                                             | |
|  |  · Wait for the rate limit to reset and resume                | |
|  |  · Switch to a different agent in Configuration                | |
|  |  · Check your API provider dashboard for usage limits          | |
|  |                                                               | |
|  |  [Resume Session]  [Retry from Beginning]  [Go to Config]     | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  [Log ●]  [Changes]  [Info]                                      |
|                                                                   |
|  (Log tab shown by default — last output helps diagnose the issue)|
|                                                                   |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Error Summary card** replaces the degraded/completion banner area. Uses a
  red-tinted left border and red ✕ icon. States the error in plain language,
  identifies which phase and iteration failed, and shows relevant context
  (agent, timing)
- **"What you can do" section** provides actionable recovery guidance specific
  to the error type. This follows UX-11.2: every error states what happened,
  why, and what the user can do about it
- **Three recovery actions** are shown inline on the card:
  - "Resume Session" — continues from the checkpoint (most common action)
  - "Retry from Beginning" — starts over with a clean slate
  - "Go to Config" — for errors that require configuration changes (agent
    swap, API key issues)
- **Resume button also appears** in the page header (next to the session name)
  for quick access. This is the primary action for failed runs (UX-4.1:
  "Resume action visible on the session itself")
- **Phase timeline** marks the failed phase with ✕ and shows subsequent phases
  as "Skipped" with empty circles. The connecting lines stop at the failure
  point, visually showing "progress stopped here"
- **Log tab is selected by default** for failed runs because the last log
  output is usually the most useful diagnostic information

#### 4.4.3 Run Detail — Paused State (UX-13.1)

When a run is paused (checkpoint saved), the page communicates that work is
preserved and resumption is one click away.

```
+------------------------------------------------------------------+
|  ← Sessions         cache-layer                                  |
|                      "Add caching layer"       [Resume]  [Actions ▾]|
+------------------------------------------------------------------+
|                                                                   |
|  +-- Phase Timeline -------------------------------------------+ |
|  |                                                              | |
|  |  [Plan]━━━━━━[Develop]━━━━━━[Review]      [Commit]          | |
|  |    ✓           ⏸ 3/5          ○              ○               | |
|  |   1m 20s      Paused        Waiting        Waiting           | |
|  |                                                              | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +-- Paused Banner (amber-tinted) ------------------------------+ |
|  |                                                               | |
|  |  ⏸  Session paused — checkpoint saved                        | |
|  |                                                               | |
|  |  Paused during Development (iteration 3 of 5)                | |
|  |  Checkpoint includes all progress from iterations 1-2.        | |
|  |  Resuming will continue from iteration 3.                     | |
|  |                                                               | |
|  |  Last active: 1 day ago · Total time: 14m 30s                | |
|  |                                                               | |
|  |  [▶ Resume Session]                          [Cancel Session] | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  [Log ●]  [Changes]  [Info]                                      |
|                                                                   |
|  (Log shows output up to the pause point)                        |
|                                                                   |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Paused Banner** uses an amber-tinted left border and ⏸ icon, consistent
  with the warning/paused color language. The tone is reassuring — "checkpoint
  saved", "progress includes all iterations 1-2"
- **Resume is the hero action** — large amber button, prominently placed, and
  also in the page header. The user should feel that resuming is effortless
  (UX-4.4: eliminate excise)
- **Cancel is available but visually secondary** — outlined style, positioned
  away from Resume to prevent accidental clicks. Clicking Cancel shows the
  confirmation dialog (Section 5.4)
- **Phase timeline** shows ⏸ on the paused phase (not ✕ — this is not a
  failure). Future phases show "Waiting" to indicate they haven't been skipped,
  just not reached yet
- **Context about what's preserved** is explicit: "Checkpoint includes all
  progress from iterations 1-2. Resuming will continue from iteration 3."
  This gives the user confidence that nothing is lost (UX-13.1)

### 4.5 Worktrees

**Purpose:** Create and manage git worktrees for parallel feature work.
**Posture:** Management view — organized for scanning and quick actions.

```
+------------------------------------------------------------------+
|  Worktrees                                   [+ New Worktree]     |
+------------------------------------------------------------------+
|                                                                   |
|  Main Repository                                                  |
|  +--------------------------------------------------------------+ |
|  |  main                                                         | |
|  |  Primary repository · branch: main                           | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  Active — Running sessions (2)                                    |
|  +--------------------------------------------------------------+ |
|  |  add-auth                          ● Running (Develop 3/5)   | |
|  |  branch: wt-62-auth  · claude      started 12m ago           | |
|  |                            [Open Session]  [Open Folder]  ⋮  | |
|  +--------------------------------------------------------------+ |
|  |  fix-api-routes                    ● Running (Review 1/2)    | |
|  |  branch: wt-45-api   · codex       started 5m ago            | |
|  |                            [Open Session]  [Open Folder]  ⋮  | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  Idle — No active sessions (3)                                    |
|  +--------------------------------------------------------------+ |
|  |  cache-layer                       ⏸ Paused (checkpoint)     | |
|  |  branch: wt-40-cache                                         | |
|  |                     [Resume Session]  [New Session]  [...]  ⋮ | |
|  +--------------------------------------------------------------+ |
|  |  refactor-db                       No session                 | |
|  |  branch: wt-50-refac                                         | |
|  |                            [Start Session]  [Open Folder]  ⋮ | |
|  +--------------------------------------------------------------+ |
|  |  update-deps                       ✓ Last run: Completed     | |
|  |  branch: wt-51-deps                2h ago                    | |
|  |                            [Start Session]  [Open Folder]  ⋮ | |
|  +--------------------------------------------------------------+ |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Worktrees are grouped by activity state:** "Active" (has a running session)
  and "Idle" (no session or completed). This lets the user quickly find what's
  active vs. what's available. Group headers include counts
- **Each worktree card shows:** The descriptive worktree name as the primary
  label, branch name as secondary info, session status inline with phase
  progress if running, agent name, and time context
- **Actions are contextual:** Active worktrees show "Open Session" (goes to
  run detail). Idle worktrees with checkpoints show "Resume Session". Worktrees
  with no session show "Start Session" (opens the wizard pre-filled with this
  worktree). All show "Open Folder" (opens in system file browser) and a
  three-dot menu with more options (Delete Worktree, Copy Path, etc.)
- **Status is shown inline** on each worktree, so the user doesn't need to
  cross-reference the Sessions page. Links are bidirectional — worktree views
  link to sessions, and session views link back to worktrees (UX-8.4)

**New Worktree Dialog:**

```
+--------------------------------------------------+
|  Create Worktree                                  |
+--------------------------------------------------+
|                                                   |
|  What are you working on?                         |
|                                                   |
|  Ticket/Issue Number    [62          ]            |
|  Short Description      [gui-redesign]            |
|                                                   |
|  Preview:                                         |
|  ┌─────────────────────────────────────────────┐  |
|  │  Name:    wt-62-gui-redesign                │  |
|  │  Branch:  wt-62-gui-redesign                │  |
|  │  Path:    ../wt-62-gui-redesign             │  |
|  └─────────────────────────────────────────────┘  |
|                                                   |
|  [ ] Start a session immediately after creating   |
|                                                   |
|               [Cancel]     [Create Worktree]      |
+--------------------------------------------------+
```

The dialog asks for a ticket number and short description (developer-friendly
language, not git internals). A live preview shows the generated name, branch,
and path so the user can verify before creating. The optional checkbox to start
a session immediately reduces the common two-step flow to one. (UX-4.4:
eliminate excise)

### 4.6 Configuration

**Purpose:** Visual, form-based editing of Ralph Workflow pipeline settings.
**Posture:** Focused task view — methodical review and editing.

The configuration page replaces raw TOML editing with structured forms. It uses
a scope selector and collapsible sections.

```
+------------------------------------------------------------------+
|  Configuration                                                    |
+------------------------------------------------------------------+
|  Scope: [Effective ✓] [Global] [Project]                         |
|                                                                   |
|  The "Effective" view shows what will actually run, with the      |
|  source of each value indicated. Switch to Global or Project      |
|  to edit values at that scope.                                    |
+------------------------------------------------------------------+
|                                                                   |
|  ▼ General                                                        |
|  +--------------------------------------------------------------+ |
|  |                                                               | |
|  |  Developer Iterations    [5        ]  range: 1–20             | |
|  |    How many development cycles before stopping                | |
|  |                                      ○ default  ● global     | |
|  |                                                               | |
|  |  Reviewer Reviews        [2        ]  range: 0–10             | |
|  |    Review passes per iteration. 0 = skip review               | |
|  |                                      ● default               | |
|  |                                                               | |
|  |  Max Dev Continuations   [2        ]  range: 1–10             | |
|  |    Continuation attempts when agent runs out of tokens        | |
|  |                                      ● default               | |
|  |                                                               | |
|  |  Review Depth            [Standard           ▾]               | |
|  |    How thoroughly the reviewer examines changes               | |
|  |    Options: Standard · Comprehensive · Security · Incremental | |
|  |                                      ○ default  ● project    | |
|  |                                                               | |
|  |  Verbosity               [━━━━━━●━━━] 2                      | |
|  |    0 = quiet, 1 = normal, 2 = verbose, 3 = debug             | |
|  |                                      ● global                | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▶ Execution                                        4 settings    |
|  ▶ Retry & Fallback                                 6 settings    |
|  ▶ Git Identity                                     2 settings    |
|                                                                   |
|  ▼ Agent Chains & Drains                                          |
|  +--------------------------------------------------------------+ |
|  |                                                               | |
|  |  Agent chains are named, ordered lists of agents. The first   | |
|  |  agent is preferred; the rest serve as fallbacks if it fails  | |
|  |  (rate limit, auth error, context exhaustion).                | |
|  |                                                               | |
|  |  ── Chains (drag to reorder) ────────────────────────         | |
|  |                                                               | |
|  |  planner (2 agents):                                          | |
|  |  ┌────────────────┐  ┌────────────────┐                      | |
|  |  │ claude-opus    │→ │ opencode-gpt4  │                      | |
|  |  │ claude-code    │  │ opencode       │                      | |
|  |  │ opus-4-6       │  │ gpt-4-turbo    │                      | |
|  |  └────────────────┘  └────────────────┘                      | |
|  |  [+ Add Agent to Chain]                        [Rename] [✕]  | |
|  |                                                               | |
|  |  developer (3 agents):                                        | |
|  |  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  | |
|  |  │ glm-agent      │→ │ claude-opus    │→ │ codex-o3       │  | |
|  |  │ opencode       │  │ claude-code    │  │ codex          │  | |
|  |  │ glm-4-plus     │  │ opus-4-6       │  │ o3             │  | |
|  |  └────────────────┘  └────────────────┘  └────────────────┘  | |
|  |  [+ Add Agent to Chain]                        [Rename] [✕]  | |
|  |                                                               | |
|  |  reviewer (2 agents):                                         | |
|  |  ┌────────────────┐  ┌────────────────┐                      | |
|  |  │ opencode-gpt4  │→ │ claude-opus    │                      | |
|  |  │ opencode       │  │ claude-code    │                      | |
|  |  │ gpt-4-turbo    │  │ opus-4-6       │                      | |
|  |  └────────────────┘  └────────────────┘                      | |
|  |  [+ Add Agent to Chain]                        [Rename] [✕]  | |
|  |                                                               | |
|  |  analyzer (1 agent):                                          | |
|  |  ┌────────────────┐                                          | |
|  |  │ opencode-gpt4  │                                          | |
|  |  │ opencode       │                                          | |
|  |  │ gpt-4-turbo    │                                          | |
|  |  └────────────────┘                                          | |
|  |  [+ Add Agent to Chain]                        [Rename] [✕]  | |
|  |                                                               | |
|  |  commit (1 agent):                                            | |
|  |  ┌────────────────┐                                          | |
|  |  │ claude-opus    │                                          | |
|  |  │ claude-code    │                                          | |
|  |  │ opus-4-6       │                                          | |
|  |  └────────────────┘                                          | |
|  |  [+ Add Agent to Chain]                        [Rename] [✕]  | |
|  |                                                               | |
|  |  [+ Create New Chain]                                         | |
|  |                                                               | |
|  |  ── Drains (which phase uses which chain) ───────────         | |
|  |                                                               | |
|  |  Drains assign each pipeline phase to the best chain for the  | |
|  |  job. Different models excel at different tasks — use drains   | |
|  |  to match agent strengths to phase requirements.              | |
|  |                                                               | |
|  |  Example: Claude Opus excels at planning, GLM at executing    | |
|  |  detailed instructions, GPT at verification. Drains let you   | |
|  |  assign each to the phase where it shines.                    | |
|  |                                                               | |
|  |  Planning        [planner            ▾]  Creates the plan     | |
|  |  Development     [developer           ▾]  Writes the code     | |
|  |  Review          [reviewer            ▾]  Reviews changes     | |
|  |  Fix             [developer           ▾]  Fixes review issues | |
|  |  Commit          [commit              ▾]  Writes commit msgs  | |
|  |  Analysis  [?]   [analyzer            ▾]  Checks code vs plan | |
|  |                                                               | |
|  |  [?] Analysis runs after each dev iteration to verify the     | |
|  |  code satisfies the plan. GPT models are often a good fit     | |
|  |  for this role — consider a dedicated chain.                  | |
|  |                                                               | |
|  |  ── Configured Agents ───────────────────────────────         | |
|  |                                                               | |
|  |  ┌───────────────────────┐  ┌───────────────────────┐        | |
|  |  │  claude-opus          │  │  opencode-gpt4        │        | |
|  |  │  Tool: Claude Code    │  │  Tool: OpenCode       │        | |
|  |  │  Provider: Anthropic  │  │  Provider: OpenAI     │        | |
|  |  │  Model: opus-4-6      │  │  Model: gpt-4-turbo   │        | |
|  |  │  [Edit]    [Remove]   │  │  [Edit]    [Remove]   │        | |
|  |  └───────────────────────┘  └───────────────────────┘        | |
|  |                                                               | |
|  |  ┌───────────────────────┐  ┌───────────────────────┐        | |
|  |  │  glm-agent            │  │  codex-o3             │        | |
|  |  │  Tool: OpenCode       │  │  Tool: Codex          │        | |
|  |  │  Provider: ZhipuAI    │  │  Provider: OpenAI     │        | |
|  |  │  Model: glm-4-plus    │  │  Model: o3            │        | |
|  |  │  [Edit]    [Remove]   │  │  [Edit]    [Remove]   │        | |
|  |  └───────────────────────┘  └───────────────────────┘        | |
|  |                                                               | |
|  |  [+ Add Agent]                                                | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▼ Agent Tools                                                    |
|  +--------------------------------------------------------------+ |
|  |  Ralph Workflow delegates to these CLI tools for AI work.     | |
|  |  Manage installations in Preferences → Agent Tools.           | |
|  |                                                               | |
|  |  Claude Code         ✓ v1.2.3   Ready   [Open Settings →]    | |
|  |  Claude Code Switch  ✓ v0.5.2   Ready   [Open Settings →]    | |
|  |  Codex               ✓ v0.9.1   Ready   [Open Settings →]    | |
|  |  OpenCode             ✕ Not installed    [Install →]          | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|                          [Revert Changes]  [Save Configuration]   |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Three scope tabs:** "Effective" shows the final merged configuration as
  read-only with source indicators (default / global / project). "Global" and
  "Project" tabs allow editing values at that scope. This makes the precedence
  system transparent (UX-13.3)
- **Source indicators** on each field (small labels like "● default",
  "○ global", "● project") show where the current effective value comes from.
  Values that differ from defaults are visually highlighted with an amber left
  border
- **Every field has:** A label, the input control, a valid range or options
  list, and a help description explaining what the setting does in plain
  language. No developer needs to look up what "max_dev_continuations" means
  (UX-3.10)
- **Sections are collapsible** with a disclosure triangle. Collapsed sections
  show how many settings they contain. This lets the user see the full table
  of contents and expand only what they need (UX-5.1)
- **Agent Chains & Drains** section has three subsections:
  - **Chains:** Named, ordered lists of agents displayed as visual pipelines
    with drag-to-reorder. Each chain has a name (e.g., "developer", "reviewer",
    "commit") and contains agent cards showing the user-defined name, CLI tool,
    provider, and model. Chains can be created, renamed, or deleted. Agents
    within a chain can be added, removed, or reordered by dragging. When an
    agent fails (rate limit, auth error, etc.), Ralph automatically falls back
    to the next agent in the chain
  - **Drains:** Six dropdown selectors that bind pipeline phases to named
    chains. The six drains are Planning, Development, Review, Fix, Commit, and
    Analysis. The primary purpose of drains is matching agent strengths to
    phase requirements — for example, Claude Opus excels at planning, GLM at
    executing detailed instructions, and GPT at verification. Each drain also
    independently tracks its fallback position within its chain.
    Multiple drains can share the same chain if desired. The dropdown options
    are populated from the configured chain names
  - **Configured Agents:** All agents defined in the system, shown as cards.
    Clicking "+ Add Agent" opens the Add Agent dialog (see below). Agents can
    be edited, removed, or reordered within chains by dragging
- **Add/Edit Agent dialog — adaptive widget design:** The dialog uses
  progressive narrowing: CLI Tool → Provider → Model. Each selection
  constrains the next field. Widget types adapt to the data volume of each
  tool, following UX-6.1 (Hick's Law: ≤7 visible items; longer lists need
  search) and UX-4.4 (Eliminate Excise: don't ask for info the system knows).

  **CLI Tool** uses a **radio group** (not dropdown) showing only installed
  tools. Radio groups surface all options at once — faster than opening a
  dropdown for 2-4 items (UX-3.6 Recognition over Recall). Each option shows
  the tool name, version, and connection status. Uninstalled tools are not
  shown (UX-3.5 Error Prevention). If only one tool is installed, it is
  auto-selected and shown as a read-only label.

  **Provider** is adaptive:
  - Claude Code / Codex → single provider → **auto-filled read-only label**
    ("Anthropic" / "OpenAI"). No selector shown — eliminates a meaningless
    decision (UX-4.4).
  - OpenCode → multiple providers → **dropdown** with auth status indicators
    (✓ configured / ⚠ needs key). Typically 5-10 providers.

  **Model** is adaptive based on list size:
  - Small list (≤15 models, e.g., Claude Code, Codex) → **grouped dropdown**
    organized by model family (e.g., "Claude 4.x", "Claude 3.5.x"). Each
    option shows model name and context window size.
  - Large list (>15 models, e.g., OpenCode) → **searchable combo box** with
    type-ahead filtering. User types to narrow results. Options are grouped
    by model family. Each option shows model name, context window, and cost
    tier indicator ($ / $$ / $$$). A loading skeleton appears while models
    are fetched from the provider API.

  **Why adaptive, not one-size-fits-all:** A flat dropdown with 200 models is
  unusable (Hick's Law). But forcing a search box on someone choosing between
  3 Codex models adds friction. The widget matches the data.

  ```
  Add Agent dialog — when CLI tool has few models (Claude Code / Codex):

  +----------------------------------------------------+
  |  Add Agent                                    [✕]  |
  +----------------------------------------------------+
  |                                                    |
  |  Agent Name         [my-reviewer       ]           |
  |    A short name to identify this agent             |
  |                                                    |
  |  CLI Tool                                          |
  |    (●) Claude Code   v1.2.3  ✓                     |
  |    ( ) Codex         v0.9.1  ✓                     |
  |    ( ) OpenCode      v2.1.0  ✓                     |
  |                                                    |
  |  Provider            Anthropic                     |
  |                      (determined by tool)           |
  |                                                    |
  |  Model              [claude-opus-4-6        ▾]    |
  |                      ── Claude 4.x ──              |
  |                        opus-4-6   (200K ctx)       |
  |                        sonnet-4-6 (200K ctx)       |
  |                      ── Claude 3.5.x ──            |
  |                        haiku-3-5  (200K ctx)       |
  |                                                    |
  |               [Cancel]     [Add Agent]             |
  +----------------------------------------------------+


  Add Agent dialog — when CLI tool has many models (OpenCode):

  +----------------------------------------------------+
  |  Add Agent                                    [✕]  |
  +----------------------------------------------------+
  |                                                    |
  |  Agent Name         [my-analyzer      ]            |
  |    A short name to identify this agent             |
  |                                                    |
  |  CLI Tool                                          |
  |    ( ) Claude Code   v1.2.3  ✓                     |
  |    ( ) Codex         v0.9.1  ✓                     |
  |    (●) OpenCode      v2.1.0  ✓                     |
  |                                                    |
  |  Provider           [OpenAI           ✓ ▾]        |
  |                      Anthropic        ✓            |
  |                      Google           ✓            |
  |                      Mistral          ⚠            |
  |                                                    |
  |  Model              [gpt-4-turbo         ✕]       |
  |                     ┌────────────────────────┐     |
  |                     │ 🔍 gpt-4              │     |
  |                     │ ── Recommended ──────  │     |
  |                     │   gpt-4-turbo  128K $$ │     |
  |                     │   gpt-4o       128K $$ │     |
  |                     │ ── GPT-4.x ──────────  │     |
  |                     │   gpt-4        8K  $$  │     |
  |                     │   gpt-4-32k    32K $$$ │     |
  |                     │ ── GPT-3.5 ──────────  │     |
  |                     │   gpt-3.5-turbo 16K $  │     |
  |                     └────────────────────────┘     |
  |                                                    |
  |               [Cancel]     [Add Agent]             |
  +----------------------------------------------------+
  ```

- **"Add Agent to Chain" interaction:** Clicking `[+ Add Agent to Chain]`
  within a chain opens a **popover picker** listing all configured agents
  not already in that chain. Each option shows the agent name, tool, and
  model as secondary text. A "Create new agent..." option at the bottom
  opens the Add Agent dialog inline, and the newly created agent is
  automatically added to the chain. This eliminates the two-step workflow
  of defining an agent first and then navigating back to add it (UX-4.4
  Eliminate Excise).

  ```
  Add Agent to Chain popover:

  ┌─────────────────────────────────┐
  │  Add to "planner" chain         │
  │                                 │
  │  ○ claude-opus                  │
  │    Claude Code · opus-4-6       │
  │                                 │
  │  ○ opencode-gpt4               │
  │    OpenCode · gpt-4-turbo       │
  │                                 │
  │  ○ codex-o3                    │
  │    Codex · o3                   │
  │                                 │
  │  ── or ──────────────────────── │
  │  [+ Create new agent...]        │
  └─────────────────────────────────┘
  ```

- **Agent Tools** section shows the status of the CLI tools (Claude Code,
  Claude Code Switch, Codex, OpenCode) that Ralph Workflow delegates to.
  Each tool shows installed/not installed status and version. "Open Settings"
  opens that CLI's own configuration (where the user manages API keys,
  models, etc. through the tool's native interface). "Install" links to
  the Agent Tools Manager (Section 4.13). Ralph Workflow never manages API
  keys directly — each CLI tool handles its own authentication
- **Form validation** happens on blur (not keystroke). Invalid values show a
  red border with an error message below the field. The Save button is
  disabled when there are validation errors
- **Dirty tracking:** If the user has unsaved changes and navigates away, a
  dialog asks "Save changes?" with Save / Discard / Cancel options
- **Save confirmation** shows a brief success toast: "Configuration saved" —
  non-blocking, auto-dismisses in 3 seconds

### 4.7 GUI Preferences

**Purpose:** Settings specific to the desktop application, separate from the
pipeline configuration.

```
+------------------------------------------------------------------+
|  Preferences                                                      |
+------------------------------------------------------------------+
|                                                                   |
|  ▼ Appearance                                                     |
|  +--------------------------------------------------------------+ |
|  |  Theme              [Dark ▾]                                  | |
|  |  Accent Color        ■ #f59e0b  [Change...]                  | |
|  |  Sidebar Width       [220      ] px                           | |
|  |  UI Font Size         [14      ] px                           | |
|  |  Monospace Font       [JetBrains Mono ▾]                      | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▼ Behavior                                                       |
|  +--------------------------------------------------------------+ |
|  |  Polling Interval     [5       ] seconds                      | |
|  |    How often to check for run status updates                  | |
|  |                                                               | |
|  |  Log Auto-Scroll      [■] On                                 | |
|  |  Log Buffer            [5000   ] lines                        | |
|  |  Confirm Before Cancel [■] On                                 | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▼ Notifications                                                  |
|  +--------------------------------------------------------------+ |
|  |  Desktop Notifications  [■] On                                | |
|  |                                                               | |
|  |  Notify when:                                                 | |
|  |    [■] Session completes                                      | |
|  |    [■] Session fails                                          | |
|  |    [ ] Phase changes                                          | |
|  |    [ ] Degraded condition detected                            | |
|  |                                                               | |
|  |  Sound                  [Default ▾]                           | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▼ Startup                                                        |
|  +--------------------------------------------------------------+ |
|  |  Restore Last Workspaces  [■] On                              | |
|  |  Default View              [Home ▾]                           | |
|  |  Check for Updates         [■] On                             | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▼ Keyboard Shortcuts                                             |
|  +--------------------------------------------------------------+ |
|  |  Shortcut           Action              Rebind                | |
|  |  ─────────────────  ──────────────────  ───────               | |
|  |  g then h           Home                [Edit]                | |
|  |  g then s           Sessions            [Edit]                | |
|  |  g then w           Worktrees           [Edit]                | |
|  |  g then c           Configuration       [Edit]                | |
|  |  g then p           Preferences         [Edit]                | |
|  |  ?                  Help & Shortcuts     [Edit]                | |
|  |  Ctrl+N             New Session          [Edit]                | |
|  |  Ctrl+,             Preferences          [Edit]                | |
|  |  Ctrl+Tab           Next Workspace       [Edit]                | |
|  |  Ctrl+Shift+Tab     Previous Workspace   [Edit]                | |
|  |  Ctrl+W             Close Workspace      [Edit]                | |
|  |  Ctrl+F             Search               [Edit]                | |
|  |  Escape             Close Modal/Dialog   —                     | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|                       [Reset to Defaults]  [Save Preferences]     |
+------------------------------------------------------------------+
```

Preferences are stored separately from pipeline configuration and are
application-wide (not per-workspace). Changes take effect immediately where
possible (accent color, font size) but still require explicit save to persist.

### 4.8 Onboarding / First Run

**Purpose:** Welcome new users and guide them through essential setup.
**Posture:** Low-density, focused — no distractions, one task per screen.

The onboarding flow appears on first launch or when no workspaces are open.
It takes over the entire main content area.

**Step 1: Welcome**

```
+------------------------------------------------------------------+
|                                                                   |
|  Step 1 of 3                                                      |
|                                                                   |
|                          [R]                                      |
|                    Ralph Workflow                                  |
|                                                                   |
|         Unattended AI development, from prompt to commit.         |
|                                                                   |
|    Write a prompt describing a feature. Ralph Workflow plans,     |
|    develops, reviews, and commits the code — automatically.       |
|                                                                   |
|                                                                   |
|                       [Get Started →]                             |
|                                                                   |
|                                                                   |
+------------------------------------------------------------------+
```

The Ralph Workflow logo (R mark from logo.svg) is displayed large and centered
in amber. The tagline is concise — one sentence explaining the value
proposition. The description is two short sentences explaining *how* it works.
Single call-to-action button.

**Step 2: Agent Tools**

```
+------------------------------------------------------------------+
|                                                                   |
|  Step 2 of 3                                                      |
|                                                                   |
|  Check Your Agent Tools                                           |
|                                                                   |
|  Ralph Workflow delegates AI work to CLI tools. You need at       |
|  least one installed and configured to get started.               |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |  Claude Code                                                  | |
|  |  ✓ Installed · v1.2.3 · Authenticated                        | |
|  |  Anthropic's coding agent. Recommended as primary developer.  | |
|  |                                           [Ready ✓]           | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |  Claude Code Switch                                           | |
|  |  ✕ Not installed                                              | |
|  |  Alternative Claude runner with provider switching.           | |
|  |                                    [Install...]  [Skip]       | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |  Codex CLI                                                    | |
|  |  ✕ Not installed                                              | |
|  |  OpenAI's coding agent. Can serve as developer or reviewer.   | |
|  |                                    [Install...]  [Skip]       | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |  OpenCode                                                     | |
|  |  ✕ Not installed                                              | |
|  |  Multi-provider agent — supports Anthropic, OpenAI, Google,   | |
|  |  and more. Models discovered dynamically from your accounts.  | |
|  |                                    [Install...]  [Skip]       | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ⓘ Each tool manages its own API keys and configuration.         |
|    Ralph Workflow just orchestrates them.                          |
|                                                                   |
|                           [← Back]         [Continue →]           |
+------------------------------------------------------------------+
```

Each tool shows its installation status, version (if installed), and whether
it's fully authenticated (set up through the tool's own auth process).
Tools that aren't installed show an "Install" button that walks the user
through installation, and a "Skip" option since not every tool is required.
The "Continue" button is disabled until at least one tool is installed and
ready. Ralph Workflow never touches API keys or auth tokens directly — each
CLI tool handles its own authentication. The onboarding step auto-detects
which tools are already installed on the user's system.

**Step 3: Open Workspace**

```
+------------------------------------------------------------------+
|                                                                   |
|  Step 3 of 3                                                      |
|                                                                   |
|  Open a Repository                                                |
|                                                                   |
|  Select a git repository to use as your workspace.                |
|  This is where Ralph Workflow will create worktrees and run       |
|  development sessions.                                            |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |                                                               | |
|  |       (folder)  [Browse for Repository...]                   | |
|  |                                                               | |
|  |  Or drag and drop a folder here                               | |
|  |                                                               | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ⓘ The repository must be a git repository. Ralph Workflow       |
|    creates worktrees alongside the repo directory.                |
|                                                                   |
|                           [← Back]       [Open & Finish →]        |
+------------------------------------------------------------------+
```

After selecting a repository, the selected path appears with a green check,
and the button text changes to "Open & Finish →".

**After onboarding:**
The user lands on the Dashboard for their newly opened workspace, with the
sidebar and activity bar visible. A small, dismissible "Quick Tips" card
appears at the top of the dashboard:

```
+--------------------------------------------------------------+
|  (lightbulb) Quick Tips                                 [✕]  |
|                                                               |
|  · Press g then h/s/w/c to navigate between pages            |
|  · Press ? to see all keyboard shortcuts                      |
|  · Create a worktree for each feature you want to build       |
|  · Click "New Session" to launch your first AI development run|
+--------------------------------------------------------------+
```

This tips card only appears once (dismissed permanently when closed).

### 4.9 Global Search

**Purpose:** Find sessions, worktrees, and runs across the current workspace.

Search is accessible via `Ctrl+K` or the search field in the Sessions sidebar.
The global search opens as a command palette overlay (like GitHub's Ctrl+K):

```
+-------- centered overlay, 600px wide --------+
|  🔍 [Search sessions, worktrees, runs...    ]|
+-----------------------------------------------+
|                                               |
|  Sessions                                     |
|  ● add-auth           Running   Dev 3/5      |
|  ✓ fix-api-routes     Complete  30m ago       |
|                                               |
|  Worktrees                                    |
|  wt-62-auth           branch: wt-62-auth     |
|                                               |
|  Recent                                       |
|  ● cache-layer        Paused    1d ago        |
|                                               |
+-----------------------------------------------+
```

- Results are categorized by type (Sessions, Worktrees)
- Results appear as-you-type (fuzzy matching)
- Keyboard navigable (↑↓ to move, Enter to select, Esc to close)
- Recent searches appear when the field is empty
- Selecting a result navigates to its detail page

### 4.10 Notification Center

**Purpose:** History of all notifications with context.

Opens as a slide-out panel from the right edge when clicking the notification
bell in the status bar:

```
                              +-------- 360px panel --------+
                              |  Notifications        [✕]   |
                              |  [All] [Unread (3)]         |
                              +------------------------------+
                              |                              |
                              |  Today                       |
                              |                              |
                              |  ✓ add-auth completed        |
                              |    3 iterations, 2 reviews   |
                              |    2 minutes ago             |
                              |    [View Session]            |
                              |                              |
                              |  ✕ login-flow failed         |
                              |    "API rate limit exceeded" |
                              |    2 hours ago               |
                              |    [View Session] [Resume]   |
                              |                              |
                              |  ⏸ cache-layer paused        |
                              |    Checkpoint saved          |
                              |    1 day ago                 |
                              |    [View Session] [Resume]   |
                              |                              |
                              |  Yesterday                   |
                              |                              |
                              |  ✓ perf-optimize completed   |
                              |    5 iterations, 1 review    |
                              |    [View Session]            |
                              |                              |
                              |  [Mark All Read]             |
                              +------------------------------+
```

- Each notification shows what happened, why (brief reason), when, and an
  action to navigate to the relevant session
- Failed/paused notifications include direct recovery actions (Resume)
- Notifications are grouped by day
- "Unread" filter shows only new notifications since last viewed
- "Mark All Read" clears all unread badges

### 4.11 Prompt Template Library

**Purpose:** Browse, create, and manage reusable prompt templates.

Accessible from the New Session Wizard (Step 1) via "Browse Templates":

```
+------------------------------------------------------------------+
|  Prompt Templates                          [+ Create Template]    |
+------------------------------------------------------------------+
|  [Search templates...]                                            |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |  Feature Implementation                          [Built-in]  | |
|  |  Standard template for implementing a new feature with        | |
|  |  tests. Includes sections for requirements, constraints,      | |
|  |  and acceptance criteria.                                     | |
|  |                                    [Preview]  [Use Template]  | |
|  +--------------------------------------------------------------+ |
|  |  Bug Fix                                         [Built-in]  | |
|  |  Structured template for fixing bugs. Includes sections for   | |
|  |  reproduction, root cause, and expected behavior.             | |
|  |                                    [Preview]  [Use Template]  | |
|  +--------------------------------------------------------------+ |
|  |  Refactoring                                     [Built-in]  | |
|  |  Template for code refactoring tasks. Focuses on what to      | |
|  |  change, constraints to preserve, and test coverage.          | |
|  |                                    [Preview]  [Use Template]  | |
|  +--------------------------------------------------------------+ |
|  |  Add auth middleware                             [Custom]     | |
|  |  My custom template for adding authentication. Last used      | |
|  |  3 days ago.                                                  | |
|  |                          [Preview]  [Edit]  [Use Template]    | |
|  +--------------------------------------------------------------+ |
|                                                                   |
+------------------------------------------------------------------+
```

- Templates are shown as cards with name, description, and a badge indicating
  "Built-in" (shipped with Ralph Workflow) or "Custom" (user-created)
- "Preview" opens a read-only view of the template content
- "Use Template" loads the template content into the prompt editor and closes
  the library
- Custom templates have an "Edit" button and can be deleted from the three-dot
  menu
- Templates are searchable by name and description

### 4.12 Changes Viewer (Diff View)

**Purpose:** Show the user exactly what code Ralph Workflow changed, with
full syntax-highlighted diffs — per-iteration and as a cumulative summary.
**Posture:** Review/inspection view — the user is studying specific code
changes to understand and verify what the AI produced.

The Changes Viewer is accessible from:
- The Run Detail page (tab alongside the Log viewer)
- Each row in the Iteration History table (clicking "3 files" opens that
  iteration's changes)
- Completed sessions in the Sessions list (via three-dot menu → "View Changes")
- The "Recent Completions" section on the Dashboard (click "View Changes")

**Full Changes View (within Run Detail):**

The main content area below the Phase Timeline switches between tabs:
**[Log]  [Changes]  [Info]**

When the "Changes" tab is active:

```
+------------------------------------------------------------------+
|  ← Sessions         add-auth                                     |
|                      "Add user authentication"         [Actions ▾]|
+------------------------------------------------------------------+
|  Phase Timeline (same as before — always visible)                 |
+------------------------------------------------------------------+
|                                                                   |
|  [Log]  [Changes ●]  [Info]                                      |
|                                                                   |
|  Showing: [All Iterations ▾]  |  7 files changed  +142  -38      |
|                                                                   |
|  +-- File Tree (left, 240px) ----+-- Diff (right, flex) --------+|
|  |                                |                               ||
|  |  src/                          |  src/auth/handler.rs          ||
|  |  ├─ auth/                      |                               ||
|  |  │  ├─ handler.rs     +45 -3   |   @@ -12,6 +12,28 @@         ||
|  |  │  ├─ middleware.rs  +32 -0   |                               ||
|  |  │  └─ mod.rs         +8  -2   |    use crate::db::Pool;      ||
|  |  ├─ routes/                    |    use crate::models::User;  ||
|  |  │  └─ mod.rs         +12 -8   |                               ||
|  |  └─ main.rs           +5  -3   |   +pub async fn login(       ||
|  |                                |   +    pool: &Pool,           ||
|  |  tests/                        |   +    credentials: Login,    ||
|  |  └─ auth_test.rs     +40 -22  |   +) -> Result<Token> {      ||
|  |                                |   +    let user = pool        ||
|  |  ────────────────────          |   +        .get_user(&cred... ||
|  |  7 files, +142 -38            |   +        .await?;           ||
|  |                                |   +                           ||
|  |                                |   +    verify_password(       ||
|  |                                |   +        &credentials.pw,   ||
|  |                                |   +        &user.hash         ||
|  |                                |   +    )?;                    ||
|  |                                |   +                           ||
|  |                                |   +    Ok(generate_jwt(user))  |
|  |                                |   +}                          ||
|  |                                |                               ||
|  +--------------------------------+-------------------------------+|
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Split layout** with a file tree on the left and the diff on the right —
  the same pattern developers know from GitHub PRs, VS Code's diff viewer,
  or `git diff` in a GUI client. This is immediately familiar (UX-6.5, UX-10.2)
- **File tree** shows the directory structure of changed files with
  additions/deletions counts per file in green/red. Clicking a file scrolls
  the diff panel to that file's changes. The currently viewed file is
  highlighted
- **Syntax highlighting** — diffs render with full language-aware syntax
  coloring appropriate to each file type (Rust, TypeScript, Python, etc.).
  Added lines have a subtle green-tinted background. Removed lines have a
  subtle red-tinted background. The colors are muted enough to not overwhelm
  the syntax highlighting but clear enough to distinguish additions from
  deletions
- **Hunk headers** (`@@ -12,6 +12,28 @@`) are shown in muted text to provide
  line number context. Context lines (unchanged) are shown in normal text
  between hunks
- **Summary bar** at the top shows total files changed, total additions, and
  total deletions. A dropdown filters by iteration ("All Iterations",
  "Iteration 1", "Iteration 2", etc.) so the user can see what changed in
  each development cycle independently
- **Per-iteration changes** — selecting a specific iteration from the dropdown
  shows only the diff for that iteration. This answers "what did iteration 3
  actually do?" which is critical for understanding the AI's development
  process
- **Unified diff format** (not side-by-side) is the default — it's more
  space-efficient and matches the developer's `git diff` muscle memory. A
  toggle for side-by-side view is available for users who prefer it:
  `[Unified ▾]` dropdown with options: Unified | Side-by-Side

**Accessing from Iteration History:**

In the Run Detail's Iteration History table, the "Files Changed" column is
a clickable link:

```
|  Iter  Duration  Files Changed  Tests       Result            |
|  ────  ────────  ────────────   ─────       ──────            |
|  1     4m 12s    3 files ←link  8/8 pass    ✓ Complete        |
|  2     3m 45s    2 files ←link  10/10 pass  ✓ Complete        |
|  3     Running...               —           ◉ In Progress     |
```

Clicking "3 files" switches to the Changes tab filtered to that iteration's
diff.

**Completed Session Summary:**

For completed sessions, the Changes tab shows the cumulative diff — every
change across all iterations combined. This is the "what did this entire
session produce?" view. The summary bar shows:

```
|  Session complete · 3 iterations · 7 files changed  +142  -38     |
|  [View by Iteration ▾]  [Copy as Patch]  [Open in Editor]        |
```

- "Copy as Patch" copies the full diff to clipboard in patch format
- "Open in Editor" opens the changed files in the user's default editor/IDE

**Empty state (no changes yet):**
```
+--------------------------------------------------------------+
|                                                               |
|   No changes yet                                              |
|                                                               |
|   Code changes will appear here as the AI develops.           |
|   The current iteration is still in progress.                 |
|                                                               |
+--------------------------------------------------------------+
```

### 4.13 Agent Tools Manager

**Purpose:** Install, configure, update, and monitor the CLI tools that
Ralph Workflow orchestrates. This is a key differentiator — Ralph Workflow
doesn't just use these tools, it helps the user manage them.
**Posture:** Setup/maintenance view — visited during initial setup and
occasionally for updates.

Accessible from Preferences or the Configuration page's Agent Tools section.

```
+------------------------------------------------------------------+
|  Agent Tools                                                      |
+------------------------------------------------------------------+
|                                                                   |
|  Ralph Workflow orchestrates these CLI tools to plan, develop,    |
|  review, and commit code. Each tool manages its own API keys      |
|  and model configuration — Ralph Workflow never handles keys      |
|  directly.                                                        |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |  Claude Code                                                  | |
|  |  Anthropic's AI coding agent                                  | |
|  |                                                               | |
|  |  Status:    ✓ Installed                                       | |
|  |  Version:   v1.2.3                                            | |
|  |  Location:  /usr/local/bin/claude                             | |
|  |  Auth:      ✓ Configured                                     | |
|  |  Health:    ● Ready                                           | |
|  |  Models:    claude-opus-4-6, claude-sonnet-4-6 (+3 more)      | |
|  |                                                               | |
|  |  [Check for Updates]  [Open CLI Settings]  [Test Connection]  | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |  Claude Code Switch                                           | |
|  |  Alternative Claude Code runner with provider switching       | |
|  |                                                               | |
|  |  Status:    ✓ Installed                                       | |
|  |  Version:   v0.5.2                                            | |
|  |  Location:  /usr/local/bin/claude-code-switch                 | |
|  |  Auth:      ✓ Configured                                     | |
|  |  Health:    ● Ready                                           | |
|  |                                                               | |
|  |  [Check for Updates]  [Open CLI Settings]  [Test Connection]  | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |  Codex CLI                                                    | |
|  |  OpenAI's AI coding agent                                    | |
|  |                                                               | |
|  |  Status:    ✓ Installed                                       | |
|  |  Version:   v0.9.1                                            | |
|  |  Location:  /usr/local/bin/codex                              | |
|  |  Auth:      ✓ Configured                                     | |
|  |  Health:    ● Ready                                           | |
|  |  Models:    gpt-4-turbo, o3 (+5 more)                         | |
|  |                                                               | |
|  |  [Check for Updates]  [Open CLI Settings]  [Test Connection]  | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  +--------------------------------------------------------------+ |
|  |  OpenCode                                                     | |
|  |  Multi-provider AI coding agent                               | |
|  |                                                               | |
|  |  Status:    ✕ Not Installed                                   | |
|  |                                                               | |
|  |  OpenCode supports multiple AI providers (Anthropic, OpenAI,  | |
|  |  Google, Groq, and more) through a unified interface. Models  | |
|  |  and providers are discovered dynamically — install it to see | |
|  |  what's available to your accounts.                           | |
|  |                                                               | |
|  |  [Install OpenCode...]                                        | |
|  +--------------------------------------------------------------+ |
|                                                                   |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Each tool gets a card** showing its full status: installed/not, version,
  binary location, whether authentication is configured (detected by checking
  the tool's own config), health (can it actually run), and available models
- **Available models line** shows a summary of models the tool can access.
  This list is fetched dynamically from the provider APIs through each tool.
  When adding an agent in Configuration (Section 4.6), the model dropdown is
  populated from this same dynamic query — the user only sees models they
  actually have access to
- **"Test Connection"** runs a quick health check — invokes the CLI with a
  trivial request to verify it's working end-to-end. Shows a brief result:
  "✓ Connection successful — claude-opus-4-6 responded" or
  "✕ Failed: authentication rejected. Open Claude Code settings to
  reconfigure."
- **"Open CLI Settings"** launches the CLI tool's own configuration
  interface (e.g., `claude config` or the tool's settings). This is where
  the user manages API keys, model preferences, provider accounts, etc. —
  Ralph Workflow doesn't duplicate these settings
- **"Check for Updates"** compares the installed version against the latest
  available. If an update is available, the button changes to
  "[Update to v1.3.0]" with a brief changelog summary
- **"Install" flow** for tools that aren't installed: a dialog walks
  through the installation steps appropriate for the user's platform
  (brew on macOS, npm/pip, manual download). Ralph Workflow can run the
  install command directly or show the command for the user to paste
- **Dynamic model discovery:** For tools like OpenCode that support many
  providers, the model list updates when the user's provider accounts change.
  A "Refresh Models" action refetches the available models from all
  configured providers

**Install Dialog example:**

```
+----------------------------------------------------+
|  Install Claude Code                               |
+----------------------------------------------------+
|                                                    |
|  Choose installation method:                       |
|                                                    |
|  [● npm (recommended)]                            |
|  [ ] Homebrew                                      |
|  [ ] Manual download                               |
|                                                    |
|  Command that will be run:                         |
|  ┌──────────────────────────────────────────────┐  |
|  │  npm install -g @anthropic-ai/claude-code    │  |
|  └──────────────────────────────────────────────┘  |
|                                                    |
|  [Cancel]                    [Install]              |
+----------------------------------------------------+
```

After installation, the tool card refreshes to show the installed version
and prompts the user to set up authentication through the tool's own
setup process.

**Update available state:**

```
|  +--------------------------------------------------------------+ |
|  |  Claude Code                                                  | |
|  |  Anthropic's AI coding agent                                  | |
|  |                                                               | |
|  |  Status:    ✓ Installed                                       | |
|  |  Version:   v1.2.3  →  v1.3.0 available                      | |
|  |                                                               | |
|  |  What's new in v1.3.0:                                        | |
|  |  · Improved context handling for large codebases              | |
|  |  · Better error recovery in long sessions                     | |
|  |                                                               | |
|  |  [Update to v1.3.0]  [Open CLI Settings]  [Test Connection]  | |
|  +--------------------------------------------------------------+ |
```

**Tool not configured state (installed but no auth):**

```
|  |  Status:    ✓ Installed                                       | |
|  |  Version:   v1.2.3                                            | |
|  |  Auth:      ✕ Not configured                                  | |
|  |  Health:    ⚠ Needs setup                                    | |
|  |                                                               | |
|  |  Claude Code is installed but needs authentication set up.    | |
|  |  Open its settings to log in or configure an API key.         | |
|  |                                                               | |
|  |  [Open CLI Settings]                                          | |
```

### 4.14 Help & Documentation

**Purpose:** In-app guidance so users never need to leave the application to
understand Ralph Workflow's concepts, features, or configuration.
**Posture:** Reference view — quick lookups and learning, not deep reading.

Ralph Workflow has several layers of in-app help, following UX-3.10 ("Help
content is positioned in context, next to the thing it explains"):

#### 4.14.1 Keyboard Shortcuts Overlay

Triggered by `?` from anywhere. A centered overlay showing all shortcuts
grouped by category:

```
+---------- centered overlay, 500px wide ----------+
|  Keyboard Shortcuts                         [✕]  |
+---------------------------------------------------+
|                                                   |
|  Navigation                                       |
|  g then h         Home                            |
|  g then s         Sessions                        |
|  g then w         Worktrees                       |
|  g then c         Configuration                   |
|  g then p         Preferences                     |
|                                                   |
|  Actions                                          |
|  Ctrl+N           New Session                     |
|  Ctrl+K           Search / Command Palette        |
|  Ctrl+F           Find in Current View            |
|  Ctrl+,           Preferences                     |
|                                                   |
|  Workspaces                                       |
|  Ctrl+Tab         Next Workspace                  |
|  Ctrl+Shift+Tab   Previous Workspace              |
|  Ctrl+W           Close Workspace                 |
|                                                   |
|  General                                          |
|  ?                This help                        |
|  Escape           Close dialog/modal              |
+---------------------------------------------------+
```

#### 4.14.2 Contextual Help Tooltips

Every configuration field, wizard step, and drain binding has a `[?]` icon
that shows a contextual tooltip on hover or click. Tooltips explain:
- **What the setting does** in plain language
- **When to change it** (common use cases)
- **Recommended values** where applicable

Examples:
- Analysis drain `[?]`: "Runs after each development iteration to verify the
  code satisfies the plan. GPT models (e.g., gpt-4-turbo) are often a good
  fit — consider using a separate chain from your developer."
- Developer Iterations `[?]`: "How many times the developer agent will attempt
  to implement the feature. More iterations = more refinement. 3-5 is typical."
- Agent Chains `[?]`: "When an agent fails (rate limit, auth error, context
  exceeded), Ralph automatically falls back to the next agent in the chain.
  Order matters — put your preferred agent first."

#### 4.14.3 Concepts Guide

Accessible from the `?` (Help) icon in the activity bar, or from Help menu.
A slide-out panel or dedicated page explaining Ralph Workflow's core concepts:

```
+------------------------------------------------------------------+
|  Help                                                             |
+------------------------------------------------------------------+
|                                                                   |
|  ▼ Core Concepts                                                  |
|  +--------------------------------------------------------------+ |
|  |                                                               | |
|  |  How Ralph Workflow Works                                     | |
|  |                                                               | |
|  |  You write a prompt → Ralph creates a plan → an AI agent      | |
|  |  develops code → another agent reviews it → Ralph commits.    | |
|  |  This repeats until the feature is complete.                  | |
|  |                                                               | |
|  |  The Pipeline                                                 | |
|  |  [Plan] → [Develop] → [Review] → [Commit]                    | |
|  |  Each phase is powered by one or more "agent drains" (see     | |
|  |  below) that select which AI agent handles each role.         | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▼ Agent Chains & Drains                                          |
|  +--------------------------------------------------------------+ |
|  |                                                               | |
|  |  Agent Chains                                                 | |
|  |  A chain is an ordered list of AI agents. When the first      | |
|  |  agent fails (rate limit, auth error, etc.), Ralph            | |
|  |  automatically tries the next one. Chains give your pipeline  | |
|  |  resilience — if one API is down, work continues.             | |
|  |                                                               | |
|  |  Agent Drains                                                 | |
|  |  A drain binds a pipeline role to a chain. Six drains map     | |
|  |  to the four pipeline phases:                                 | |
|  |                                                               | |
|  |  Plan phase:                                                  | |
|  |  · Planning — creates the implementation plan                 | |
|  |                                                               | |
|  |  Develop phase:                                               | |
|  |  · Development — writes and modifies code                     | |
|  |  · Analysis — checks code against the plan after each dev     | |
|  |    iteration (GPT models recommended for this role)           | |
|  |                                                               | |
|  |  Review phase:                                                | |
|  |  · Review — reviews code changes for quality                  | |
|  |  · Fix — addresses issues found during review                 | |
|  |                                                               | |
|  |  Commit phase:                                                | |
|  |  · Commit — generates commit messages                         | |
|  |                                                               | |
|  |  Each drain lets you pick the best agent for that phase.       | |
|  |  For example, use Claude Opus for planning, GLM for dev,      | |
|  |  and GPT for analysis/review. Fallback order is per-drain.    | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▼ Worktrees                                                      |
|  +--------------------------------------------------------------+ |
|  |  Worktrees are parallel copies of your repository — each      | |
|  |  gets its own branch and directory. They let Ralph work on    | |
|  |  multiple features simultaneously without conflicts.          | |
|  |  Named as wt-{ticket}-{description} (e.g., wt-62-gui).       | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▼ Sessions & Runs                                                |
|  +--------------------------------------------------------------+ |
|  |  A session is a complete AI development task — from prompt    | |
|  |  to committed code. A run is the execution of that session.   | |
|  |  Runs can be paused, resumed, and retried. Checkpoints save   | |
|  |  progress so you can stop and continue later.                 | |
|  +--------------------------------------------------------------+ |
|                                                                   |
|  ▼ Configuration Scopes                                           |
|  +--------------------------------------------------------------+ |
|  |  Settings can be defined at two levels:                       | |
|  |  · Global (~/.config/ralph-workflow.toml) — applies everywhere| |
|  |  · Project (.agent/ralph-workflow.toml) — per-repository      | |
|  |                                                               | |
|  |  Project settings override global settings. The "Effective"   | |
|  |  tab in Configuration shows the final merged result.          | |
|  +--------------------------------------------------------------+ |
|                                                                   |
+------------------------------------------------------------------+
```

**Key design decisions:**

- **Collapsible sections** so the user can scan topics and expand what they
  need (UX-5.1)
- **Plain language, not technical docs** — this is a quick reference for
  users inside the app, not a developer guide. Jargon is explained, not
  assumed (UX-3.2)
- **Accessible from the activity bar** via the `?` (Help) icon at the bottom,
  making it reachable from anywhere in 1 click (UX-5.4)
- **Each concept links to its related page** — "Agent Chains & Drains" links
  to Configuration, "Worktrees" links to the Worktrees page, etc. These
  cross-references help users navigate from understanding to action (UX-8.4)

#### 4.14.4 Empty State Help

Every empty state in the app includes a brief explanation and a link to the
relevant help section. For example, the empty dashboard includes
"[Learn how it works]" which opens the Concepts Guide. This ensures that
even brand-new users have a path to understanding without leaving the app
(UX-1.3, UX-3.10).

---

## 5. State Design

Every page has four designed states: **loaded, loading, empty, and error**.
None of these should ever show a raw framework default or an undesigned blank.

### 5.1 Loading States

Loading uses skeleton placeholders that match the shape and layout of the
content they replace. This prevents layout shift and gives the user
spatial context for where content will appear.

**Dashboard loading:**
```
+------------------------------------------------------------------+
|  Home                                          [+ New Session]    |
+------------------------------------------------------------------+
|  +--- Stats Row -------------------------------------------------+
|  | [████████████] [████████████] [████████████] [████████████]    |
|  +----------------------------------------------------------------+
|                                                                   |
|  Active Runs                                                      |
|  +--------------------------------------------------------------+ |
|  |  [████████████████████████████████████████████████████]       | |
|  |  [████████████████████████████████████████████████████]       | |
|  +--------------------------------------------------------------+ |
```

Skeleton blocks use a subtle shimmer animation (dark to slightly lighter,
repeating). The page title and section headers render immediately — only
data-dependent content uses skeletons.

**Run Detail loading:**
The phase timeline renders immediately with all four phase labels visible (Plan,
Develop, Review, Commit) but in a neutral/loading state. The log panel shows
a "Connecting..." message. The info panel shows skeleton blocks for each field.

**Sessions List loading:**
The filter tabs render immediately. The table header renders immediately. Row
placeholders shimmer.

### 5.2 Empty States

Empty states are designed, not blank. They tell the user what belongs here
and how to populate it.

**Dashboard — No sessions yet:**
```
+------------------------------------------------------------------+
|  Home                                          [+ New Session]    |
+------------------------------------------------------------------+
|                                                                   |
|                          [R]                                      |
|                                                                   |
|              No sessions in this workspace yet                    |
|                                                                   |
|    Create a worktree and start your first AI development session. |
|    Ralph Workflow will plan, develop, review, and commit code     |
|    based on your prompt.                                          |
|                                                                   |
|                      [+ New Session]                              |
|                   [Learn how it works]                             |
|                                                                   |
+------------------------------------------------------------------+
```

The Ralph Workflow logo appears in muted opacity as a visual anchor. The
message explains what the area is for. A prominent action button gives
the user a clear next step. A secondary link offers guidance.

**Sessions — Empty after filtering:**
```
+--------------------------------------------------------------+
|                                                               |
|   No failed sessions                                          |
|                                                               |
|   None of your sessions have failed. That's a good sign!      |
|   Switch to "All" to see all sessions.                        |
|                                                               |
+--------------------------------------------------------------+
```

Contextual empty states acknowledge the filter and suggest alternatives.

**Worktrees — No worktrees:**
```
+--------------------------------------------------------------+
|                                                               |
|   No worktrees created yet                                    |
|                                                               |
|   Worktrees let you work on multiple features in parallel.    |
|   Each worktree gets its own branch and directory.            |
|                                                               |
|                    [+ Create Worktree]                         |
|                                                               |
+--------------------------------------------------------------+
```

### 5.3 Error States

Errors are shown in context with clear recovery actions.

**Connection lost:**
A persistent banner appears at the top of the main content area:

```
+--------------------------------------------------------------+
|  ✕ Unable to connect to the backend. Retrying...   [Retry Now] |
+--------------------------------------------------------------+
```

The status bar indicator changes from green "Connected" to red "Disconnected".
The rest of the UI remains visible with stale data — it doesn't blank out the
entire screen. (UX-11.1)

**Workspace not found:**
If a previously opened workspace path no longer exists:

```
+--------------------------------------------------------------+
|                                                               |
|   Workspace not found                                         |
|                                                               |
|   The directory /path/to/old-repo no longer exists.           |
|   It may have been moved, renamed, or deleted.                |
|                                                               |
|   [Remove from List]  [Browse for New Location]               |
|                                                               |
+--------------------------------------------------------------+
```

**Configuration save failed:**
Error appears inline below the Save button:

```
|  ✕ Could not save configuration: permission denied.           |
|    Check that ~/.config/ralph-workflow.toml is writable.       |
|                                            [Try Again]         |
```

**Session launch failed:**
A dialog with the error and recovery options:

```
+--------------------------------------------+
|  Session Launch Failed                     |
+--------------------------------------------+
|                                            |
|  Could not start the session:              |
|  "No API key configured for agent claude"  |
|                                            |
|  [Go to Configuration]  [Cancel]           |
+--------------------------------------------+
```

Every error message follows the pattern: **what happened** + **why** +
**what to do about it**. (UX-11.2)

### 5.4 Confirmation Dialogs

Destructive actions always require confirmation with specific consequences
stated:

**Cancel a running session:**
```
+--------------------------------------------+
|  Cancel Session?                           |
+--------------------------------------------+
|                                            |
|  This will stop the "add-auth" session.    |
|  The current iteration will be             |
|  interrupted and in-progress changes       |
|  may be lost.                              |
|                                            |
|  A checkpoint will be saved so you can     |
|  resume later.                             |
|                                            |
|         [Keep Running]  [Cancel Session]   |
+--------------------------------------------+
```

**Delete a worktree:**
```
+--------------------------------------------+
|  Delete Worktree?                          |
+--------------------------------------------+
|                                            |
|  This will delete the worktree             |
|  "wt-62-auth" and its directory.           |
|                                            |
|  ⚠ Any uncommitted changes in this        |
|  worktree will be permanently lost.        |
|                                            |
|             [Cancel]  [Delete Worktree]    |
+--------------------------------------------+
```

The destructive button uses red/error color and is positioned on the right.
The safe/cancel option is the visual default. (UX-3.3)

---

## 6. Keyboard Shortcuts

| Shortcut          | Action                          |
|-------------------|---------------------------------|
| `g` then `h`     | Navigate to Home/Dashboard      |
| `g` then `s`     | Navigate to Sessions            |
| `g` then `w`     | Navigate to Worktrees           |
| `g` then `c`     | Navigate to Configuration       |
| `g` then `p`     | Navigate to Preferences         |
| `?`               | Show keyboard shortcuts overlay |
| `Ctrl+N`          | New Session                     |
| `Ctrl+,`          | Open Preferences                |
| `Ctrl+K`          | Open Search / Command Palette   |
| `Ctrl+F`          | Search within current view      |
| `Ctrl+Tab`        | Next workspace                  |
| `Ctrl+Shift+Tab`  | Previous workspace              |
| `Ctrl+W`          | Close current workspace         |
| `Escape`          | Close modal/dialog/overlay      |

The `?` shortcut opens a full keyboard shortcuts reference as a centered
overlay, showing all shortcuts grouped by category (Navigation, Actions,
Workspaces, General).

Shortcuts use the `g` prefix pattern (like vim) for navigation — press `g`,
then the section letter within 500ms. This avoids conflicts with text input.

---

## 7. Responsive Behavior

While this is a desktop app, window resizing should be handled gracefully:

| Window Width    | Layout                                               |
|-----------------|------------------------------------------------------|
| >= 1200px       | Full layout: activity bar + sidebar + content         |
| 900-1199px      | Activity bar + collapsed sidebar (icons only) + content |
| < 900px         | Activity bar only, sidebar as overlay on demand       |

Minimum window size: 800x600px.

The Run Detail split panel collapses to a tabbed view (Log | Info) below
1000px width. The dashboard stat cards reflow from 4-across to 2x2 below
1100px.

---

## 8. Accessibility

- All interactive elements have visible focus rings (2px amber outline)
- Tab order follows visual layout (activity bar → sidebar → content)
- ARIA labels on all icon-only buttons
- Status badges include both icon and text label — never color alone
- Keyboard navigation for all features, no mouse-only interactions
- Screen reader support for phase timeline and status changes
- `aria-live` regions for log streaming, status bar updates, and
  notification count changes
- Contrast ratio: minimum 4.5:1 for body text, 3:1 for large text and
  UI elements
- Focus management: after wizard step transitions, focus moves to the
  first interactive element on the new step
- Custom keyboard shortcuts don't override system or screen reader shortcuts
- Reduced motion: all animations respect `prefers-reduced-motion`

---

## 9. Cross-Page Patterns

### 9.1 Page Header Pattern

Every page follows the same header structure:

```
|  Page Title                              [Primary Action Button]  |
```

The title is Display typography (24px, weight 600), left-aligned. The primary
action button (if any) is amber, right-aligned. This pattern is consistent on
every page — the user always knows where to look for the page name and the
primary action.

### 9.2 List/Table Pattern

All lists in the application share:
- Consistent row height (48px for compact, 72px for cards with secondary text)
- Hover state using `--bg-raised`
- Click anywhere on the row to navigate to the detail view
- Status badges positioned consistently (right side)
- Three-dot menus for secondary actions (right edge)
- Right-click context menus on list rows, mirroring the three-dot menu actions
  (UX-10.1: "Right-click context menus are available where users expect them")
- Horizontal dividers between rows using `--border-default`

### 9.3 Form Pattern

All forms share:
- Labels above inputs (never placeholder-only)
- Help text below inputs in `--text-muted` color
- Error messages below inputs in `--status-error` color, appearing on blur
- Consistent input height (40px for single-line, variable for multi-line)
- Consistent spacing between form groups (24px)
- Primary button (Save/Create/Launch) is amber, right-aligned
- Secondary button (Cancel/Back) is outlined, left of primary

### 9.4 Navigation Feedback

- The active page is indicated in the activity bar (amber left border + tinted
  background on the active icon)
- Back links use "← Page Name" format at the top of detail pages
- All transitions between pages use a 250ms ease-out fade to prevent jarring
  swaps
- Navigating back to a list page preserves scroll position and filter state
