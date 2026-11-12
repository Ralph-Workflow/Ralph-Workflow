# Developer plan reconciliation implementation plan

> The current contract is **free-form**: the development-result and
> development-analysis-choice artifacts validate only the frontmatter
> `status` enum, the body is free-form markdown for the next agent, and
> parallel work is the default. See
> `ralph/mcp/artifacts/format_docs/development_result.md` and
> `ralph/mcp/artifacts/format_docs/development_analysis_decision.md` for
> the current contract, and
> `specs/2026-08-12-developer-plan-reconciliation-design.md` for the
> reconciliation model.

**Outcome:** Development agents execute plans of different sizes, reconcile
inaccurate plan items without weakening the request, and submit an evidence-backed
result that development analysis audits independently.

### [S-3] Give every developer prompt one progress and reconciliation loop

Single-source concise guidance that inventories plan IDs, processes ready
items, checks premises when ready, records the outcome of each ready item in
the result body, changes tack after a failed approach, and reports a partial
result when necessary work is blocked.
Keep compact plans direct and large independent groups concurrent. Include the
same verified-delivery and run-budget commitments in continuation prompts.

Verify: render initial, continuation, and fallback prompts and assert
on observable shared behavior and ordering rather than duplicated prose.

### [S-4] Make development analysis audit plan deviations independently

Have the analyzer derive plan applicability independently from the request,
plan, and workspace while keeping request-criterion verification independent
of implementer narrative. Reject unjustified omissions without prescribing a
fix; never use the developer's summary, reason, proof, or completion claim as
evidence.

Verify: render the analyzer and assert that it separates request satisfaction
from implementer narrative and retains the evidence-first verdict contract.

### [S-6] Review and verify the integrated behavior

Run focused tests, obtain an independent code review, repair every material
finding, then run `make verify`. Rebase onto `main`, resolve conflicts without
discarding user changes, rerun `make verify`, and use only Ralph's generated
commit workflow for the final commit.

Verify: both pre-rebase and post-rebase authoritative gates exit zero with no
warnings or errors; the final worktree records the Ralph-generated commit.

### [S-7] Route non-actionable analysis outcomes

Make analysis interpret every developer result from fresh evidence: complete
when criteria are already satisfied, and request changes only for an actionable
localized gap. An analysis `failed` verdict records why no safe actionable
continuation exists and returns to development for another iteration.

Verify: render the analyzer choice rules, assert all developer-result
statuses still reach analysis, and prove `failed` and `request_changes`
verdicts both return to development.

### [S-8] Trace prompting rules to research evidence

Map every normative developer/analyzer prompt behavior in the design to a
primary AI-agent or reasoning source, state the narrow inference each source
supports, and distinguish local workflow contracts that require repository
tests rather than research citations.

Verify: run the fabrication guard and documentation checks, then independently
review the traceability table for unsupported or overstated claims.
