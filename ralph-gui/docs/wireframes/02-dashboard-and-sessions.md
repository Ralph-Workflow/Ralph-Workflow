# Dashboard And Sessions

Focus: `AC-3`, `AC-4`, UX-2, UX-4, UX-5, UX-6, UX-9, UX-11.

## Dashboard

```
+-------------------------------------------------------------------+
| Dashboard                                       [New Session]      |
+-------------------------------------------------------------------+
| [Quick Tips: shortcuts, first session, worktrees]             [x] |
| Quick Actions: [New Session] [Create Worktree] [Open Configuration] |
|                                                                   |
| +--Active Runs--+ +--Worktrees--+ +--Completed Today--+ +--Success Rate--+ |
| | 3 running now | | 5 total      | | 12 completed      | | 95% passing   | |
| | +1 vs 1h ago  | | 2 active      | | +4 vs yesterday   | | stable        | |
| +---------------+ +---------------+ +-------------------+ +----------------+ |
|                                                                   |
| Active Runs                                          [View all ->] |
| [add-auth]       Plan > Develop > Review > Commit   12m [Open Run] |
|                  now in Develop 3 of 5   claude                    |
| [fix-api-routes] Plan > Develop > Review > Commit    5m [Open Run] |
|                  now in Review 1 of 2    codex                     |
|                                                                   |
| Needs Attention                                                    |
| [login-flow] Failed 12m ago  API rate limit exceeded [Resume] [View Logs] |
| [cache-layer] Paused 1d ago  checkpoint saved       [Resume] [View Logs] |
|                                                                   |
| Recent Completions                                                 |
| [perf-optimize] 3 iterations, 2 reviews, 7 files   30m ago [Open Run] |
| [cleanup-ci]    2 iterations, 1 review, 4 files    2h ago [Open Run] |
| [cache-tuning]  4 iterations, 2 reviews, 9 files    1d ago [Open Run] |
+-------------------------------------------------------------------+
```

- The dashboard shows the full pipeline sequence instead of only shorthand phase text
- `Open Run` and `View Logs` make output access obvious from every run reference
- `Dashboard` replaces the earlier `Home` label to improve page identity consistency

Annotation:

- The page is organized around three user questions: what is running, what needs attention, and what just finished
- Summary cards stay compact and interpretable; they support scanning rather than acting as decorative counters
- Recovery actions remain inline so the user does not have to navigate away to keep work moving
- Summary cards use a responsive bento grid: 4-up on wide windows, 2-up on medium widths, and 1-up on narrow widths
- Recent Completions represents the last 5-10 completed runs; opening one lands on the read-only completed run detail view

## Dashboard Loading / Empty / Offline States

```
+-------------------------------------------------------------------+
| Dashboard                                       [New Session]      |
+-------------------------------------------------------------------+
| Loading workspace activity...                                     |
| [Skeleton cards]                                                  |
| [Skeleton active run rows]                                        |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Dashboard                                       [New Session]      |
+-------------------------------------------------------------------+
| No sessions yet. Start a new session to see live progress,        |
| failures, and completed changes here.                             |
|                                                  [New Session]    |
+-------------------------------------------------------------------+
```

```
+-------------------------------------------------------------------+
| Dashboard                                       [Reconnect]        |
+-------------------------------------------------------------------+
| Backend unavailable. Showing cached activity from 42s ago.        |
| Active run actions are temporarily disabled until the connection   |
| returns.                                                          |
+-------------------------------------------------------------------+
```

## Sessions List

```
+---------------------------------------------------------------------------+
| Sessions        [Search descriptions, worktrees, run IDs...] [New Session] |
+---------------------------------------------------------------------------+
| [All 23] [Running 3] [Paused 2] [Completed 15] [Failed 3]                 |
| Worktree: [All worktrees v]   Sort: [Newest first v]                      |
+---------------------------------------------------------------------------+
| [ ] Name ^v         Status ^v Pipeline ^v          Agent ^v Age ^v More   |
| [ ] add-auth        Running   Dev 3 of 5           claude   12m    [Open] |
| [ ] fix-api-routes  Running   Rev 1 of 2           codex     5m    [Open] |
| [ ] login-flow      Failed    Dev 2 of 5           claude    2h    [Logs] |
| [ ] cache-layer     Paused    Rev 1 of 2           codex     1d  [Resume] |
+---------------------------------------------------------------------------+
| 2 selected                               [Resume] [Cancel] [Delete]       |
+---------------------------------------------------------------------------+
```

- Broad-to-narrow filtering remains the default sequence: status, worktree, then search
- Row actions make direct output access and inline recovery explicit

Annotation:

- This list is designed as a management view: scan, filter, act, and return without losing context
- Returning from a run detail preserves the current filters, sort, selection, and scroll position

## Sessions Empty / No Results State

```
+-------------------------------------------------------------------+
| Sessions        [login search term                      ]          |
+-------------------------------------------------------------------+
| No sessions match these filters.                                  |
| [Clear Search]  [Reset Filters]                                   |
+-------------------------------------------------------------------+
```

## Batch Progress Overlay

```
+----------------------------------------------------------------+
| Resuming 2 sessions                                             |
+----------------------------------------------------------------+
| add-auth         already running                           done  |
| cache-layer      checkpoint found                          done  |
|                                                                |
| Overall progress: [####################] 2 of 2                |
|                                                [Close]         |
+----------------------------------------------------------------+
```

## Batch Partial Success Result

```
+----------------------------------------------------------------+
| Batch Action Complete                                           |
+----------------------------------------------------------------+
| 1 session resumed successfully.                                 |
| 1 session needs attention.                                      |
|                                                                |
| cache-layer   resumed                               [Open Run]  |
| login-flow    provider still unavailable            [View Logs] |
|                                                                |
| [Close]                                                        |
+----------------------------------------------------------------+
```

## Destructive Action Confirmation

```
+----------------------------------------------------------------+
| Cancel 2 Sessions?                                              |
+----------------------------------------------------------------+
| The selected sessions will stop immediately. Unsaved UI state   |
| is preserved, but the runs will not continue automatically.     |
|                                                                |
| [Keep Running]                          [Cancel Sessions]       |
+----------------------------------------------------------------+
```

```
+----------------------------------------------------------------+
| Delete 2 Sessions?                                              |
+----------------------------------------------------------------+
| The selected session records will be removed from the list and  |
| history views. This cannot be undone.                           |
|                                                                |
| [Keep Sessions]                         [Delete Sessions]       |
+----------------------------------------------------------------+
```

## Interaction, Keyboard, And Accessibility Notes

- Rows support keyboard selection, `Enter` to open, `Space` to select, and `Esc` to clear bulk selection
- Batch resume applies only to paused or failed sessions; other selections disable `Resume` with an explanation
- Status changes are announced via live region and use text plus badge, never color alone
- Disabled actions explain why, for example `Resume` disabled when no checkpoint exists
- List and dashboard updates should avoid layout jumps; live updates replace row content in place
- Right-click on a session row opens a context menu with the same actions shown inline
