# Repo conversion docs push — 2026-05-23

## Action
Shipped the already-prepared Codeberg-first conversion docs upgrade to the live public repos.

## What went live
- `README.md`
- `START_HERE.md`
- `docs/README.md`

Commit:
- `76d93e0c50cc9d426de3b0c16f326b3d78081f26` — `docs: sharpen first-run conversion path`

## Why this was the highest-leverage move
The current bottleneck is conversion from interest to free use on the primary repo. Reddit is structurally constrained, Apollo is already inside a live measurement window, and curator/directory lanes are saturated. Shipping the conversion upgrade to the live repo surfaces improved the main Codeberg evaluation path immediately.

## Verification
- committed only the three intended conversion-surface files
- post-push markdown link check passed
- `git ls-remote` confirmed the same commit on `origin/main` and `github/main`

## Runtime repairs handled in the same run
- recovered from a non-fast-forward Codeberg push rejection
- rebased onto the latest primary repo head
- resolved the docs-map conflict without dropping the stronger first-run route
- preserved unrelated pre-existing local edits in stash plus a patch backup
- restored the repo to a clean working tree instead of leaving conflicts behind

## Expected outcome
More qualified repo visitors should now reach a clearer first-task path, understand how to judge the morning-after result, and have a more direct Codeberg-first next step.

## Measurement window
- Review by: **2026-05-30 16:35 Europe/Berlin**
- Success signal: any Codeberg star/watch/fork delta or better first-run feedback tied to the improved repo path
- Replacement condition: if Codeberg stays flat, pair the next move with a new high-intent demand-capture or citation lane
