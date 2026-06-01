# Marketing execution — repo conversion quickstart patch

- Timestamp: 2026-05-24 01:32 Europe/Berlin
- Action: **Patch README and START_HERE with install + first-run commands**
- Channel: **primary repo conversion surface**
- Status: **executed**

## Why this was the highest-leverage move now
- The active bottleneck is conversion from interest to free use.
- Same-family directory and curator outreach are already inside overlapping measurement windows and should not be burst again right now.
- The root repo surfaces explained the concept but still made evaluators guess the executable install/run path.
- Shared artifacts already contained a proven quickstart sequence, so the strongest move was to reuse that artifact and close the repo-surface conversion gap immediately.

## Shared findings/artifacts reused
- `agents/marketing/ADOPTION_FUNNEL_NEXT.md`
- `agents/marketing/logs/marketing_workflow_audit_latest.json`
- `agents/marketing/logs/marketing_workflow_audit_latest.md`
- `Ralph-Site/vendor/Ralph-Workflow/START_HERE.md`
- `Ralph-Site/vendor/Ralph-Workflow/docs/sphinx/quickstart.md`

## Files changed
- `/home/mistlight/.openclaw/workspace/README.md`
- `/home/mistlight/.openclaw/workspace/START_HERE.md`

## What changed
- Added a clear prerequisite: Ralph Workflow orchestrates a supported agent CLI you already have installed and authenticated.
- Added the exact first-run command sequence:
  - `pipx install ralph-workflow`
  - `ralph --init`
  - `ralph --diagnose`
  - edit `PROMPT.md`
  - `ralph`
- Kept Codeberg-first CTA intact.

## Expected outcome
Increase the number of qualified evaluators who can go from repo visit to an executable first run without guessing the install path.

## Review window
- Review on **2026-05-31 Europe/Berlin**.
- Primary success signal: any Codeberg adoption delta.
- Secondary signal: better alignment between top-level repo copy and deeper quickstart docs.

## Replacement condition
If Codeberg still shows no meaningful movement after this window, treat this patch as insufficient and move to a stronger proof/distribution lane instead of doing more README-only tuning.

## Docs review note
- **What changed:** inserted prerequisite + minimal command path into the two top-level conversion surfaces.
- **Why here:** this is where evaluators decide whether they can actually try the product now.
- **What was pruned/shortened:** nothing added to navigation; no link farm expansion.
- **Duplication reduced:** yes — top-level surfaces now match the already-proven quickstart path.
- **Why the top-level experience is better:** evaluators no longer need to guess installation or whether Ralph replaces their existing agent CLI.
