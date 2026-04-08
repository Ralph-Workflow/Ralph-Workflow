# Feature: Technical Debt Reduction Program for Ralph Workflow

## Problem
Ralph Workflow has a strong documented architecture, but the codebase still carries concentrated technical debt that raises change cost in key areas. The biggest issues are not broad architectural collapse; they are localized hotspots where implementation and repository policy have drifted:

1. **Known correctness debt in config merging**
   - `ralph-workflow/src/config/unified/merging.rs:61-63` explicitly documents known issues with booleans and default-valued fields in `merge_with`.
2. **Deprecated API still present**
   - `ralph-workflow/src/checkpoint/size_monitor.rs:122-125` still exposes deprecated `check_json_and_log`.
3. **Large, high-change files increase rigidity and fragility**
   - `ralph-workflow/src/reducer/boundary/commit.rs` (992 lines)
   - `ralph-workflow/src/reducer/boundary/planning.rs` (989 lines)
   - `ralph-workflow/src/reducer/state_reduction/commit/mod.rs` (988 lines)
   - `ralph-workflow/src/git_helpers/rebase_checkpoint.rs` (986 lines)
   - `ralph-workflow/src/common/domain_types.rs` (935 lines)
4. **Policy compliance debt around lint and panic-oriented escape hatches**
   - The repo policy forbids `#[allow(...)]` and sharply limits `.unwrap()` / `.expect()`, but scan results found UI lint fixtures with forbidden `#![allow(...)]` and multiple non-obviously-allowed unwrap/expect call sites that should be audited and either removed or structurally justified.
5. **Testing blind spots in risk-heavy boundary areas**
   - Multiple production files in `pipeline/` and `git_helpers/` appear to lack direct unit tests, while the codebase depends heavily on deterministic recovery and boundary correctness.
6. **Documentation drift**
   - Architecture docs are strong overall, but some path references are stale and some known implementation decisions are documented in comments/RFCs rather than in current canonical docs.

This matters because Ralph is explicitly positioned as an unattended orchestrator. Technical debt in recovery, config, prompts, git handling, and verification paths creates disproportionate operational risk: small regressions can damage determinism, resume safety, or policy compliance.

## Goal
Reduce high-interest technical debt in the Ralph Workflow codebase without weakening the architectural model. The outcome should be a codebase that is easier to change, easier to verify, and more consistent with its own published standards.

Success means:
- the known config merge bug is resolved or removed,
- deprecated code is removed or fully migrated,
- the largest production hotspots are decomposed by responsibility,
- separation of concerns is improved through clearer module boundaries without introducing premature crate splits,
- lint/panic policy compliance is made explicit and enforced,
- risky modules have direct tests at the right level,
- architecture and maintenance docs accurately describe current reality.

## Evaluation Framework And Sources
Use these principles to judge priorities and trade-offs:

1. **Martin Fowler — Technical Debt Quadrant**
   - Treat debt as something to prioritize by interest and intent, not just by whether code looks ugly.
   - Use this to separate high-interest reckless debt from lower-interest prudent debt.
   - Source: https://martinfowler.com/bliki/TechnicalDebtQuadrant.html
2. **Martin Fowler — Code Smell**
   - Treat large files, deprecated shims, stringly seams, and policy exceptions as surface indicators of deeper design problems.
   - Source: https://martinfowler.com/bliki/CodeSmell.html
3. **Martin Fowler — Flag Argument**
   - Prefer explicit APIs over boolean-driven branching where call-site intent matters.
   - Apply this when reviewing config merge, workflow branching, and handler/orchestrator interfaces.
   - Source: https://martinfowler.com/bliki/FlagArgument.html
4. **Ham Vocke / Martin Fowler site — Practical Test Pyramid**
   - Put most coverage in fast unit tests for pure logic, then add narrow integration tests at boundaries.
   - Source: https://martinfowler.com/articles/practical-test-pyramid.html
5. **Robert C. Martin — Clean Code**
   - Favor small units, single responsibility, descriptive interfaces, fewer flag arguments, readable tests, and elimination of needless repetition / opacity.
   - Use this as general maintainability guidance, not as a reason to churn stable code.
6. **Repository source of truth**
   - `CODE_STYLE.md`
   - `docs/architecture/README.md`
   - `docs/architecture/event-loop-and-reducers.md`
   - `docs/agents/verification.md`
   These repo docs define the architecture and verification contract that remediation must preserve.

## User Stories
- As a maintainer, I want high-interest technical debt removed first so routine changes do not trigger cascades of unrelated fixes.
- As a contributor, I want hotspot modules broken into clearer responsibilities so I can make changes without re-learning huge files.
- As a reviewer, I want policy compliance to be structural rather than comment-based so verification catches real regressions quickly.
- As an operator, I want config, checkpoint, git, and recovery paths to be explicitly tested so unattended runs remain trustworthy.
- As a future contributor, I want architecture docs to match the code so I can extend the system without guessing.

## Scope
This effort covers technical debt in the workspace crates with priority on `ralph-workflow`, then `ralph-gui`, `xtask`, lints, and shared test infrastructure.

Primary focus areas:
- configuration merge correctness,
- checkpoint / prompt / recovery related maintainability,
- reducer and git hotspot decomposition,
- lint and panic-policy compliance,
- boundary-focused testing,
- architecture documentation accuracy.

Out of scope unless directly required by the debt being fixed:
- redesigning the whole reducer architecture,
- splitting `ralph-workflow` into additional crates without clear evidence that a subsystem can own a stable, narrow contract,
- replacing core libraries just because newer versions exist,
- speculative abstractions with no demonstrated payoff,
- cosmetic churn that does not reduce risk or change cost.

Module-boundary guidance for this proposal:
- Treat the current problem primarily as hotspot concentration inside `ralph-workflow`, not automatic proof that the crate boundary is wrong.
- Prefer extracting focused internal modules first, especially where large files mix prompt preparation, validation, archival, git boundary work, recovery mapping, or unrelated domain types.
- Consider a crate split only if a subsystem becomes independently reusable or can depend on a stable public contract instead of `PipelineState` / `Effect` / `PipelineEvent` internals.

## Evidence Collected From The Current Codebase
### Architecture strengths to preserve
- The codebase is intentionally reducer-driven: `State -> Orchestrator -> Effect -> Handler -> Event -> Reducer -> State`.
- Architecture docs and `CODE_STYLE.md` consistently emphasize pure orchestration/reduction with explicit effect boundaries.
- The repo already has strong verification expectations via `cargo xtask verify`.

### Concrete debt signals
- `ralph-workflow/src/config/unified/merging.rs:62` — known issues comment on merge correctness.
- `ralph-workflow/src/checkpoint/size_monitor.rs:122` — deprecated wrapper still present.
- `tests/integration_tests/_TEMPLATE.rs:260` — TODO placeholder still present.
- Large production files:
  - `ralph-workflow/src/reducer/boundary/commit.rs`
  - `ralph-workflow/src/reducer/boundary/planning.rs`
  - `ralph-workflow/src/reducer/state_reduction/commit/mod.rs`
  - `ralph-workflow/src/git_helpers/rebase_checkpoint.rs`
  - `ralph-workflow/src/common/domain_types.rs`
- Stringly or low-semantic type seams still exist in places such as:
  - `ralph-workflow/src/config/validation/key_detection.rs:13`
  - `ralph-workflow/src/executor/mock/mod.rs:13`
- Architecture docs have minor stale path references even though the underlying model remains consistent.

### Likely high-interest debt clusters
- config merge semantics,
- commit/planning boundary modules,
- commit state reduction,
- git rebase checkpoint handling,
- domain type sprawl,
- GUI command modules using unwrap/expect patterns that may not satisfy repo policy,
- missing direct tests around pipeline and git boundary behavior.

## Plan

### Phase 1 — Stabilize correctness and policy hotspots first
Address debt with the highest risk-to-change ratio before doing broad refactors.

1. **Fix or retire `UnifiedConfig::merge_with` debt**
   - Reproduce the documented boolean/default-value failure modes with failing tests.
   - Decide whether `merge_with` should remain as a backward-compatibility helper or be narrowed to test-only semantics with stronger guardrails.
   - Remove the known-issues comment by fixing the behavior, or replace the API with something whose limits are explicit and tested.
2. **Remove deprecated checkpoint size shim**
   - Find all call sites of `check_json_and_log`.
   - Migrate them to `check_json` plus caller-owned logging.
   - Delete the deprecated wrapper once unused.
3. **Clean explicit placeholder debt**
   - Remove or finalize `tests/integration_tests/_TEMPLATE.rs:260` placeholder content so templates cannot be mistaken for active test intent.
4. **Audit policy violations surfaced by scans**
   - Remove forbidden `#![allow(...)]` attributes in lint UI fixtures by restructuring the fixture or using an allowed narrower mechanism where policy permits.
   - Audit every non-boundary `.unwrap()` / `.expect()` surfaced in `ralph-gui` commands and other non-allowed locations.
   - Convert them to `Result` propagation, typed errors, or explicit boundary-only crash points as required by repo policy.

### Phase 2 — Decompose high-interest hotspot modules
Refactor only where large files map to mixed responsibilities or hide policy inside implementation.

1. **Split boundary/planning and boundary/commit by responsibility**
   - Break `reducer/boundary/planning.rs` into smaller modules aligned with one kind of effectful concern each (prompt prep, archival/write, validation handoff, etc.).
   - Break `reducer/boundary/commit.rs` similarly, preserving the event/effect contract.
   - Keep one handler-layer integration point, but move mixed effect families into focused submodules rather than growing one file per phase.
2. **Split `state_reduction/commit/mod.rs` by event category or state transition responsibility**
   - Keep reducers pure and make event handling easier to audit.
   - Prefer policy-family splits (transition rules, validation fallout, residual-file handling, termination safety) over arbitrary size-based slicing.
3. **Refactor `git_helpers/rebase_checkpoint.rs` into smaller units**
   - Separate data modeling, file I/O, rebase-step interpretation, and recovery/state mapping if they are currently mixed.
   - Treat this as internal subsystem decomposition first, not automatic evidence that `git_helpers` should become its own crate.
4. **Refactor `common/domain_types.rs` only where cohesion is weak**
   - Keep genuinely shared, cohesive types together.
   - Split unrelated clusters into focused modules if the current file is acting as a dumping ground.
   - Group types by semantic domain (for example agent, git, config, validation) rather than by “all newtypes live here”.

### Phase 3 — Strengthen type semantics and API clarity
Reduce change amplification from ambiguous primitives and overly broad interfaces.

1. Replace remaining high-value stringly seams with domain types where misuse is likely.
   - Prioritize config, executor, and boundary-facing tuples/maps whose string slots hide meaning.
2. Remove boolean-driven or ambiguous APIs where clearer explicit methods would improve call-site intent.
3. Ensure comments describing “known issues” are replaced by tests, types, or narrower APIs.

### Phase 4 — Close testing gaps using the repo’s intended test strategy
Follow the repo’s architecture and the practical test pyramid instead of just increasing test count.

1. **Pure logic**
   - Add or tighten unit tests for config merge semantics, reducer transitions, and domain-type invariants.
2. **Boundary behavior**
   - Add narrow integration tests for git helper behavior, filesystem interactions via `Workspace`, prompt generation/replay boundaries, and checkpoint serialization behavior.
3. **Risk-based hotspot coverage**
   - Add direct tests for any module touched in phases 1-3 before or during refactors.
4. **Regression tests for prior debt**
   - Each removed known issue or deprecated path must leave behind tests that prevent recurrence.

### Phase 5 — Repair documentation drift and make debt visible
Make the codebase easier to navigate after remediation.

1. Update stale architecture path references in `docs/architecture/`.
2. Add a short technical-debt status section or maintenance note documenting:
   - what debt was paid down,
   - what deliberate debt remains,
   - why remaining debt is acceptable for now.
3. Ensure RFCs or comments that mention fixed debt are either linked to canonical docs or cleaned up if they only describe solved incidents.

## Implementation Rules
- Preserve the reducer/event/effect architecture.
- Prefer responsibility-based module decomposition inside existing crates before proposing new crate boundaries.
- Do not weaken lint policy.
- Do not add `#[allow(...)]` to silence issues.
- Do not use `as any`, `@ts-ignore`, or Rust equivalents of suppression-by-convenience.
- Keep I/O in boundary modules and policy in reducers/orchestration.
- Do not split modules purely by line count; each new module should own a coherent concern.
- Prefer deletion of dead/deprecated code over keeping compatibility shims with unclear ownership.
- Use TDD for bug fixes and refactors: failing test first, then minimal fix, then cleanup.

## Sequencing
Recommended execution order:
1. Config merge correctness
2. Deprecated checkpoint API removal
3. Explicit lint/panic policy cleanup
4. Hotspot decomposition in commit/planning/rebase areas
5. Type/API tightening
6. Test expansion for touched areas
7. Documentation updates

This order follows Fowler’s debt-interest framing: pay down debt that is both risky and frequently touched before lower-interest cleanup.

## Edge Cases And Risks
- Refactoring reducer/boundary code can accidentally move policy into handlers or I/O into pure modules.
- Splitting large files can create artificial fragmentation if done by line count rather than responsibility.
- Forcing crate extraction too early can increase coupling pressure by turning internal workflow contracts into unstable public APIs.
- Replacing unwrap/expect mechanically can make code worse if typed error boundaries are not designed first.
- Some apparent debt may be prudent debt in low-change areas; do not churn stable code without measurable payoff.
- Some lint-scan findings may require confirmation against the repository’s boundary exceptions before changing behavior.

## Constraints
- `cargo xtask verify` must pass at the end.
- No ERROR/WARNING diagnostics are acceptable.
- Existing architecture docs remain the contract unless intentionally updated in the same work.
- Any surfaced issue during verification becomes part of the scope and must be fixed before completion.

## Acceptance
- [ ] The known config merge semantics issue is fixed or replaced with a narrower, explicitly tested API.
- [ ] `check_json_and_log` is no longer used and the deprecated wrapper is removed or fully justified.
- [ ] Placeholder TODO debt in active templates is removed or made intentional and explicit.
- [ ] Forbidden `#![allow(...)]` usage is eliminated.
- [ ] Non-allowed `.unwrap()` / `.expect()` usage in targeted modules is removed or relocated to documented boundary sites.
- [ ] At least the major hotspot files listed above are either decomposed or explicitly deferred with rationale.
- [ ] Hotspot refactors improve module boundaries by responsibility and do not merely redistribute code by file size.
- [ ] Any proposed crate split is either avoided or justified by a stable narrow contract and explicit payoff.
- [ ] Touched high-risk modules gain direct tests at the correct layer.
- [ ] Architecture docs reflect current paths and behavior.
- [ ] `cargo xtask verify` passes cleanly.

## Verification
Minimum required verification:
- `cargo xtask verify`

Targeted verification while executing this plan:
- focused unit tests for config merge behavior
- focused tests for checkpoint size-monitor call-site migration
- focused reducer/boundary/git helper tests for each hotspot refactor
- documentation review of updated `docs/architecture/*` references

## Definition Of Done
The work is done when the highest-interest technical debt called out above has either been removed or deliberately deferred with explicit rationale, the repository’s own architectural and linting rules are satisfied, and full verification passes without errors or warnings.