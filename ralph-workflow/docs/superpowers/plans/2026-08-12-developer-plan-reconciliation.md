# Developer plan reconciliation implementation plan

**Outcome:** Development agents execute plans of different sizes, reconcile
inaccurate plan items without weakening the request, and submit a validated
per-item disposition that development analysis audits independently.

### [S-1] Characterize the disposition artifact contract

Add black-box markdown and model tests for the closed `completed`, `adapted`,
`not_applicable`, and `blocked` vocabulary. Require a disposition on every plan
proof in a completed result and reject `blocked` completion claims while
keeping partial results free-form.

Verify: run the focused development-result artifact tests and observe the new
cases fail before production changes, then pass afterward.

### [S-2] Preserve canonical proof coverage with dispositions

Update proof parsing and validation without changing stable plan/work-unit ID
coverage. Ensure every canonical ID still appears exactly once and disposition
metadata cannot bypass missing, duplicate, or unknown-ID failures.

Verify: run the focused execution proof tests, including linear, work-unit,
worker, and fan-in cases.

### [S-3] Give every developer prompt one progress and reconciliation loop

Single-source concise guidance that inventories plan IDs, processes ready
items, checks premises when ready, records one disposition, changes tack after
a failed approach, and reports a partial result when necessary work is blocked.
Keep compact plans direct and large independent groups concurrent. Include the
same verified-delivery and run-budget commitments in continuation prompts.

Verify: render initial, continuation, worker, and fallback prompts and assert
on observable shared behavior and ordering rather than duplicated prose.

### [S-4] Make development analysis audit plan deviations independently

Have the analyzer derive plan applicability independently from the request,
plan, and workspace while keeping request-criterion verification independent
of implementer narrative. Reject unjustified omissions without prescribing a
fix; never use the developer's disposition or rationale as evidence.

Verify: render the analyzer and assert that it separates request satisfaction
from disposition auditing and retains the evidence-first verdict contract.

### [S-5] Align format documentation and examples

Update the development-result format reference and examples with disposition
syntax, valid N/A evidence, invalid excuses, and the completed/partial rule.
Keep documentation concise and remove contradictory wording.

Verify: run the fabrication guard before and after edits, artifact-format
documentation tests, and the documentation build through the full gate.

### [S-6] Review and verify the integrated behavior

Run focused tests, obtain an independent code review, repair every material
finding, then run `make verify`. Rebase onto `main`, resolve conflicts without
discarding user changes, rerun `make verify`, and use only Ralph's generated
commit workflow for the final commit.

Verify: both pre-rebase and post-rebase authoritative gates exit zero with no
warnings or errors; the final worktree records the Ralph-generated commit.
