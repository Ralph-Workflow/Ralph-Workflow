# Worktrees, Configuration, And Preferences

Focus: `AC-6`, `AC-7`, `AC-8`, UX-3.5, UX-5.3, UX-5.4, UX-11, UX-13.3.

## Worktrees Page

```
+--------------------------------------------------------------------------------+
| Worktrees                                                      [New Worktree]  |
+--------------------------------------------------------------------------------+
| Main repository                                                                |
| my-repo · main                                                                 |
| Choose a worktree to review status, continue work, or open its folder.         |
|                                                                                |
| Active (2)                                                                     |
| add-auth       1.2 GB  Running · Develop 3 of 5 [Open Run] [Open Folder] [More] |
| fix-api-routes 0.8 GB  Running · Review 1 of 2  [Open Run] [Open Folder] [More] |
|                                                                                |
| Not Running (3)                                                                |
| cache-layer    0.9 GB  Paused · checkpoint saved [Resume] [View Diff] [More]   |
| refactor-db    0.4 GB  Ready to start            [Start Session] [Open Folder] [More] |
| update-deps    0.6 GB  Completed 2h ago          [Start Session] [View Diff] [More] |
+--------------------------------------------------------------------------------+
```

- Active and not-running groupings remain because they match how users scan for work
- Overflow actions are labeled as `More` instead of an unlabeled glyph
- `Open Folder` uses platform-neutral language and keeps the action explicit

Annotation:

- A short page-level sentence explains what users can do here before they scan rows and actions
- Session actions and filesystem actions stay separate to reduce accidental wrong-path clicks
- Status labels use text plus state words rather than color-only shorthand so worktree health is scannable and accessible
- `Not Running` is clearer than `Idle` because it describes system state directly rather than relying on interpretation
- `Remove Worktree` appears disabled in active-row menus until active runs stop; inactive rows expose it as a normal destructive action with confirmation

## Worktrees Empty / Loading / Unavailable States

```
+------------------------------------------------------------------------+
| Worktrees                                              [New Worktree]  |
+------------------------------------------------------------------------+
| No worktrees yet. Create one to isolate a task before starting a run.  |
|                                                    [New Worktree]      |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| Worktrees                                                              |
+------------------------------------------------------------------------+
| Loading worktrees...                                                   |
| [Skeleton rows]                                                        |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| Worktrees                                              [Reconnect]     |
+------------------------------------------------------------------------+
| Workspace state is unavailable right now. Showing cached worktree      |
| names only. Actions are unavailable until the backend reconnects.      |
+------------------------------------------------------------------------+
```

## Create Worktree Dialog

```
+-----------------------------------------------------------------+
| Create Worktree                                                  |
+-----------------------------------------------------------------+
| Ticket number        [62]                                        |
| Short description    [gui-redesign]                              |
| Base branch or commit [main v]                                   |
| Use a short, searchable name so the branch and folder stay easy  |
| to scan later.                                                   |
|                                                                 |
| Preview                                                          |
| Name   wt-62-gui-redesign                                        |
| Branch wt-62-gui-redesign                                        |
| Path   ../wt-62-gui-redesign                                     |
|                                                                 |
| [ ] Create and start a session                                   |
|                                              [Cancel] [Create]   |
+-----------------------------------------------------------------+
```

- The preview keeps naming consequences visible before creation so users do not need to infer derived values

## Create Worktree Validation States

```
+-----------------------------------------------------------------+
| Create Worktree                                                  |
+-----------------------------------------------------------------+
| Ticket number        [62]                                        |
| Short description    [gui redesign]                              |
| Use letters, numbers, and dashes only.                           |
| Base branch or commit [missing-ref]                              |
| Base ref not found in this repository. Choose an existing        |
| branch or commit.                                                |
|                                              [Create disabled]   |
+-----------------------------------------------------------------+
```

## Delete Worktree Confirmation

```
+-----------------------------------------------------------------+
| Remove Worktree?                                                 |
+-----------------------------------------------------------------+
| `wt-62-gui-redesign` will be removed from the worktree list.     |
| Running sessions must be stopped before removal.                 |
|                                                                 |
| [Keep Worktree]                           [Remove Worktree]      |
+-----------------------------------------------------------------+
```

## Configuration Page

```
+--------------------------------------------------------------------------------+
| Configuration                                                                  |
+--------------------------------------------------------------------------------+
| Scope: [Effective] [Global] [Project]                    [View As TOML]        |
| Search settings...                                                             |
|                                                                                |
| v General                                                                      |
| Common settings used during everyday setup.                                    |
| Verbosity [2 v] [?]  Default: 2                                                |
| Developer Iterations [5] [?]  Default: 1-20  Source: Project  Overridden      |
| Reviewer Reviews [2] [?]  Default: 0-10  Source: Global                        |
| Max Dev Continuations [3] [?]  Default: 3                                      |
| Review Depth [Standard v] [?] Recommended for most work                        |
| Prompt Path [./PROMPT.md] [Browse]                                             |
| Templates Directory [~/.ralph/templates] [Browse]                              |
|                                                                                |
| v Execution                                                                    |
| Runtime behavior and safety defaults.                                          |
| Checkpoint Enabled [on] [?]   Isolation Mode [on] [?]                          |
| Interactive Mode [off] [?]    Auto-detect Stack [on] [?]                       |
| Developer Context [normal v]  Reviewer Context [normal v]                      |
| Force Universal Prompt [off]                                                   |
|                                                                                |
| > Retry And Fallback                                                           |
|                                                                                |
| > Git Identity                                                                 |
|                                                                                |
| v Agent Chains                                                                 |
| developer  [planner] -> [developer] -> [reviewer]   [Add Agent] [Reorder]     |
| fast-path   [developer] -> [reviewer]                 [Add Agent] [Reorder]    |
|                                                                                |
| v Drains                                                                       |
| Planning [planner v] [?]   Development [developer v] [?]                       |
| Review [reviewer v] [?]    Fix [developer v] [?]                               |
| Commit [developer v] [?]   Analysis [fast-path v] [?]                          |
| Helper: drains bind pipeline phases to named chains; chains can be shared.     |
|                                                                                |
| v Configured Agents                                                            |
| claude-opus   Claude Code · Anthropic · opus-4-6   [Edit] [Remove]            |
| opencode-gpt4 OpenCode · OpenAI · gpt-4-turbo     [Edit] [Remove]             |
|                                                                                |
| v Agent Tools                                                                  |
| Claude Code  Ready  v1.3.0  [Test Connection] [Open CLI Settings]              |
| OpenCode     Needs setup     [Test Connection] [Open CLI Settings]             |
|                                                                                |
| Dirty state: 2 unsaved changes                                                 |
|                                                           [Revert] [Save]      |
+--------------------------------------------------------------------------------+
```

Annotation:

- Scope, search, and grouped sections answer three questions in order: what layer am I editing, how do I find it, and what value will actually run
- Everyday settings stay expanded while advanced groups start collapsed so the page remains approachable without hiding depth
- Inline defaults, ranges, source labels, and override markers reduce guesswork and make precedence visible
- Contextual `[?]` help appears on fields and drain bindings so first-time users do not have to leave the page to decode unfamiliar terms like `drain` or `effective`
- Section descriptions use plain language to explain intent before individual controls

## Configuration Search / Validation / Save States

```
+------------------------------------------------------------------------+
| Configuration                                                          |
+------------------------------------------------------------------------+
| Search settings... retry                                               |
| No matching settings.                                [Clear Search]    |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| Configuration                                                          |
+------------------------------------------------------------------------+
| Developer Iterations [25]                                              |
| Must be between 1 and 20.                                              |
| High retry values may increase cost and delay feedback.                |
|                                                   [Save disabled]      |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| Configuration                                                          |
+------------------------------------------------------------------------+
| Saved project configuration successfully.                              |
+------------------------------------------------------------------------+
```

## Raw TOML Fallback

```
+-------------------------------------------------------------------+
| Configuration - Project                        [Form View] [TOML *]|
+-------------------------------------------------------------------+
| ~/.agent/ralph-workflow.toml                                       |
| ------------------------------------------------------------------ |
| developer_iterations = 5                                           |
| reviewer_reviews = 2                                               |
| review_depth = "standard"                                         |
| ...                                                                |
| ------------------------------------------------------------------ |
| Validation: no errors                                              |
|                                                    [Revert] [Save] |
+-------------------------------------------------------------------+
```

- Form and TOML modes stay in sync live; editing either view updates the other representation
- Form mode remains the default because it lowers syntax burden, while TOML stays available for expert editing and bulk review

## TOML Error / Read-Only States

```
+-------------------------------------------------------------------+
| Configuration - Project                        [Form View] [TOML *]|
+-------------------------------------------------------------------+
| Validation: line 8, expected string after `provider =`             |
| Fix the syntax error or Revert changes to save.                    |
|                                                    [Save disabled] |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Configuration - Effective                      [Form View] [TOML *]|
+-------------------------------------------------------------------+
| This file is read-only in Effective scope. Switch to Global or     |
| Project scope to make changes.                                     |
+-------------------------------------------------------------------+
```

## Preferences

```
+-------------------------------------------------------------------+
| Preferences                                                       |
+-------------------------------------------------------------------+
| v Appearance                                                      |
| Theme [Follow System v]  Accent [amber]  Sidebar width [220 px]   |
| Base font size [14 px]   Monospace font [JetBrains Mono v]        |
| Reduced motion [Follow System v]                                  |
|                                                                   |
| v Behavior                                                        |
| Polling [5s]  Auto-scroll [on]  Log buffer [5000 lines]           |
| Confirm before cancelling runs [on]  Show phase change notifications [on] |
|                                                                   |
| v Notifications                                                   |
| Desktop notifications [on]  Notify on completion [on]             |
| Notify on failure [on]  Notify on phase change [off]              |
| Notify on degraded condition [on]  Sound [On · Chime v]           |
|                                                                   |
| v Startup                                                         |
| Restore last workspaces [on]  Default view [Dashboard v]          |
| Check for updates on startup [on]                                 |
|                                                                   |
| v Keyboard Shortcuts                                              |
| Cmd/Ctrl+K   Global Search         [Rebind]                       |
| Cmd/Ctrl+F   Find In Current View  [Rebind]                       |
| Cmd/Ctrl+,   Preferences           [Rebind]                       |
| ... full shortcut list continues ...                              |
|                                                    [Reset]        |
|                                              [Reset All] [Save]   |
+-------------------------------------------------------------------+
```

- Preference changes apply immediately and persist without restart
- Motion and sound settings respect system preference first, then allow explicit override when needed
- Type controls stay visible in the primary appearance section because legibility is a first-order preference, not an advanced tweak

## Shortcut Conflict State

```
+-------------------------------------------------------------------+
| Rebind Shortcut                                                   |
+-------------------------------------------------------------------+
| Press a new shortcut for `Global Search`.                         |
| `Cmd/Ctrl+F` is already used by `Find In Current View`.           |
|                                                                   |
| [Keep Existing]                     [Use Anyway Disabled]         |
+-------------------------------------------------------------------+
```

## Reset All Preferences Confirmation

```
+-------------------------------------------------------------------+
| Reset All Preferences?                                            |
+-------------------------------------------------------------------+
| This restores appearance, behavior, notifications, startup, and   |
| shortcuts to their defaults.                                      |
|                                                                   |
| [Keep Current Settings]                      [Reset All]           |
+-------------------------------------------------------------------+
```

## Interaction, Keyboard, And Accessibility Notes

- Unsaved changes always show a dirty indicator and prompt on navigation away with `Save`, `Discard`, and `Cancel`
- Dangerous values warn before save, not after execution fails
- `More` menus and row actions must be keyboard reachable and exposed through context menus where appropriate
- Status and source information always use text labels in addition to color or placement
- Error text stays adjacent to the affected field and explains both the problem and the next action
- Section headings, helper text, and button labels stay consistent across pages so users do not relearn terms
