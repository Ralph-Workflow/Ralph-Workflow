# Shell And Workspaces

Focus: `AC-1`, `AC-2`, UX-1, UX-2, UX-3, UX-10, UX-11, UX-12.

## Primary Shell

```
+------------------------------------------------------------------------------------+
| [R] Ralph Workflow  [my-repo 2 x] [api-service ! x] [docs-site x] [+ Open Workspace] |
|                                                       [Notifications 2] [_][O][X]    |
+-----------+-------------------+-----------------------------------------------------+
| Home      | Workspace         | Dashboard                                            |
| Sessions  | my-repo           | Workspace > Dashboard                                |
| Runs (2)  | 2 running · live  |                                                     |
| Worktrees | 1 needs attention | Active runs, attention items, and recent completions |
| Config    | Connected         |                                                     |
|           |                   |                                                     |
| Help      | Quick actions     | Main content area                                   |
| Prefs     | [New Session]     |                                                     |
|           | [New Worktree]    |                                                     |
|           | [View Active Runs]|                                                     |
+-----------+-------------------+-----------------------------------------------------+
| my-repo · main | 2 running · live | 1 attention | Connected | Bell (2)            |
+------------------------------------------------------------------------------------+
```

- Primary navigation matches the product model: `Home`, `Sessions`, `Runs`, `Worktrees`, `Configuration`, `Preferences`
- Workspace tabs carry status badges, visible close affordances, and drag-to-reorder behavior
- Status bar items are clickable shortcuts to active runs, attention items, notifications, and connection details

Annotation:

- The shell makes the Workspace -> Worktree -> Session -> Run hierarchy easier to infer from the first glance
- The page title and breadcrumb separate global app context from the current page so users know both where they are and what they are looking at
- Quick actions remain near workspace status because they are workspace-scoped, not global actions
- `File > Open Workspace` and the tab-bar `+ Open Workspace` button use the same open flow; reopening an already-open workspace activates the existing tab instead of creating a duplicate
- Recent Workspaces are also available from the File menu, and startup restore reopens prior workspaces before restoring each workspace's last page

## Workspace Loading / Switching

```
+--------------------------------------------------------------------------------+
| [R] Ralph Workflow  [my-repo] [api-service *] [+ Open Workspace]               |
+-----------+-------------------+------------------------------------------------+
| Home      | Workspace         | Loading workspace: api-service                 |
| Sessions  | api-service       | Workspace > Dashboard                          |
| Runs      | Syncing state     |                                                |
| Worktrees | Reconnecting      | [Skeleton cards]                               |
| Config    |                   | [Skeleton list rows]                           |
| Help      | Recent workspaces |                                                |
| Prefs     | my-repo           | Previous page, filters, and scroll restore     |
|           | docs-site         | after load                                     |
+-----------+-------------------+------------------------------------------------+
| api-service | reconnecting... | last cached update 8s ago | [Connection Details] |
+--------------------------------------------------------------------------------+
```

- Prevents a blank screen during workspace switches
- Makes preserved-per-workspace context explicit

Annotation:

- The loading state distinguishes between "switching" and "broken" so users do not mistake live work for a frozen UI
- Cached age plus reconnecting text makes stale data legible without forcing users into a details screen
- Each workspace restores its own last page, sidebar state, filters, and scroll position after load

## No Workspaces Open

This wireframe remains required for `AC-1.1` and `AC-1.3`.

```
+--------------------------------------------------------------------------------+
| [R] Ralph Workflow                                                       [_][O][X] |
+--------------------------------------------------------------------------------+
|                                                                                |
|                         Welcome to Ralph Workflow                              |
|                                                                                |
|    Open a git repository to run sessions, track runs, and manage worktrees.   |
|                                                                                |
|              [Open Workspace]            [View Quick Start]                    |
|                                                                                |
|  Recent Workspaces                                                             |
|  +--------------------------------------------------------------------------+ |
|  | my-repo                          last opened 2h ago          [Open]      | |
|  | api-service                      last opened yesterday       [Open]      | |
|  | old-copy                         path missing                [Locate]     | |
|  +--------------------------------------------------------------------------+ |
|                                                                                |
|  No recent workspaces? Drag a repository folder here or press Cmd/Ctrl+O.     |
+--------------------------------------------------------------------------------+
```

- Adds an invalid-recent-entry recovery path instead of leaving broken history unexplained
- Calls out the keyboard shortcut and drag-drop path to reduce first-run friction

Annotation:

- The empty state teaches the product model in one scan: open a repo first, then do work inside it
- Recent workspaces reduce recall burden, while the invalid entry row shows that stale history is recoverable rather than broken

## Invalid Workspace Dialog

```
+----------------------------------------------------------------+
| Open Workspace Failed                                           |
+----------------------------------------------------------------+
| `/tmp/project-copy` is not a git repository.                    |
|                                                                 |
| Ralph Workflow can only open folders that contain git history.  |
|                                                                 |
| [Choose Another Folder]   [Locate Existing Repo]   [Cancel]     |
+----------------------------------------------------------------+
```

## Workspace Unavailable Dialog

```
+----------------------------------------------------------------+
| Workspace Unavailable                                           |
+----------------------------------------------------------------+
| `~/code/old-copy` cannot be opened right now.                   |
| Reason: folder moved or you no longer have permission.          |
|                                                                 |
| You can remove it from recents or locate the new path.          |
|                                                                 |
| [Locate Workspace]   [Remove From Recent]   [Cancel]            |
+----------------------------------------------------------------+
```

## Close Workspace With Active Runs

```
+----------------------------------------------------------------+
| Close Workspace?                                                |
+----------------------------------------------------------------+
| `my-repo` has 2 active runs. Closing now hides live monitoring  |
| but does not stop the runs.                                     |
|                                                                 |
| [Keep Workspace Open]          [Close Workspace]                |
+----------------------------------------------------------------+
```

## Notifications Tray

```
+----------------------------------------------------------------+
| Notifications (2 unread)                        [Mark all read] |
+----------------------------------------------------------------+
| add-auth failed                 2m ago          [Open Run]      |
| cache-layer resumed             5m ago          [View Session]  |
+----------------------------------------------------------------+
```

## Interaction, Keyboard, And Accessibility Notes

- Icon-only controls require visible tooltips and accessible names: `Notifications`, `Open Workspace`, window controls, tab close buttons
- Activity-bar items show tooltip text with page name and shortcut on hover or keyboard focus
- Visible focus follows reading order: title bar tabs -> activity bar -> sidebar -> page header -> page content -> status bar
- Sidebar has a drag handle for resize, supports collapse/expand, and preserves collapse state across sessions
- Recommended shortcuts stay consistent across the app: `Cmd/Ctrl+O` open workspace, `Cmd/Ctrl+1..9` switch workspace tabs, `Cmd/Ctrl+Tab` and `Cmd/Ctrl+Shift+Tab` cycle workspaces, `Cmd/Ctrl+W` closes the current workspace, `Cmd/Ctrl+N` opens New Session, `Cmd/Ctrl+K` opens command palette, `Cmd/Ctrl+F` opens in-view search, `Cmd/Ctrl+,` opens preferences, `g` then `p` jumps to Preferences, `?` opens shortcut help, `Escape` closes trays, dialogs, and overlays
- Workspace tabs support overflow, horizontal scroll, and a context menu with `Close`, `Close Others`, and `Copy Path`
- Status changes are announced in a live region and never rely on color alone; badges always include text
- Disabled actions explain why, for example `View Active Runs` disabled when the backend is offline
