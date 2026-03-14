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
| Template: [Feature v] [Save As Template]                               |
| Characters: 1842  Words: 312                                            |
| Describe the goal, key constraints, and acceptance notes.              |
|                                                [Cancel] [Next ->]      |
+------------------------------------------------------------------------+
```

- The assistant is closed by default so the prompt editor remains the primary task surface
- Templates, preview, and history support recognition over recall without forcing extra pages
- Helper text focuses on what to include, while counts stay present but visually secondary

## Step 1 - AI Assist Open: Describe What To Build

```
+--------------------------------------------------------------------------------+
| Prompt                          [Preview] [History] [AI Assist ^]              |
|                                                 Planning helper available       |
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
| Reviewing the current prompt...                                                |
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
| [Apply Suggestion] [Edit Before Applying] [Review Again]                       |
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
| Enter a prompt to continue. Start with the goal, then add constraints |
| or acceptance notes if needed.                                        |
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
| AI Assist is unavailable because no Planning helper is configured yet.         |
| [Open Configuration ->]                                                        |
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
| Session defaults                                                  |
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
- Advanced choices stay behind `Customize` so the default path emphasizes the few settings most people change

## Step 2 - Expanded Configuration

```
+-------------------------------------------------------------------+
| Configure                                                         |
+-------------------------------------------------------------------+
| Worktree [add-auth v]                                             |
| Review depth [Standard (recommended) v]                           |
| Iterations [5]   Reviews [2]                                      |
|                                                                   |
| Session roles                                                     |
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
| Select a worktree before continuing. This keeps the run scoped  |
| to the correct workspace.                                       |
|                                                [Next -> disabled] |
+----------------------------------------------------------------+
```

```
+----------------------------------------------------------------+
| Configure                                                       |
+----------------------------------------------------------------+
| Iterations [25]                                                 |
| Enter a value from 1 to 20.                                     |
| Reviews [-1]                                                    |
| Enter a value from 0 to 10.                                     |
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
| Add at least one configured chain before launching.           |
|                                                               |
| [Open Configuration]                                          |
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
| Review before launch                               [Edit Prompt]  |
| Worktree: add-auth     Iterations: 5     Reviews: 2 [Edit Config] |
| Planning: planner      Development: developer Review: reviewer    |
| Effective configuration preview                                   |
| review_depth=standard · checkpoint=on · isolation=on             |
| planning=planner · development=developer · review=reviewer       |
| Effective config source: project + session override               |
|                                                                   |
| Preflight checks                                                   |
| [OK] Workspace available                                           |
| [OK] Agent chain configured                                        |
| [Needs attention] Prompt mentions token expiry but no             |
|                  acceptance note                                   |
|                                                [Review Prompt]     |
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
| [Blocking] Missing authentication for `claude-opus`           |
| [Blocking] Selected worktree no longer exists                 |
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
| Open Configuration to change the chain or fix the tool auth.   |
| Your draft choices are preserved.                              |
|                                                                |
| [Open Configuration]                  [Close]                 |
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
- Step changes, validation errors, helper updates, and launch failures are announced through a live region
- Focus lands on the page title on entry, then the first invalid field when validation fails
- `Enter` advances only when the current step is valid; `Esc` closes dialogs, not the whole wizard
- The stepper, preflight states, and validation states use text labels and icons so meaning does not depend on color alone
- Compact controls like `Preview`, `History`, and `AI Assist` keep visible text labels; tooltips stay supplemental
- Primary actions remain visually dominant, while counts, source metadata, and secondary utilities stay de-emphasized
- Status and error copy stays close to the affected field and tells the user what to do next
- Target sizes and spacing should support comfortable pointer and keyboard use, especially for small utility controls
- Long worktree and role lists should support search to keep choices manageable
- `Escape` closes the AI Assist panel without clearing its wizard-session conversation history
- Motion stays restrained: use subtle transitions between steps, avoid auto-advancing content, and provide a reduced-motion fallback
- Loading and failure states should persist until dismissed or resolved; do not rely on timed disappearance
