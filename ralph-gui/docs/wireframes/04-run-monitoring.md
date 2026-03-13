# Run Monitoring

Focus: `AC-5`, UX-2.4, UX-3.1, UX-3.9, UX-4.1, UX-9.4, UX-11.1, UX-13.3.

## Running State

```
+------------------------------------------------------------------------+
| <- Sessions   Run Monitoring: add-auth     [Pause] [Cancel Session] [More] |
|                Add user authentication                                  |
+------------------------------------------------------------------------+
| [Plan]====[Develop 3 of 5 *]----[Review]----[Commit]                    |
|  1m20s     Running now         Waiting          Waiting                  |
| Workflow: Running in Develop 3 of 5                                    |
| Transport: Live connected | last update just now | auto-scroll on       |
|                                                                        |
| [Log *] [Changes] [Info]                                               |
| [Search] [Level: All v] [Auto-scroll: On] [Download]                   |
|                                                                        |
| 14:23:01 Starting iteration 3                                           |
| 14:23:05 Reading PLAN.md                                                |
| 14:23:20 8 tests passing                                                |
| 14:23:30 Writing changes...                                             |
|                                                                        |
| Session info                                                           |
| Worktree wt-62-auth  Agent claude  Duration 12m34s  Checkpoint yes     |
|                                                                        |
| Iteration History                                                      |
| 1  [3 files]  4m12s  8 of 8 pass   complete                            |
| 2  [2 files]  3m45s 10 of 10 pass  complete                            |
| 3  running                                                             |
|                                                                        |
| Review History                                                         |
| 1  45s  2 findings   complete                                          |
| 2  running  1 finding so far                                           |
+------------------------------------------------------------------------+
```

- The page title names the screen and the subject run together for faster orientation
- Workflow state and transport state stay separate so stale transport does not imply failed workflow state

Annotation:

- The timeline stays above everything else because it answers the primary monitoring question first: where is the run right now
- Log tools remain close to the log stream because they change how the same evidence is inspected, not what the run is doing
- Completed phases are clickable and open a phase summary panel; phase colors remain fixed: Plan=purple, Develop=blue, Review=amber, Commit=green
- The global status bar remains visible on this page and continues to show aggregate run count and connection health

## Stream Interrupted / Reconnecting State

```
+------------------------------------------------------------------------+
| Run Monitoring: add-auth                                              |
+------------------------------------------------------------------------+
| [Plan]====[Develop 3 of 5 *]----[Review]----[Commit]                  |
| Workflow: Running in Develop 3 of 5                                   |
| Transport: Reconnecting - showing cached output from 18s ago          |
| [Retry Now]                                                           |
+------------------------------------------------------------------------+
```

## Loading / Empty / Partial Data States

```
+------------------------------------------------------------------------+
| Run Monitoring: add-auth                                              |
+------------------------------------------------------------------------+
| Loading run details...                                                |
| [Skeleton timeline]                                                   |
| [Skeleton log panel]                                                  |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| [Log *] [Changes] [Info]                                              |
| No logs yet. Output will appear here after the run starts producing.  |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| [Changes *] [Info]                                                    |
| No code changes yet. This panel will show files and diffs as the AI   |
| develops the run.                                                     |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| Session info                                                          |
| Some metadata could not be loaded. Showing the run status and logs    |
| that are still available.                              [Retry]         |
+------------------------------------------------------------------------+
```

## Completed State

```
+-------------------------------------------------------------------+
| <- Sessions   Run Monitoring: add-auth                     [More]  |
+-------------------------------------------------------------------+
| [Plan]====[Develop]====[Review]====[Commit]                       |
|   ok          ok            ok            ok                       |
|                                                                   |
| [Success] Session completed successfully                 25m 30s   |
| [3 iterations] [2 reviews] [7 files changed] [18 tests passed]    |
|                                                                   |
| [Log] [Changes *] [Info]                                          |
| All iterations   7 files changed   +142  -38   [Copy Patch] [Open in Editor] |
|                                                                   |
| src/auth/handler.rs   +45 -3                                      |
| ... unified diff ...                                              |
+-------------------------------------------------------------------+
```

## Failed State

```
+-------------------------------------------------------------------+
| <- Sessions   Run Monitoring: login-flow [Resume] [Retry] [More]  |
+-------------------------------------------------------------------+
| [Plan]====[Develop X 2 of 5]----[Review]----[Commit]              |
|                                                                   |
| [Error] Session failed during Development                         |
| API rate limit exceeded                                           |
| What happened: provider stopped accepting requests                |
| What you can do: wait and resume, switch agent, or retry later    |
| [Resume Session] [Retry From Beginning] [Go To Config]            |
|                                                                   |
| [Log *] [Changes] [Info]                                          |
| ... last live output and stderr context ...                       |
+-------------------------------------------------------------------+
```

## Degraded State

```
+-------------------------------------------------------------------+
| <- Sessions   Run Monitoring: add-auth      [Pause] [More]        |
+-------------------------------------------------------------------+
| [Plan]====[Develop 4 of 5 *]----[Review]----[Commit]              |
| [Degraded] Fallback agent active after 2 retry attempts           |
| Current agent: codex  Previous agent: claude                      |
| [View Reason] [Open Configuration]                                |
+-------------------------------------------------------------------+
```

## Paused State

```
+-------------------------------------------------------------------+
| <- Sessions   Run Monitoring: cache-layer [Resume] [More]         |
+-------------------------------------------------------------------+
| [Plan]====[Develop || 3 of 5]----[Review]----[Commit]             |
|                                                                   |
| [Paused] Checkpoint saved                                         |
| Paused 6m ago. Resume will continue from iteration 3.             |
| [Resume Session]                              [Cancel Session]     |
|                                                                   |
| [Log *] [Changes] [Info]                                          |
| ... output up to pause point ...                                  |
+-------------------------------------------------------------------+
```

## Cancel Confirmation

```
+-------------------------------------------------------------------+
| Cancel Session?                                                   |
+-------------------------------------------------------------------+
| `add-auth` will stop now. Completed changes stay on disk, but the |
| run will no longer continue automatically.                        |
|                                                                   |
| [Keep Running]                              [Cancel Session]       |
+-------------------------------------------------------------------+
```

## Changes Tab Layout

```
+-------------------------------------------------------------------+
| [Log] [Changes *] [Info]                                          |
| Iteration: [All iterations v]  View: [Unified v]  +142  -38       |
| +----------------------+ +--------------------------------------+ |
| | src/                 | | @@ -12,6 +12,28 @@                   | |
| | auth/handler.rs      | | + pub async fn login(...)           | |
| | auth/middleware.rs   | | ...                                  | |
| | tests/auth_test.rs   | |                                      | |
| +----------------------+ +--------------------------------------+ |
+-------------------------------------------------------------------+
```

## Interaction, Keyboard, And Accessibility Notes

- Auto-scroll pauses when the user scrolls up and exposes a `Scroll To Bottom` recovery control
- Phase changes, failures, pauses, and reconnect states are announced in a live region
- Status is always shown with text plus badge, not color alone
- `More` expands secondary actions including `Open in Terminal`, `Open Worktree`, and repeat-safe detach/close actions; primary recovery actions stay visible in the header
- Log rendering uses a monospace font, supports ANSI colors, virtualizes large logs, and resumes replay from the last acknowledged sequence boundary after reconnect
- Clicking the files-changed count in Iteration History opens the Changes tab filtered to that iteration
- Relative timing should appear alongside timestamps where useful, for example `2m ago` and `14:23:01`
- Animations respect `prefers-reduced-motion`; pulse/live indicators fall back to static badges
