# Repo conversion surface tighten — 2026-05-24

## Action
Tightened the first-run conversion path on the owned repo surfaces instead of stacking another overlapping outreach or directory action.

## Why this was the highest-leverage move now
- `marketing_workflow_audit_latest.md` says the current bottleneck is `distribution_and_message_to_primary_repo_conversion`.
- `adoption_metrics_latest.json` still shows Codeberg flat across the active window.
- Reddit is structurally suspended here, Apollo is in a live measurement window until **2026-05-30**, and same-family directory / curator bursts were explicitly marked as overlap risks.
- `ADOPTION_FUNNEL_NEXT.md` says the strongest immediate asset is a clearer first-task / start-here path.

## What changed
- `README.md`
  - tightened the route-to-use sequence around one honest first run
  - moved the example first task into the main path instead of leaving it implicit
- `START_HERE.md`
  - added a copy-paste `PROMPT.md` contract
  - added a tomorrow-morning review card centered on the merge-or-not question
- `docs/README.md`
  - promoted the example first task into the best-first-clicks group

## Docs review note
- **What changed:** top-level surfaces now push evaluators through task choice, prompt shape, and review standard with fewer decisions.
- **Why it belongs here:** these are first-touch conversion surfaces, not deep docs.
- **What was pruned / merged:** no new page was added; the existing first-task and example assets were reused instead of creating another siloed guide.
- **Duplication reduced:** yes — the action tightened routing instead of scattering the same advice across more pages.
- **Why the top-level experience is better now:** a serious evaluator can land on the repo and reach a concrete first run faster, with a clearer judgment standard.

## Verification
- Reviewed `git diff -- README.md START_HERE.md docs/README.md`
- Ran a small sanity check confirming the edited files still keep Codeberg-first references

## Expected outcome
A cleaner free-use path for qualified evaluators, which should improve the odds that current traffic turns into real Codeberg inspection, issues, watches, or stars.

## Measurement window
- Review by: **2026-05-31 04:00 Europe/Berlin**
- Success signal: this path gets reused by follow-on assets or coincides with better Codeberg adoption / higher-quality first-run feedback
- Replacement condition: if the conversion surface is still flat by the review checkpoint, stop rewording the same repo surfaces and switch to a different executable demand-capture or proof lane
