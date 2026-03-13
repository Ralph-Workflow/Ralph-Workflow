# New Session Wizard

Focus: `AC-4.3`, `AC-4.3.1`, `AC-4.3.2`, `AC-4.3.3`, UX-3.5, UX-4.2, UX-5.1, UX-11.3, UX-12.

## Step 1 - Prompt

```
+------------------------------------------------------------------------+
| New Session                                                            |
| [1 Prompt *]----[2 Configure]----[3 Review And Launch]                 |
+------------------------------------------------------------------------+
| Prompt                          [Preview] [History] [AI Assist v]      |
|                                                                        |
| +--------------------------------+                                     |
| | # Feature: Add auth            |                                     |
| | Add session auth middleware... |                                     |
| |                                |                                     |
| |                                |                                     |
| +--------------------------------+                                     |
|                                                                        |
| Characters: 1842  Words: 312  Template: [Feature v] [Save As Template] |
| Prompt is required. Clear goals and acceptance notes improve outcomes. |
|                                                [Cancel] [Next ->]      |
+------------------------------------------------------------------------+
```

- The assistant is closed by default so the prompt editor remains the primary task surface
- Counts, templates, preview, and history support recognition over recall without forcing extra pages

## Step 1 - AI Assist Open: Describe What To Build

```
+--------------------------------------------------------------------------------+
| Prompt                          [Preview] [History] [AI Assist ^]              |
|                                                 Using: planner-chain via Planning drain |
| +--------------------------------+ +-----------------------------------------+ |
| | # Feature: Add auth            | | [Describe what to build *] [Refine current prompt] | |
| | Add session auth middleware... | | Conversation                              | |
| |                                | | User: Add a dark mode toggle...            | |
| |                                | | Suggested Prompt                           | |
| |                                | | Build a persistent dark mode setting...    | |
| +--------------------------------+ | [Apply To Editor] [Edit And Apply]        | |
|                                    | Describe the feature or problem... [Send]  | |
|                                    +-----------------------------------------+ |
+--------------------------------------------------------------------------------+
```

## Step 1 - AI Assist Open: Refine Current Prompt

```
+--------------------------------------------------------------------------------+
| AI Assist                                                                      |
| [Describe what to build] [Refine current prompt *]                             |
+--------------------------------------------------------------------------------+
| Analyzing current editor content...                                            |
| [Loading indicator]                                                            |
+--------------------------------------------------------------------------------+
```

```
+--------------------------------------------------------------------------------+
| AI Assist                                                                      |
| [Describe what to build] [Refine current prompt *]                             |
+--------------------------------------------------------------------------------+
| Issues identified                                                              |
| - Missing success criteria                                                     |
| - Too much implementation detail                                               |
|                                                                                |
| Suggested improved prompt                                                      |
| "Implement auth middleware with token-expiry acceptance criteria..."           |
|                                                                                |
| [Apply Suggestion] [Edit And Apply] [Analyze Again]                            |
+--------------------------------------------------------------------------------+
```

## Step 1 - Validation / Empty / Unavailable States

```
+------------------------------------------------------------------------+
| Prompt                                                                |
+------------------------------------------------------------------------+
| +--------------------------------+                                    |
| |                                |                                    |
| |                                |                                    |
| +--------------------------------+                                    |
| Prompt is required before you can continue.                           |
|                                                [Cancel] [Next -> disabled] |
+------------------------------------------------------------------------+
```

```
+------------------------------------------------------------------------+
| History                                                                |
+------------------------------------------------------------------------+
| No recent prompts yet. Start with a short goal, or choose a template.  |
| [Use Template]                                                         |
+------------------------------------------------------------------------+
```

```
+--------------------------------------------------------------------------------+
| AI Assist                                                                      |
| [Describe what to build disabled] [Refine current prompt disabled]             |
+--------------------------------------------------------------------------------+
| No Planning agent configured. Set one up in Configuration to enable AI help.   |
| [Go To Configuration ->]                                                       |
+--------------------------------------------------------------------------------+
```

## Step 2 - Configure

```
+-------------------------------------------------------------------+
| New Session                                                       |
| [1 Prompt]----[2 Configure *]----[3 Review And Launch]            |
+-------------------------------------------------------------------+
| Worktree [add-auth v]   [Create New Worktree]                     |
|                                                                   |
| Developer (planner -> developer -> reviewer) · 5 iterations ·     |
| 2 reviews · Standard review depth                                 |
| Inline: Iterations [5] Reviews [2]   Value source: Global + Project |
|                                            [Customize v]          |
|                                                                   |
| Launch presets: [Standard Daily v] [Save Preset] [Delete Preset]  |
|                                                                   |
| [Back] [Cancel]                                      [Next ->]    |
+-------------------------------------------------------------------+
```

- The default configured view is collapsed so repeat launches stay fast
- Inline iterations and reviews keep the most common edits within one click

## Step 2 - Expanded Configuration

```
+-------------------------------------------------------------------+
| Configure                                                         |
+-------------------------------------------------------------------+
| Worktree [add-auth v]                                             |
| Review depth [Standard (recommended) v]                           |
| Iterations [5]   Reviews [2]                                      |
| Planning    [planner v]        Analysis [analyzer v]              |
| Development [developer v]      Review   [reviewer v]              |
| Fix         [developer v]      Commit   [commit v]                |
|                                                                   |
| Advanced v                                                         |
| Developer context [normal v]  Reviewer context [normal v]         |
| Checkpoint enabled [on]     Isolation mode [on]                   |
|                                                                   |
| [Reset To Defaults]                               [Customize ^]    |
+-------------------------------------------------------------------+
```

## Step 2 - Validation / Loading / Blocking States

```
+----------------------------------------------------------------+
| Configure                                                       |
+----------------------------------------------------------------+
| Worktree [none selected v]                                      |
| Select a worktree before continuing.                            |
|                                                [Next -> disabled] |
+----------------------------------------------------------------+
```

```
+----------------------------------------------------------------+
| Configure                                                       |
+----------------------------------------------------------------+
| Iterations [25]                                                 |
| Iterations must be between 1 and 20.                            |
| Reviews [-1]                                                    |
| Reviews must be between 0 and 10.                               |
|                                                [Next -> disabled] |
+----------------------------------------------------------------+
```

```
+----------------------------------------------------------------+
| Configure                                                       |
+----------------------------------------------------------------+
| Loading workspace defaults...                                   |
| [Skeleton fields]                                               |
+----------------------------------------------------------------+
```

```
+---------------------------------------------------------------+
| Setup Required                                                |
+---------------------------------------------------------------+
| No agent chains are configured for this workspace.            |
|                                                               |
| You need at least one configured chain before launching.      |
|                                                               |
| [Go To Configuration]                                         |
|                                                               |
| [Back] [Cancel]                           [Next -> disabled]   |
+---------------------------------------------------------------+
```

## Step 3 - Review And Launch

```
+-------------------------------------------------------------------+
| New Session                                                       |
| [1 Prompt]----[2 Configure]----[3 Review And Launch *]            |
+-------------------------------------------------------------------+
| Preflight summary                                   [Edit Prompt] |
| Worktree: add-auth     Iterations: 5     Reviews: 2 [Edit Config] |
| Planning: planner      Dev: developer    Review: reviewer         |
| Effective configuration preview                                   |
| review_depth=standard · checkpoint=on · isolation=on             |
| planning=planner · development=developer · review=reviewer       |
| Effective config source: project + session override               |
|                                                                   |
| Preflight checks                                                   |
| [ok] workspace available                                           |
| [ok] agent chain configured                                        |
| [warn] prompt mentions token expiry but no acceptance note         |
|                                                [Review Again]      |
| Estimated resource usage: medium · 1 planning pass · up to 5 dev iterations |
|                                                                   |
| Launch opens the new run detail page immediately.                  |
|                                          [Back] [Cancel] [Launch]  |
+-------------------------------------------------------------------+
```

## Step 3 - Blocking Preflight State

```
+----------------------------------------------------------------+
| Review And Launch                                              |
+----------------------------------------------------------------+
| [error] Missing authentication for `claude-opus`               |
| [error] Selected worktree no longer exists                     |
|                                                                |
| Fix the blocking issues before launching.                      |
| [Edit Configuration]   [Choose Worktree]   [Back]              |
+----------------------------------------------------------------+
```

## Launching / Launch Failure / Leave Draft States

```
+----------------------------------------------------------------+
| Launching Session                                              |
+----------------------------------------------------------------+
| Creating run, saving prompt, and opening monitoring view...    |
| [Loading indicator]                                            |
+----------------------------------------------------------------+
```

```
+----------------------------------------------------------------+
| Session Launch Failed                                          |
+----------------------------------------------------------------+
| Missing authentication for `claude-opus`.                      |
|                                                                |
| Open Configuration to change the chain, or fix the tool auth.  |
| Your draft choices are preserved.                              |
|                                                                |
| [Go To Configuration]                  [Close]                 |
+----------------------------------------------------------------+
```

```
+----------------------------------------------------------------+
| Leave New Session Wizard?                                      |
+----------------------------------------------------------------+
| Your prompt and session choices are not launched yet.          |
|                                                                |
| [Stay Here]        [Discard Draft]        [Save As Template]   |
+----------------------------------------------------------------+
```

## Interaction, Keyboard, And Accessibility Notes

- `Back` is always available after step 1; returning to an earlier step preserves entered values
- Step changes, validation errors, and launch failures are announced through a live region
- Focus lands on the page title on entry, then the first invalid field when validation fails
- `Enter` advances only when the current step is valid; `Esc` closes dialogs, not the whole wizard
- Compact controls like `Preview`, `History`, and `AI Assist` require text labels and tooltips
- Long worktree and role lists should support search to keep choices manageable
- `Escape` closes the AI Assist panel without clearing its wizard-session conversation history
