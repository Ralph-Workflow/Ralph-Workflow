# Configurable Orchestration Layer

**Date:** 2026-04-09
**Status:** Proposed
**Scope:** Define the intended orchestration flow explicitly, replace the remaining legacy structured-output assumptions with an MCP-artifact-only orchestration contract, and move drain routing, phase wiring, and orchestration invariants into validated TOML so bad agent selection fails fast instead of silently falling through to the wrong chain.

> This is the proposal for configurable orchestration. Its job is to define the target flow clearly enough that implementation and validation cannot drift.

---

## 1) What flow this proposal is defining

The current document must do two things clearly:

1. define the intended orchestration flow in plain language
2. define the config and validation model that enforces that flow

The intended target flow is:

```text
plan -> dev -> analysis
             ├── back to dev
             └── forward to either plan or review

review -> fix -> analysis
              ├── back to fix
              └── forward to either plan, review, or commit depending on remaining work and cycle policy
```

More precisely:

- **Planning** produces the work plan for the current development cycle.
- **Development** performs implementation work for that cycle.
- **Analysis after development** decides whether the development work needs another dev attempt, another planning cycle, or progression into review.
- **Review** inspects the resulting changes for the current review cycle.
- **Fix** addresses findings from review.
- **Analysis after fix** decides whether the fix needs another fix attempt, another planning/development cycle, another review pass, or progression toward commit.
- **Commit** is only reached when the preceding analysis decision says the current work is ready to be finalized.

That explicit decision structure is the core of this proposal. The rest of the document exists to make that flow enforceable.

### Important note about current implementation vs target flow

The current codebase does **not** model this cleanly yet.

Repo evidence shows that today:

- `analysis` is often treated as a drain/sub-phase rather than a first-class phase decision point
- planning re-entry is mostly tied to iteration boundaries and recovery resets
- the review/fix loop is implemented, but its transition logic is spread across reducers and continuation rules instead of being declared as one clear orchestration contract

This proposal therefore defines the **target orchestration model** that implementation should converge on, while using the current codebase only as evidence for where the existing behavior is muddy or wrong.

## 2) Why this proposal exists now

The original version assumed the pipeline still revolved around legacy structured-output files and schema-bound extraction. That is no longer true.

The current product direction is MCP-first:

- agents submit typed artifacts through the MCP bridge
- prompts already instruct planning to submit JSON artifacts rather than write legacy output files
- the runtime has MCP session drains, drain classes, policy modes, and capability gating
- yet the orchestration layer still contains compatibility-era fallback behavior and drain normalization that can silently choose the wrong chain

That mismatch is what produced the recent regressions.

### The concrete failures we now need to design against

Recent MCP work surfaced two classes of orchestration bugs:

1. **Wrong drain, wrong agent, wrong session policy**
   - planning can inherit or normalize into the wrong chain
   - drain identity and agent role are still partially decoupled
   - permissive fallback behavior hides bad configuration instead of rejecting it

2. **Legacy output-path assumptions still influence control flow**
   - completion detection and retry cleanup still rely on legacy output file paths
   - boundary modules still keep compatibility branches instead of enforcing artifact-only behavior
   - docs and planning language still describe the old flow rather than the actual MCP flow

This proposal updates the design so orchestration fixes both problems together.

---

## 3) Root causes discovered in the current codebase

The revised proposal is based on the current implementation, not on the pre-migration design.

### A. Drain resolution is too permissive

`UnifiedConfig::resolve_agent_drains_checked()` currently derives missing drain bindings from sibling drains and legacy-style chain names.

That behavior was meant to be convenient, but after the MCP migration it became dangerous:

- a missing planning binding can silently inherit from development
- a missing fix binding can silently inherit from review
- analysis and commit can be auto-derived even when the user never intended those mappings
- bad TOML can still produce a runnable pipeline rather than a validation error

This is the opposite of what we want for orchestration safety.

### B. Drain identity, role identity, and session identity are not strict enough

The runtime now has three distinct concepts:

- **drain**: planning, development, analysis, review, fix, commit
- **role**: which kind of agent prompt/behavior we want
- **session policy**: MCP access mode, policy mode, and drain class

Today those concepts are still partially collapsed together:

- planning and development share the same broad role family in places that should remain distinct
- review and fix likewise share routing assumptions that are too broad
- normalization logic repairs mismatches at runtime instead of rejecting impossible states earlier

That makes the system tolerant of corrupted or incomplete state when it should be precise.

### C. MCP is primary, but the runtime still behaves like compatibility mode

The current code already contains MCP-native pieces:

- MCP endpoint leasing and session bridging
- drain-class to access-mode mapping
- artifact submission prompts
- JSON artifact ingestion

But several orchestration decisions still assume the presence of legacy extraction, validation, rendering, and completion-file paths. That means the pipeline is neither fully legacy nor fully MCP-native, which is the riskiest possible state.

---

## 4) Goals

This proposal now has **two equally important goals**.

### Goal 1: Fix orchestration correctness bugs

The orchestration layer must stop selecting the wrong chain, wrong session policy, or wrong fallback path.

Success means:

- planning never routes through fix-specific behavior
- fix never inherits read-only planning behavior
- analysis and commit only inherit configuration when that inheritance is explicit and validated
- retry, continuation, and resume logic preserve the intended drain identity
- agent selection is a pure function of normalized config + current phase/drain + worker context

### Goal 2: Make TOML the enforcement layer, not just a settings file

The TOML contract must catch bad orchestration states before a run starts.

Success means:

- incomplete drain definitions are rejected early
- invalid drain transitions are rejected early
- incompatible session policies are rejected early
- artifact expectations are validated against phase definitions
- configuration cannot silently degrade into a different drain than the one the user intended

---

## 5) Non-goals

This proposal does **not** attempt to:

- introduce a scripting language for orchestration
- make reducers or effect handlers dynamically programmable
- allow arbitrary runtime-defined phases without Rust types
- preserve compatibility with the legacy output pipeline indefinitely

The new design is intentionally stricter than the old one.

---

## 6) Design principles

1. **MCP artifacts are the only supported structured-output path.**
   No runtime decision should depend on legacy output files, compatibility extractors, or compatibility cleanup.

2. **Every built-in drain must be explicit.**
   Hidden drain inheritance is no longer acceptable. If inheritance exists, it must be declared in TOML and validated.

3. **Phase and drain identity are first-class contracts.**
   Phase, drain, role, policy mode, drain class, artifact type, and worker context must agree.

4. **Reject invalid orchestration up front.**
   Startup validation is cheaper than debugging a run that invoked the wrong agent.

5. **TOML is the orchestration source of truth; Rust is the enforcement engine.**
   TOML declares what the workflow should do, including phase flow, decision points, fan-out, merge behavior, and prompt contracts. Rust validates that declaration and refuses impossible combinations.

6. **Policy must live outside `ralph-workflow`.**
   The end state is a standalone `ralph-workflow-policy` crate that owns orchestration policy types, TOML schemas, prompt contracts, prompt texts, template assets, templating logic, and validation rules without depending on `ralph-workflow` at all.

7. **Compatibility fallbacks must be temporary and measurable.**
   Any remaining migration path must be explicit, isolated, and removable.

8. **Parallel execution must be explicitly scoped.**
   If orchestration launches parallel workers, each worker must declare its namespace, allowed directory scope, artifact scope, and completion contract up front.


---

## 7) Proposed config split

The original three-file split still makes sense, but the ownership boundary changes.

### Standalone crate boundary

The end goal is to move orchestration policy into a dedicated crate:

```text
ralph-workflow-policy/
  ├── src/
  │   ├── policy types
  │   ├── TOML schema + loaders
  │   ├── validation rules
  │   ├── prompt contract types
  │   ├── prompt rendering / templating
  │   └── normalized orchestration model
  └── prompt texts / template assets

ralph-workflow/
  └── consumes normalized policy + rendered prompt interfaces from ralph-workflow-policy
```

Hard rule:

- `ralph-workflow-policy` must have **no dependency** on `ralph-workflow`
- `ralph-workflow` may depend on `ralph-workflow-policy`
- prompt definitions, prompt texts, template assets, templating logic, TOML schemas, normalized flow contracts, and validation logic belong to `ralph-workflow-policy`
- reducer/runtime execution belongs to `ralph-workflow`
- `ralph-workflow` may consume prompt outputs from the policy crate, but it must not own the canonical template source

That separation prevents circular dependency, prevents runtime code from quietly redefining policy, and makes orchestration behavior testable without booting the whole workflow engine.

At the user/project level, the policy crate still materializes the same contract shape:

```text
.agent/
  ├── agents.toml          WHO   — chain definitions and drain bindings
  ├── pipeline.toml        WHEN  — phase order, retry policy, transitions, invariants
  └── artifacts.toml       WHAT  — prompt templates, artifact contracts, validation rules
```

### `agents.toml`: explicit drain contracts

Instead of treating drain bindings as a loose map from drain name to chain name, make each drain a typed contract.

Example:

```toml
[agent_chains]
planner = ["claude"]
developer = ["opencode/minimax/m2-5"]
reviewer = ["codex"]
fixer = ["opencode/openai/gpt-5.2"]
committer = ["claude"]

[agent_drains.planning]
chain = "planner"
role = "planning"
policy_mode = "read_only"
drain_class = "planning"
artifact_type = "plan"

[agent_drains.development]
chain = "developer"
role = "development"
policy_mode = "dev"
drain_class = "dev"
artifact_type = "development_result"

[agent_drains.analysis]
chain = "reviewer"
role = "analysis"
policy_mode = "read_only"
drain_class = "review"
artifact_type = "analysis_report"

[agent_drains.review]
chain = "reviewer"
role = "review"
policy_mode = "read_only"
drain_class = "review"
artifact_type = "issues"

[agent_drains.fix]
chain = "fixer"
role = "fix"
policy_mode = "fixer"
drain_class = "fixer"
artifact_type = "fix_result"

[agent_drains.commit]
chain = "committer"
role = "commit"
policy_mode = "commit"
drain_class = "commit"
artifact_type = "commit_message"
```

The important change is that TOML no longer says only “planning uses chain X.” It says what planning **is**.

### `pipeline.toml`: phase transitions and orchestration rules

This file holds the actual flow contract, including the analysis decision points that were missing from the previous draft.

This file holds:

- phase order
- allowed transition graph
- retry strategy
- continuation strategy
- recovery policy
- phase-level artifact expectations
- whether a phase is read-only or write-capable
- parallel worker definitions and namespace restrictions

Example:

```toml
[phases]
sequence = ["planning", "development", "analysis", "review", "fix", "commit"]
internal_states = ["awaiting_dev_fix", "final_validation", "finalization"]

[phase_drain_map]
planning = "planning"
development = "development"
analysis = "analysis"
review = "review"
fix = "fix"
commit = "commit"

[transitions]
planning = ["development"]
development = ["analysis", "awaiting_dev_fix"]
analysis = ["development", "planning", "review", "fix", "commit", "awaiting_dev_fix"]
review = ["fix", "analysis", "commit", "awaiting_dev_fix"]
fix = ["analysis", "awaiting_dev_fix"]
commit = ["final_validation", "awaiting_dev_fix"]

[cycle_policy]
max_development_cycles = 5
max_review_cycles = 2
max_dev_continuations = 3
max_fix_continuations = 10
loop_detection_threshold = 100

[orchestration]
same_drain_resume_only = true
require_explicit_drain_bindings = true
forbid_sibling_drain_inference = true
analysis_controls_phase_progression = true
planning_reentry_allowed_from_analysis = true
review_reentry_allowed_from_analysis = true

[parallel_workers.plan_shards]
enabled = true
parent_drain = "planning"
worker_role = "planning"
max_workers = 4
namespace_mode = "required"
artifact_namespace = "plan_shards"
directory_scope = ["src/", "tests/", "docs/"]
merge_strategy = "planning_synthesis"
```

### `artifacts.toml`: prompt and artifact contracts

This file replaces the old prompt/schema framing with artifact-only rules.

Example:

```toml
[planning]
prompt_template = "planning.txt"
artifact_type = "plan"
required_sections = ["summary", "steps", "critical_files", "verification_strategy"]
submission_mode = "mcp_artifact"

[development]
prompt_template = "developer_iteration.txt"
continuation_template = "developer_iteration_continuation.txt"
artifact_type = "development_result"
submission_mode = "mcp_artifact"

[review]
prompt_template = "review.txt"
artifact_type = "issues"
submission_mode = "mcp_artifact"

[fix]
prompt_template = "fix_mode.txt"
artifact_type = "fix_result"
submission_mode = "mcp_artifact"

[commit]
prompt_template = "commit_message.txt"
artifact_type = "commit_message"
submission_mode = "mcp_artifact"
```

This keeps the existing separation of concerns while aligning all three files with the MCP runtime.

The intended end state is that a reviewer can read the TOML files and understand the orchestration flow without reverse-engineering reducer code. Rust should implement and validate that contract, not quietly invent missing flow on its own.

More importantly, the crate that defines those TOML contracts, prompt contracts, prompt texts, and templating behavior must be the standalone `ralph-workflow-policy` crate, not `ralph-workflow` itself.

### Canonical target flow

The flow contract should be readable without reverse-engineering reducers:

```text
planning -> development -> analysis

analysis after development decides one of:
- development   (needs more implementation work in the same cycle)
- planning      (the plan itself must be revised before more implementation)
- review        (development work for this cycle is ready for review)

review -> fix -> analysis

analysis after fix decides one of:
- fix           (the attempted fix is incomplete or incorrect)
- planning      (review/fix findings imply the current plan is wrong or incomplete)
- review        (run another review pass on the updated result)
- commit        (review/fix work is accepted and ready to finalize)
```

This is the behavior the orchestration config must encode and the reducers must eventually implement directly.

### Multi-agent and parallel worker contracts

Parallel execution must be declared explicitly rather than being improvised at runtime.

There are two related features here:

1. **Scoped parallel workers** — workers limited by namespace, directory scope, and merge strategy.
2. **Multi-agent phase execution** — a phase may declare that multiple agents should run concurrently, but only after a dedicated multi-agent planner produces precise assignments for each worker.

Each worker definition should specify:

- the parent drain that is allowed to launch it
- the worker role it uses
- the agent pool it may draw from
- the worker count policy
- whether namespacing is required
- the directory scope the worker may touch, if any
- the artifact namespace it may submit into
- the merge strategy that rejoins worker output into the parent drain
- the planner prompt/template that produces per-agent assignments

Example:

```toml
[multi_agent.planning]
enabled = true
default_agents = 5
max_agents = 20
planner_prompt_template = "multi_agent_planning.txt"
assignment_artifact_type = "multi_agent_plan"
merge_strategy = "planning_synthesis"

[multi_agent.planning.agent_pool]
allowed_agents = ["claude", "codex", "opencode/minimax/m2-5", "opencode/openai/gpt-5.2"]

[parallel_workers.plan_shards]
parent_drain = "planning"
worker_role = "planning"
worker_source = "multi_agent.planning"
namespace_mode = "required"
namespace_prefix = "plan_worker"
artifact_namespace = "plan_shards"
directory_scope = ["src/", "tests/", "docs/"]
```

Example fix batch worker:

```toml
[parallel_workers.fix_batches]
enabled = true
parent_drain = "fix"
worker_role = "fix"
max_workers = 3
namespace_mode = "required"
namespace_prefix = "fix_batch"
directory_scope = ["src/features/payments/", "tests/payments/"]
artifact_namespace = "fix_batches"
merge_strategy = "fix_synthesis"
```

This gives Ralph enough information to validate that a worker restricted to `src/features/payments/` cannot accidentally claim authority over the whole repository, and that a planner cannot request more concurrent workers than policy allows.

Namespacing is required to be more than a label. The namespace contract must include:

- a unique worker identity key such as `(run_id, phase_instance, parent_drain, worker_id)`
- a declared artifact namespace that cannot collide with parent-drain artifacts
- canonicalized directory scopes with overlap detection after path normalization
- a merge target that is compatible with the worker artifact type and parent drain
- a per-agent assignment generated by the multi-agent planner so each concurrent agent gets precise instructions rather than a shared vague prompt

### Multi-agent planner requirement

If a phase configures more than one concurrent agent, Ralph must first invoke a **multi-agent planner** for that phase.

That planner is a new feature with a new prompt contract. Its responsibilities are:

- inspect the parent phase goal and current artifact context
- decide whether parallel fan-out is appropriate
- assign each worker a precise objective, scope, namespace, and artifact target
- prevent duplicated effort across workers
- define how worker outputs are merged back into the parent phase

This is important because “run 5 agents” is not a plan. The orchestration contract must force a planning step that turns a phase-level goal into 5 concrete worker assignments.

The initial limits should be:

- **default concurrent agents:** 5
- **current hard maximum:** 20

Those limits should live in TOML and be validated.

---

## 8) Validation changes that catch bugs earlier

This is the most important addition to the proposal.

### Validation rule set

At startup, Ralph should reject config when any of the following are true:

1. a built-in drain is missing
2. a drain references an unknown chain
3. a drain uses a role not allowed for that drain
4. a drain uses a policy mode inconsistent with its drain class
5. a read-only drain is configured with a write-capable session policy
6. a write-capable drain is configured without the capabilities its phase requires
7. a phase expects one artifact type while the bound drain declares another
8. a transition graph allows an impossible phase jump
9. retry or continuation policy would switch to a different drain without an explicit transition
10. any config relies on implicit sibling-drain inference
11. phase, drain, and internal-state identifiers are not mapped canonically
12. `analysis` is configured but cannot act as an explicit decision point between development/review/fix/planning/commit
13. the configured transition graph does not allow the target flow `planning -> development -> analysis` and `review -> fix -> analysis`
14. cycle policy and transition policy disagree about whether analysis may send work back to planning or review
15. a parallel worker is enabled for a drain that is not allowed to launch parallel work
16. a phase requests more than the configured default/current maximum concurrent agents
17. multi-agent fan-out is enabled but no multi-agent planner prompt/artifact contract is defined
18. a parallel worker lacks a required namespace while using directory restrictions
19. two parallel worker definitions can write to overlapping canonical directory scopes without an explicit merge strategy
20. a parallel worker can submit artifacts outside its declared namespace
21. the policy crate depends on `ralph-workflow` or imports runtime-only orchestration logic
22. prompt texts, template assets, or templating logic remain owned by `ralph-workflow` instead of `ralph-workflow-policy`
23. a config contains unknown keys, alias identifiers, or mixed naming forms that normalize to the same canonical identifier
24. multiple concurrent workers can receive overlapping assignment scopes from the multi-agent planner without an explicit override policy

### Explicit inheritance, if we keep any inheritance at all

If convenience inheritance remains, it must be declarative and visible.

Example:

```toml
[agent_drains.analysis]
inherits_chain_from = "review"
role = "analysis"
policy_mode = "read_only"
drain_class = "review"
artifact_type = "analysis_report"
```

That is acceptable because it is reviewable and validation can explain it.

What is no longer acceptable is hidden runtime behavior like:

- “analysis inherits from development if present”
- “commit inherits from review or fix automatically”
- “planning falls back to development because they are both roughly developer work”

### Generated validation matrix

The config layer should also generate a deterministic validation matrix that can be unit-tested.

For each drain, emit a normalized record such as:

```text
planning  -> chain=planner   role=planning   policy=read_only   class=planning   artifact=plan
development -> chain=developer role=development policy=dev       class=dev        artifact=development_result
fix       -> chain=fixer     role=fix        policy=fixer       class=fixer      artifact=fix_result
```

Tests can snapshot this matrix and fail loudly when a drain begins resolving to an unexpected chain or policy.

The normalized output should also include canonical phase ownership and worker-launch permissions so reviewers can see, in one place, whether a drain is resumable, whether it can spawn parallel workers, which artifact namespace it owns, and whether it requires a multi-agent planner before fan-out.

---

## 9) Runtime changes required by this proposal

### Phase 1: Make drain contracts explicit and strict

**Goal:** remove silent drain inference and validate every built-in drain explicitly.

**Key changes:**

- replace loose `agent_drains: HashMap<String, String>` semantics with typed drain config records
- forbid sibling-drain inference by default
- allow only explicit inheritance when declared in TOML
- validate drain → role → policy_mode → drain_class consistency at load time

**Primary files:**

- `ralph-workflow-policy/src/...`
- `ralph-workflow/src/config/unified/types.rs`
- `ralph-workflow/src/config/validation/mod.rs`
- `ralph-workflow/src/config/unified/io_tests/`
- `ralph-workflow/src/agents/fallback.rs`

**Risk:** low to medium

**Why first:** this is the single highest-leverage fix for “planning routes to fixer” style bugs.

---

### Phase 2: Make analysis a first-class orchestration decision point

**Goal:** stop treating analysis as an implicit side effect and make it the explicit control point that decides whether work loops back or moves forward.

**Key changes:**

- model analysis as an explicit orchestration phase/drain with typed decision outcomes
- allow analysis-after-development to route to development, planning, or review
- allow analysis-after-fix to route to fix, planning, review, or commit
- ensure session drain identity survives normalization, retries, continuations, and resume
- make normalization reject impossible combinations instead of silently repairing them

**Primary files:**

- `ralph-workflow/src/agents/session/mod.rs`
- `ralph-workflow/src/mcp_server/capability_mapping.rs`
- `ralph-workflow/src/reducer/boundary/agent.rs`
- `ralph-workflow/src/reducer/boundary/planning.rs`
- `ralph-workflow/src/reducer/boundary/run_fix.rs`

**Risk:** medium

**Why second:** the biggest missing concept in the current proposal was the actual decision flow. Analysis has to become the explicit branch point before anything else will stay understandable.

---

### Phase 3: Separate drain identity from broad role families

**Goal:** stop treating planning/development and review/fix as interchangeable because they sit in the same broad role bucket.

**Key changes:**

- add explicit role variants for planning and fix where needed
- ensure session drain identity survives normalization, retries, continuations, and resume
- make normalization reject impossible combinations instead of silently repairing them

**Primary files:**

- `ralph-workflow/src/agents/session/mod.rs`
- `ralph-workflow/src/mcp_server/capability_mapping.rs`
- `ralph-workflow/src/reducer/boundary/agent.rs`
- `ralph-workflow/src/reducer/boundary/planning.rs`
- `ralph-workflow/src/reducer/boundary/run_fix.rs`

**Risk:** medium

**Why third:** once config is strict and analysis is explicit, runtime identity must match it exactly.

---

### Phase 4: Add namespaced parallel worker orchestration and multi-agent planning

**Goal:** support scoped parallel execution while keeping TOML as the authority on worker count, assignment strategy, and merge behavior.

**Key changes:**

- add typed multi-agent phase definitions with `default_agents = 5` and `max_agents = 20`
- require a dedicated multi-agent planner prompt/template before any multi-agent fan-out occurs
- store the planner output as a typed artifact that assigns each worker a precise task, namespace, and scope
- require namespaced parallel worker definitions for any directory-restricted worker
- validate that worker counts, namespaces, assignment scopes, and merge strategies are coherent
- freeze a fully normalized worker contract at startup so runtime cannot reinterpret namespace, chain, prompt, or path scope later

**Primary files:**

- `ralph-workflow-policy/src/...`
- `ralph-workflow-policy/prompt assets ...`
- `ralph-workflow/src/config/unified/types.rs`
- `ralph-workflow/src/config/validation/mod.rs`
- `ralph-workflow/src/reducer/orchestration/`
- `ralph-workflow/src/reducer/boundary/planning.rs`

**Risk:** medium

**Why fourth:** once analysis flow is explicit, the next biggest source of ambiguity is uncontrolled parallelism. Multi-agent work must be planned before it is executed.

---

### Phase 5: Move orchestration to artifact-only completion and retry handling

**Goal:** remove the remaining legacy completion-path assumptions from runtime control flow.

**Key changes:**

- replace completion detection based on legacy temp-file paths with artifact submission state
- remove compatibility cleanup logic that targets legacy output files
- make retries and continuations depend on artifact presence and reducer state only
- make boundary modules artifact-only instead of dual-mode

**Primary files:**

- `ralph-workflow/src/reducer/boundary/agent.rs`
- `ralph-workflow/src/reducer/boundary/json_artifact.rs`
- `ralph-workflow/src/reducer/boundary/planning.rs`
- `ralph-workflow/src/reducer/boundary/run_fix.rs`
- `ralph-workflow/src/files/llm_output_extraction/`

**Risk:** medium to high

**Why fifth:** strict drain routing still is not enough if runtime completion logic can drift into legacy behavior.

---

### Phase 6: Replace compatibility-era prompt and artifact wiring

**Goal:** ensure every phase prompt and artifact contract describes MCP submission only, and that prompt ownership lives entirely in `ralph-workflow-policy`.

**Key changes:**

- rename or replace legacy-named templates in the policy crate's prompt assets
- move prompt texts and templating logic out of `ralph-workflow` into `ralph-workflow-policy`
- route prompts through `artifacts.toml` instead of compatibility assumptions
- validate that each phase template declares an artifact submission requirement
- remove legacy structured-output terminology from docs and template guidance

**Primary files:**

- `ralph-workflow-policy/src/...`
- `ralph-workflow-policy/prompt assets ...`
- `ralph-workflow/src/reducer/boundary/json_artifact.rs`
- `docs/template-guide.md`
- `docs/architecture/agents-and-prompts.md`

**Risk:** medium

**Why sixth:** by this point runtime behavior is strict enough that prompt and doc cleanup can align to the same contract.

---

### Phase 7: Rule-table orchestration with invariant checks

**Goal:** keep cross-cutting orchestration logic explicit and testable.

The earlier proposal already argued for a rule table. That remains correct, but the updated rule table must operate on drain contracts and artifact state rather than compatibility-era assumptions.

**Key additions:**

- every rule declares which drains it may fire in
- every rule declares whether it preserves drain identity or performs a validated transition
- every rule has a focused invariant test
- continuation and retry rules cannot cross drains unless the transition graph allows it
- rule evaluation must consume only normalized startup config, not runtime alias repair or family-level fallback lookup

**Primary files:**

- `ralph-workflow/src/reducer/orchestration/`
- `ralph-workflow/src/reducer/state_reduction/`
- `ralph-workflow/src/reducer/state/agent_chain/`

**Risk:** medium

---

## 10) What should be removed outright

The updated design is not just additive. It should delete compatibility layers.

### Remove from runtime

- legacy output-file completion checks
- compatibility extraction/validation/rendering paths under `src/files/llm_output_extraction/` that exist only for the old flow
- retry cleanup aimed at legacy temp-file artifacts
- dual-mode boundary logic that says “artifact first, legacy fallback second”

### Remove from docs and plans

- legacy structured-output terminology in this proposal
- compatibility-language in architecture docs and template docs once runtime cleanup lands
- any plan text that still frames the migration as unfinished when MCP artifacts are the product direction

---

## 11) End-state architecture

```text
ralph-workflow-policy crate
  ├── agents.toml schema      explicit drain contracts
  ├── pipeline.toml schema    transitions + retry/continuation policy
  ├── artifacts.toml schema   prompt and artifact requirements
  ├── prompt contract types
  ├── prompt texts / template assets
  ├── prompt rendering / templating
  └── normalized policy model

Validated startup state
  ├── every built-in drain bound explicitly
  ├── every drain has a valid role/policy/class combination
  ├── every phase expects a declared artifact type
  ├── analysis is declared as an explicit decision point
  ├── every parallel worker has a namespace + directory scope contract
  └── no hidden drain inference remains

Runtime orchestration
  ├── planning -> development -> analysis
  ├── analysis decides loop-back vs forward progression
  ├── review -> fix -> analysis
  ├── parallel workers run only inside declared namespaces
  └── retries/continuations preserve or validate drain identity
```

What changes after this plan ships:

| Problem today | End-state behavior |
|---|---|
| Missing drain bindings silently inherit from siblings | Missing drain bindings fail validation immediately |
| Planning can drift into the wrong chain | Planning has an explicit contract and invariant tests |
| Analysis is implicit and hard to reason about | Analysis is a first-class decision point with explicit outgoing transitions |
| Fix and review are loosely coupled | Fix is a separate drain contract with separate policy validation |
| Completion depends on legacy temp files | Completion depends on artifact submission state |
| Prompt/template naming still reflects the old flow | Prompt and artifact contracts reflect MCP-only behavior |
| Parallel workers can overreach when spawned ad hoc | Parallel workers are declared, namespaced, and scope-validated |
| Multi-agent fan-out is vague and untestable | Multi-agent fan-out requires a planner artifact with precise per-agent assignments |
| Config changes can accidentally alter orchestration | Config changes are normalized and rejected if unsafe |

---

## 12) Success criteria

This proposal succeeds when all of the following are true:

- every built-in drain is declared explicitly or through explicit TOML inheritance
- startup validation rejects partial or ambiguous drain definitions
- planning, development, analysis, review, fix, and commit each have distinct validated contracts
- the target flow `planning -> development -> analysis` and `review -> fix -> analysis` is declared explicitly in config and docs
- analysis can explicitly decide whether work returns to development, planning, review, fix, or commit based on context
- drain normalization never changes a run into a different drain silently
- retry and continuation logic cannot cross drains without a declared transition
- runtime completion no longer depends on legacy temp-file paths
- prompts and artifact routing are MCP-only
- parallel execution is available only through declared worker contracts
- multi-agent fan-out is available only when TOML enables it and provides a multi-agent planner contract
- namespaced workers cannot submit artifacts or modify files outside their declared scope
- each concurrent agent receives a precise planner-generated assignment rather than a shared generic prompt
- orchestration TOML, prompt contracts, prompt texts, template assets, templating logic, and normalized policy types live in `ralph-workflow-policy`, not `ralph-workflow`
- `ralph-workflow-policy` has no dependency on `ralph-workflow`
- legacy structured-output references are removed from runtime code and from this proposal
- regression tests exist for the concrete failure mode where planning previously selected the wrong chain
- regression tests exist for stale or wrongly tagged artifacts being rejected for the current run/attempt/drain/namespace
- regression tests exist for analysis sending work to the wrong next phase
- regression tests exist for parallel workers escaping their namespace or directory scope
- regression tests exist proving that enabling multi-agent execution in TOML changes orchestration behavior only through the multi-agent planner path
- regression tests exist for per-agent assignment generation so workers do not receive overlapping or vague instructions
- a config author can understand exactly why a drain resolved the way it did from validation output alone

---

## 13) Recommended implementation order

1. **Strict drain contracts and validation**
2. **Explicit analysis decision flow**
3. **Drain/role/session identity separation**
4. **Standalone `ralph-workflow-policy` crate extraction**
5. **Namespaced parallel worker contracts and multi-agent planner**
6. **Artifact-only completion and retry logic**
7. **Prompt/artifact contract cleanup**
8. **Rule-table hardening and invariant coverage**

That order fixes the buggiest behavior first, then removes the compatibility paths that allowed the bugs to hide.

## 14) Additional invariants that must be explicit in implementation planning

Before implementation begins, the follow-up execution plan should preserve these non-negotiable invariants:

- **Canonical identifiers only.** `development` and `dev`, or `fix` and `fixer`, must not be treated as interchangeable names in user config.
- **Single source of authority per concept.** If `pipeline.toml` owns whether a phase is read-only or parallel-capable, `agents.toml` must reference that fact rather than redefining it.
- **Artifact acceptance is stricter than artifact presence.** A phase completes only when Ralph accepts an artifact matching the current run, attempt, drain, artifact type, and namespace.
- **Analysis owns branch decisions.** Development and fix phases do work; analysis decides whether that work loops back, requires replanning, or moves forward.
- **Multi-agent execution requires preplanned assignments.** If more than one concurrent agent is launched, the multi-agent planner must have emitted distinct worker assignments first.
- **Policy is standalone.** `ralph-workflow-policy` may not depend on `ralph-workflow`, import reducer code, or own runtime-only orchestration behavior.
- **Prompts are policy assets.** Prompt texts, template files, and templating behavior belong to `ralph-workflow-policy`, not `ralph-workflow`.
- **Config normalization is one-time, startup-only.** Runtime logic may consume normalized config, but may not silently repair invalid config after validation.
- **Inheritance is field-scoped, not semantic.** Inheriting a chain must not imply inherited role, policy mode, drain class, namespace, or session reuse.
