# Repo conversion docs upgrade — 2026-05-23

## Action run
Upgraded the actual Ralph Workflow repo conversion surfaces in `repos/Ralph-Workflow/github-mirror`:
- `README.md`
- `START_HERE.md`
- `docs/README.md`

## Why this was the highest-leverage move
The latest adoption audit says the main bottleneck is **conversion from interest to free use** on the primary repo. Reddit is structurally constrained, Apollo is already in a live measurement window until **May 30, 2026**, and curator/directory lanes are saturated with fresh activity. A stronger repo-first evaluation path is executable right now and directly improves the odds that qualified traffic turns into Codeberg stars, watches, issues, or repeat use.

## What changed
- Added a sharper 5-minute first-run path to `README.md`
- Added a copy-paste one-paragraph spec template to `README.md` and `START_HERE.md`
- Added exact low-friction first-task shapes to reduce evaluator guesswork
- Added a morning-after review checklist centered on **"would I merge this?"**
- Tightened `docs/README.md` so the first-run conversion path is the default route instead of a diffuse doc map
- Preserved Codeberg-first / GitHub-mirror framing throughout

## Why this belongs on these surfaces
- `README.md` is the highest-traffic repo entry point, so it should answer the first-use question immediately
- `START_HERE.md` is the shortest honest evaluation path and should remove ambiguity, not just point deeper
- `docs/README.md` should route readers from interest to action, not force them to infer the best path

## What was pruned / shortened / merged
- Replaced more abstract top-level framing with a clearer first-run route
- Reduced duplication between the docs map and the manual path by making the first-run route explicit
- Kept deeper explanation in secondary docs instead of expanding top-level surfaces into manuals

## Duplication reduction
The three surfaces now share one cleaner story:
1. What Ralph Workflow is
2. What task to try first
3. How to judge the morning-after result
4. Why Codeberg is the public next step

That removes some of the old split between abstract product framing and the actual first-run evaluation path.

## Why the top-level experience is better now
A developer landing on the repo can now get from curiosity to an executable first run faster:
- less guessing about task shape
- clearer copy-paste spec start
- clearer success test
- clearer Codeberg-primary public next step

## Verification
- local link resolution check passed for the three updated surfaces
- inspected git diff for the surface-level conversion changes

## Expected outcome
Higher odds that existing qualified traffic converts into:
- Codeberg stars
- Codeberg watches
- Codeberg issues about first-run friction
- repeat usage driven by a cleaner first-run path

## Measurement window
- Review by: **May 30, 2026**
- Success signal: any Codeberg adoption delta or higher-quality first-run issues attributable to the improved repo surfaces
- Replacement condition: if Codeberg stays flat across the next review window, treat this conversion upgrade as insufficient on its own and pair the next lane with higher-intent demand capture or third-party citations
