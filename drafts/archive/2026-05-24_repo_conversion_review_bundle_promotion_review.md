# Docs surface review — review-bundle promotion

- Date: 2026-05-24
- Surfaces reviewed in order: `README.md` → `START_HERE.md` → `docs/README.md`

## What changed
- Promoted `content/examples/review_bundle_example.md` into the main evaluator path on the README.
- Added an explicit pre-run trust check in `START_HERE.md` so evaluators can inspect the expected morning-after handoff shape before they run.
- Moved the review-bundle example into the top `docs/README.md` first-click set.

## Why this belongs on these surfaces
- The current bottleneck is conversion from interest to free use, so the highest-intent entry points should show not just how to start, but what a trustworthy finish looks like.
- The review-bundle example is a proof asset for trust and mergeability, which belongs directly beside the first-task and workflow-composition path.

## What was pruned / shortened / merged
- Nothing new was added as a separate top-level doc page.
- No link farm was expanded; the change reorders and strengthens existing links instead of creating another navigation branch.

## Duplication check
- Duplication was reduced in practice by reusing the existing review-bundle asset instead of inventing another proof packet.
- `docs/README.md` and `START_HERE.md` now point to the same canonical example rather than implying a new bespoke explanation is needed.

## Why the top-level experience is better now
- README now answers a missing evaluator question earlier: what should the morning-after handoff look like before I trust this workflow?
- `START_HERE.md` now connects the run path to a concrete review expectation instead of leaving trust to abstract claims.
- `docs/README.md` now makes the trust/proof asset a first-click item instead of a deeper discovery.
