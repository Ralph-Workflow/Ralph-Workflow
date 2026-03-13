# Supporting Flows

Focus: `AC-9` to `AC-14`, plus supporting UX acceptance checks.

## Onboarding

```
+-------------------------------------------------------------------+
| Onboarding                                          [Skip For Now] |
+-------------------------------------------------------------------+
| [1 Welcome *]----[2 Agent Tools]----[3 Open Workspace]             |
|                                                                   |
| Ralph Workflow helps you plan, develop, review, and commit.       |
| You can finish setup now or come back later from Help.            |
|                                                [Get Started]      |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Onboarding                                                        |
| [1 Welcome]----[2 Agent Tools *]----[3 Open Workspace]            |
+-------------------------------------------------------------------+
| Claude Code   Ready                                               |
| Codex         Not installed     [Install] [Skip]                  |
| OpenCode      Not installed     [Install] [Skip]                  |
|                                                                   |
| Skipped tools can be installed later from Agent Tools.            |
|                                [Back] [Continue]                  |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Onboarding                                                        |
| [1 Welcome]----[2 Agent Tools *]----[3 Open Workspace]            |
+-------------------------------------------------------------------+
| Claude Code   Installed but not authenticated [Open Settings]     |
| Codex         Not installed                   [Install]            |
|                                                                   |
| At least one installed tool must be authenticated to continue.    |
|                                [Back] [Continue disabled]         |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Onboarding                                                        |
| [1 Welcome]----[2 Agent Tools]----[3 Open Workspace *]            |
+-------------------------------------------------------------------+
| Drop a repository here or [Browse For Repository]                 |
| Recent workspaces: my-repo, api-service                           |
|                                [Back] [Open And Finish]           |
+-------------------------------------------------------------------+
```

## Onboarding Recovery / Completion States

```
+-------------------------------------------------------------------+
| Agent Tools                                                       |
+-------------------------------------------------------------------+
| Installing Codex...                                               |
| [Progress indicator]                                              |
|                                                    [Skip]         |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Open Workspace                                                    |
+-------------------------------------------------------------------+
| `/tmp/project-copy` is not a git repository.                      |
| [Choose Another Folder]                           [Back]          |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| You're All Set                                                    |
+-------------------------------------------------------------------+
| Ralph Workflow is ready for this repository.                      |
| [Start First Session]                         [Open Dashboard]    |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Welcome                                                           |
+-------------------------------------------------------------------+
| Open a git repository to get started, or reopen onboarding from   |
| Help if you want the guided setup again.                          |
| [Open Workspace]                              [Open Onboarding]   |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Dashboard                                                         |
+-------------------------------------------------------------------+
| [Quick Tips: keyboard navigation, first session, worktrees]   [x] |
+-------------------------------------------------------------------+
```

Annotation:

- Onboarding stays short, task-oriented, and recoverable instead of turning into documentation
- `Back`, `Skip`, blocked-continue states, and finish states prevent the first-run flow from feeling like a trap

## Global Search / Command Palette

```
+---------------------------------------------------------------+
| Search actions, sessions, worktrees...                        |
+---------------------------------------------------------------+
| Current workspace: my-repo                                    |
| Recent                                                        |
| add-auth                                                      |
| Open Preferences                                              |
|                                                               |
| Sessions                                                      |
| add-auth            Running      Dev 3 of 5                   |
|                                                               |
| Runs                                                          |
| run-2026-03-13-001  Attached     Develop 3 of 5               |
|                                                               |
| Worktrees                                                     |
| wt-62-auth          branch wt-62-auth                         |
|                                                               |
| Commands                                                      |
| New Session           Cmd/Ctrl+N                              |
| Open Preferences      Cmd/Ctrl+,                              |
|                                                               |
| Enter open  Arrow keys move  Esc close                        |
+---------------------------------------------------------------+
```

## Global Search No Results State

```
+---------------------------------------------------------------+
| Search actions, sessions, worktrees...                        |
+---------------------------------------------------------------+
| No matching results.                                          |
| Try a different term or clear the current filters.            |
+---------------------------------------------------------------+
```

Annotation:

- Results stay grouped by user mental model rather than backend type order
- Footer hints reinforce keyboard-first usage expected in developer tools

## Contextual Search

```
+-------------------------------------------------------------------+
| Log Viewer                                                [Close] |
| Find: [rate limit                    ]  2 of 7  [Prev] [Next]     |
+-------------------------------------------------------------------+
| ... matching lines highlighted in the current view ...            |
+-------------------------------------------------------------------+
```

## Contextual Search Empty State

```
+-------------------------------------------------------------------+
| Find: [nonexistent term             ]  0 results  [Prev] [Next]   |
| No matches in the current view.                                    |
+-------------------------------------------------------------------+
```

## Contextual Search - Sessions List

```
+-------------------------------------------------------------------+
| Sessions                                                [Close]    |
| Find: [auth                       ]  3 matches                     |
+-------------------------------------------------------------------+
| Matching rows filtered by description, worktree, or run ID        |
+-------------------------------------------------------------------+
```

## Contextual Search - Configuration

```
+-------------------------------------------------------------------+
| Configuration                                           [Close]    |
| Find: [retry                      ]  6 settings match             |
+-------------------------------------------------------------------+
| Retry Delay ms   Backoff Multiplier   Max Retries ...             |
+-------------------------------------------------------------------+
```

## Notification Center

```
+----------------------------------------------------------------+
| Notifications                                  [Mark All Read] [x]|
+----------------------------------------------------------------+
| Today                                                          |
| [check] [unread] add-auth completed 2m ago   [View Session] [Dismiss] |
| [error] [unread] login-flow failed  2h ago   [Resume] [Dismiss]       |
| [pause] [read] cache-layer paused   1d ago   [Resume] [Dismiss]       |
|                                                                |
| [Dismiss Completed]                                             |
+----------------------------------------------------------------+
```

## Notification Empty / Error States

```
+----------------------------------------------------------------+
| Notifications                                                   |
+----------------------------------------------------------------+
| No notifications right now. This is where run completions,      |
| failures, pauses, and recoveries appear. [Notification Preferences] |
+----------------------------------------------------------------+
```

```
+----------------------------------------------------------------+
| Notifications                                   [Retry]         |
+----------------------------------------------------------------+
| Notifications could not be loaded. Showing unread count from    |
| cache only. You can still open the related run from the status  |
| bar.                                                            |
+----------------------------------------------------------------+
```

## Prompt Template Library

```
+--------------------------------------------------------------------------------+
| Prompt Templates                                                [Create Template] |
+--------------------------------------------------------------------------------+
| Recently Used                                                                   |
| Feature Implementation   Built-in   auth, api       [Preview] [Use]             |
|                                                                                |
| Search templates...                                                             |
| Feature Implementation   Built-in  "Build a feature end-to-end"  auth, api [Preview] [Use] |
| Bug Fix                  Built-in  "Diagnose and repair a bug"   debug [Preview] [Use]     |
| Add auth middleware      Custom    "Add auth guard + tests"      auth, custom [Preview] [Edit] [Use] |
|                                                                                |
| Template Variables                                                              |
| feature_name [Add auth]    api_area [auth routes]                               |
| Stored in: ~/.ralph/templates/                                                  |
+--------------------------------------------------------------------------------+
```

## Template Empty / Validation / Delete States

```
+-------------------------------------------------------------------+
| Prompt Templates                                [Create Template] |
+-------------------------------------------------------------------+
| No custom templates yet. Save a prompt to reuse common work.      |
| [Learn How Templates Work]                                        |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Create Template                                                   |
+-------------------------------------------------------------------+
| Name [ ]                                                          |
| Name is required.                                                 |
| feature_name [ ]                                                  |
| Required variable missing.                                        |
|                                                 [Save disabled]   |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Delete Template?                                                  |
+-------------------------------------------------------------------+
| `Add auth middleware` will be removed from your custom library.   |
|                                                 [Keep] [Delete]   |
+-------------------------------------------------------------------+
```

## Help Overlay

```
+---------------------------------------------------------------+
| Keyboard Shortcuts                                        [x] |
+---------------------------------------------------------------+
| Search: [type to filter shortcuts]                         |
| Navigation: g h, g s, g w, g c, g p                        |
| Actions: Cmd/Ctrl+N, Cmd/Ctrl+K, Cmd/Ctrl+F, Cmd/Ctrl+,    |
| Workspaces: Ctrl+Tab, Ctrl+Shift+Tab, Ctrl+W               |
| General: ?, Escape                                         |
+---------------------------------------------------------------+
```

Annotation:

- The shortcut overlay closes via `Esc` or click outside and returns focus to the prior control

## Contextual Help Tooltips

```
+-------------------------------------------------------------------+
| Review Depth [?]                                                  |
+-------------------------------------------------------------------+
| Controls how thorough review passes should be.                    |
| Use `Standard` for most work; choose `Security` for auth or risk. |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Analysis Drain [?]                                                |
+-------------------------------------------------------------------+
| Checks code against the plan after each dev iteration. GPT-style  |
| models are usually the best fit here.                             |
+-------------------------------------------------------------------+
```

## Concepts Guide

```
+-------------------------------------------------------------------+
| Concepts Guide                                                    |
+-------------------------------------------------------------------+
| v How It Works                                                    |
| v The Pipeline                                                    |
| > Agent Chains And Drains                                         |
| > Worktrees                                                       |
| > Sessions And Runs                                               |
| > Configuration Scopes                                            |
|                                                                   |
| Related pages: [Open Configuration] [Open Worktrees]              |
+-------------------------------------------------------------------+
```

## Help-Rich Empty State

```
+-------------------------------------------------------------------+
| Sessions                                                          |
+-------------------------------------------------------------------+
| No sessions yet. Sessions are the runs you launch for this        |
| workspace. Start one now or learn how the workflow fits together. |
| [New Session]                              [Learn How It Works]   |
+-------------------------------------------------------------------+
```

## Agent Tools Manager

```
+--------------------------------------------------------------------------------+
| Agent Tools                                                                    |
+--------------------------------------------------------------------------------+
| Claude Code         Developer agent CLI  Ready  v1.3.0  /usr/local/bin/claude  |
|                     Models: opus, sonnet  [Open CLI Settings] [Test Connection] |
|                     [Check Updates] [Refresh Models]                            |
| Claude Code Switch  Model switch helper  Needs setup  /usr/local/bin/cc-switch |
|                     [Open CLI Settings] [Test Connection]                       |
| Codex               OpenAI coding CLI  Auth required  /usr/local/bin/codex     |
|                     Models: o3, gpt-4.1  [Open CLI Settings] [Test Connection]  |
| OpenCode            Multi-provider CLI  Not installed                           |
|                     [Install]                                                   |
|                                                                                |
| Reachable from: Onboarding, Preferences, Configuration                         |
+--------------------------------------------------------------------------------+
```

## Agent Tool State Variants

```
+-------------------------------------------------------------------+
| Agent Tools                                                       |
+-------------------------------------------------------------------+
| Codex        Installing...                         [View Progress] |
| OpenCode     Test failed: executable not found    [Open Settings] |
| Claude Code  Incompatible version                  [Update Tool]   |
+-------------------------------------------------------------------+
```

## Agent Tool Actions

```
+-------------------------------------------------------------------+
| Install OpenCode                                                  |
+-------------------------------------------------------------------+
| Recommended method: Homebrew                                      |
| ( ) Homebrew   ( ) npm   ( ) Manual                               |
| Command preview: `brew install opencode`                          |
|                                             [Cancel] [Install]    |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Claude Code Update Available                                      |
+-------------------------------------------------------------------+
| Installed: 1.2.0   Latest: 1.3.0                                  |
| Changelog: improved auth detection, better model metadata         |
|                                             [Later] [Update Tool] |
+-------------------------------------------------------------------+
```

Annotation:

- Tool cards stay organized around readiness and next action, not backend implementation detail
- Recovery is always adjacent to the failing tool so users do not have to hunt for the next step

## Interaction, Keyboard, And Accessibility Notes

- All overlays support `Esc` to close and return focus to the triggering control
- Search result counts, install progress, and tool test results are announced through a live region
- Icon buttons require labels or tooltips; unread/read notification state uses text in addition to visual treatment
- Supporting flows always define `loading`, `empty`, `error`, and `disabled` states so secondary UI feels as polished as core screens
