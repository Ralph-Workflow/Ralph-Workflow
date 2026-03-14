# Run Monitoring

Focus: `AC-5`, UX-2.4, UX-3.1, UX-3.9, UX-4.1, UX-9.4, UX-11.1, UX-13.3.

## Running State

```
+------------------------------------------------------------------------+
| <- Runs   Run Monitoring: add-auth         [Pause Run] [Cancel Run] [More] |
|          Add user authentication                                        |
+------------------------------------------------------------------------+
| [Plan]====[Develop 3 of 5 *]----[Review]----[Commit]                    |
|  1m20s     Running now           Waiting        Waiting                  |
| Current phase: Develop iteration 3 of 5                                 |
| Connection: Live | Updated just now | Auto-scroll on                    |
|                                                                        |
| [Log *] [Changes] [Info]                                               |
| [Search logs] [Level: All v] [Auto-scroll: On] [Export]                |
|                                                                        |
| 14:23:01 Starting iteration 3                                           |
| 14:23:05 Reading PLAN.md                                                |
| 14:23:20 Tests passing: 8                                               |
| 14:23:30 Writing changes                                                |
|                                                                        |
| Run details                                                            |
| Worktree wt-62-auth  Agent claude  Elapsed 12m34s  Checkpoint saved    |
|                                                                        |
| Iteration history                                                      |
| 1  [3 files]  4m12s  Tests: 8 of 8 passed   Completed                  |
| 2  [2 files]  3m45s  Tests: 10 of 10 passed Completed                  |
| 3  Running                                                             |
|                                                                        |
| Review history                                                         |
| 1  45s  Findings: 2  Completed                                         |
| 2  Running  Findings so far: 1                                         |
+------------------------------------------------------------------------+
```

- The page title names the screen and the current run together for fast orientation
- Current phase and connection status stay separate so a stale connection does not imply a failed run

Annotation:

- The timeline stays above everything else because it answers the primary monitoring question first: where is the run right now
- The run subtitle gives plain-language context so users do not need to infer meaning from the run name alone
- Log tools remain close to the log stream because they change how the same evidence is inspected, not what the run is doing
- Completed phases are clickable and open a phase summary panel; labels, icons, and status text communicate meaning even when color is unavailable
- Destructive actions stay explicit and separate from progress information; confirmation remains required before cancellation
- The global status bar remains visible on this page and continues to show aggregate run count and connection health

## Stream Interrupted / Reconnecting State

```
+------------------------------------------------------------------------+
| Run Monitoring: add-auth                                              |
+------------------------------------------------------------------------+
| [Plan]====[Develop 3 of 5 *]----[Review]----[Commit]                  |
| Current phase: Develop iteration 3 of 5                               |
| Connection issue: Reconnecting. Showing cached output from 18s ago.   |
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
| No log output yet. Activity appears here when the run starts writing. |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| [Changes *] [Info]                                                    |
| No code changes yet. Files and diffs appear here after the run        |
| writes changes.                                                       |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| Run details                                                           |
| Some details could not be loaded. Showing run status and logs that    |
| are still available.                                   [Retry]         |
+------------------------------------------------------------------------+
```

## Completed State

```
+-------------------------------------------------------------------+
| <- Runs   Run Monitoring: add-auth                         [More]  |
+-------------------------------------------------------------------+
| [Plan]====[Develop]====[Review]====[Commit]                       |
| Completed   Completed    Completed    Completed                   |
|                                                                   |
| [Completed] Run finished successfully                   25m 30s   |
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
| <- Runs   Run Monitoring: login-flow   [Resume Run] [Retry] [More] |
+-------------------------------------------------------------------+
| [Plan]====[Develop X 2 of 5]----[Review]----[Commit]              |
|                                                                   |
| [Error] Run stopped during Develop                                |
| API rate limit exceeded                                           |
| What happened: the provider stopped accepting requests            |
| Next step: resume when capacity returns, switch agent, or retry   |
| [Resume Run] [Retry From Start] [Go To Config]                    |
|                                                                   |
| [Log *] [Changes] [Info]                                          |
| ... last live output and stderr context ...                       |
+-------------------------------------------------------------------+
```

## Degraded State

```
+-------------------------------------------------------------------+
| <- Runs   Run Monitoring: add-auth          [Pause Run] [More]    |
+-------------------------------------------------------------------+
| [Plan]====[Develop 4 of 5 *]----[Review]----[Commit]              |
| [Degraded] Fallback agent active after 2 retry attempts           |
| Current agent: codex  Previous agent: claude                      |
| [Why This Changed] [Open Configuration]                           |
+-------------------------------------------------------------------+
```

## Paused State

```
+-------------------------------------------------------------------+
| <- Runs   Run Monitoring: cache-layer      [Resume Run] [More]    |
+-------------------------------------------------------------------+
| [Plan]====[Develop || 3 of 5]----[Review]----[Commit]             |
|                                                                   |
| [Paused] Checkpoint saved                                         |
| Paused 6m ago. Resume will continue from iteration 3.             |
| [Resume Run]                                  [Cancel Run]         |
|                                                                   |
| [Log *] [Changes] [Info]                                          |
| ... output up to pause point ...                                  |
+-------------------------------------------------------------------+
```

## Cancel Confirmation

```
+-------------------------------------------------------------------+
| Cancel Run?                                                       |
+-------------------------------------------------------------------+
| `add-auth` will stop now. Completed changes stay on disk, but the |
| run will no longer continue automatically.                        |
|                                                                   |
| [Keep Running]                                [Cancel Run]         |
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
- Status always appears with text plus shape or badge, not color alone
- The screen keeps one dominant action per state; secondary and infrequent actions stay under `More`
- Log rendering uses a monospace font, supports ANSI colors as a supplement only, virtualizes large logs, and resumes replay from the last acknowledged sequence boundary after reconnect
- Clicking the files-changed count in Iteration history opens the Changes tab filtered to that iteration
- Relative timing appears alongside timestamps where useful, for example `2m ago` and `14:23:01`
- Empty and partial-data states explain what is unavailable and what the user can still do
- Animations stay subtle, communicate state only when needed, and respect `prefers-reduced-motion`; pulse/live indicators fall back to static badges
- Typography should preserve clear contrast between page title, current phase, metadata, and log content; avoid compressing dense status text into one visual weight
