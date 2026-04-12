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

plan -> parallel planner -> [ralph orchestrator invokes multi dev agents] -> analysis
                                     └── back to planning (if an agent fails; parallel planner is an implementation detail, not a replanning agent)

review -> fix -> analysis
              ├── back to fix
              └── forward to either plan, review, or commit depending on remaining work and cycle policy
```

More precisely:

- **Planning** produces the work plan for the current development cycle. Planning can produce either a sequential plan (single work unit) or a parallel plan (multiple `work_units[]` declared). A parallel plan routes through the **parallel planner** — an implementation detail that generates the per-agent work unit specs (unit_id, description, edit_area, allowed_directories, dependencies) — and then the Ralph orchestrator takes those specs and invokes the configured number of development agents concurrently. If a concurrent agent fails, routing goes back to the **normal planner** for reassessment, not to the parallel planner. The orchestrator interprets the planning artifact's structure — not a config flag — to determine whether parallel invocation is warranted.
- **Development** performs implementation work for that cycle, either sequentially (single agent) or concurrently (multiple agents invoked by the Ralph orchestrator after the parallel planner generates per-agent assignments).
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

The current resolution logic uses a **three-tier fallback chain** for any drain not explicitly bound in TOML:
1. **Drain-specific chain name** — looks for a chain whose name exactly matches the drain (e.g., a chain named `"planning"` for the planning drain).
2. **Sibling drain** — copies the binding from the sibling drain if it has already been resolved. The hardcoded sibling relationships are: `planning` ↔ `development`, `review` ↔ `fix`, `commit` ← `review` or `fix`, `analysis` ← `development` or `planning`.
3. **Legacy role-family chain name** — looks for a chain named after the legacy role family: `"developer"` backs `planning`, `development`, and `analysis`; `"reviewer"` backs `review`, `fix`, and `commit`.

The third tier is the most dangerous. A config that defines only a chain named `"developer"` will silently resolve all three of `planning`, `development`, and `analysis` to that same chain — which is the exact class of wrong-chain routing bug this proposal targets. The `forbid_sibling_drain_inference = true` flag in the proposed `[orchestration]` config block must disable tiers 2 and 3.

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

The two concrete collapses are:

1. **Planning and Analysis collapse into the Developer role.** Both `planning` and `analysis` drains map to the same broad role as `development` in the current model. This means a session started for planning can be identity-equivalent to a development session — despite planning being read-only and development being write-capable. Normalization cannot distinguish them by role alone.
2. **Fix collapses into the Analysis role.** The `fix` drain maps to the same broad role as `analysis` even though fix is write-capable and analysis is read-only. A session identity check that relies on role cannot tell "agent doing fix work" from "agent doing analysis work." Phase 3 must add explicit role variants for planning and fix to break these collapses.

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

### Goal 3: Eliminate the XSD/XML compatibility layer entirely

The codebase currently contains a substantial legacy XML extraction and validation infrastructure (`ralph-workflow/src/files/llm_output_extraction/`) that pre-dates MCP. Every phase boundary still touches this infrastructure for completion detection, retry handling, output validation, and prompt rendering.

This proposal targets full removal of that compatibility layer. Success means:

- no phase completion decision depends on XSD schema validation
- no artifact accepted by the orchestrator is an XML file parsed through the legacy extractor
- the XSD schema files (`.xsd`) and XML validation/extraction modules exist only as migration scaffolding and are deleted once artifact-only paths are confirmed working
- no runtime codepath says "try artifact first, fall back to XML" — the fallback is gone

This is not optional cleanup. The dual-mode infrastructure is what allows phases to behave correctly in some configurations while silently regressing in others. Removing it closes the class of bugs where an orchestration fix in the artifact path is undermined by a surviving legacy path.

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

3. **System invariants are enforced by the runtime; users only configure chains.**
   The built-in drain semantics (which drain is read-only, which is write-capable, which maps to which policy mode, which artifact type it produces) are hardcoded system invariants. They are enforced by the runtime, not configured by users. Users only decide *which agent chain* backs each drain. The `require_explicit_drain_bindings` flag ensures users always make that choice explicitly; the system enforces everything else.

4. **Reject invalid orchestration up front.**
   Startup validation is cheaper than debugging a run that invoked the wrong agent.

5. **TOML documents and validates policy; reducers still own runtime decisions.**
   TOML should make orchestration contracts visible and testable, but reducer/orchestrator code remains the authority for runtime phase transitions, retry ordering, continuation semantics, recovery escalation, and termination behavior. Rust may consume normalized policy, but it must not delegate core state-machine ownership to user-overridable config.

6. **Policy must live outside `ralph-workflow`.**
   The end state is a standalone `ralph-workflow-policy` crate that owns orchestration policy types, TOML schemas, prompt contracts, prompt texts, template assets, templating logic, and validation rules without depending on `ralph-workflow` at all.

7. **Compatibility fallbacks must be temporary and measurable.**
   Any remaining migration path must be explicit, isolated, and removable.

8. **Parallel execution must be explicitly scoped.**
   If orchestration launches parallel workers, each worker must declare its namespace, allowed directory scope, artifact scope, and completion contract up front.

9. **Parallel execution is driven by the planning artifact, not by config flags.**
   If the planning artifact declares `work_units[]` (parallel plan elements), the orchestrator invokes multiple development agents concurrently. If the artifact is sequential, the orchestrator invokes a single agent. Users do not enable or disable parallelism via a config flag — the plan declares what the work is, and the orchestrator enacts it.


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
- TOML schemas, normalized policy types, and validation logic belong to `ralph-workflow-policy`
- reducer/runtime execution belongs to `ralph-workflow`
- prompt assets and prompt rendering should move only after their current dependencies on `PhaseContext`, capability mapping, and runtime-only prompt assembly are untangled; that extraction is a later hardening step, not a prerequisite for fixing drain resolution or orchestration correctness

That separation prevents circular dependency, keeps normalization/validation testable without booting the whole workflow engine, and avoids forcing runtime-heavy prompt assembly into a crate boundary before its dependencies are understood.

At the user/project level, the policy crate still materializes the same contract shape:

```text
.agent/
  ├── agents.toml          USER   — chain definitions and drain bindings (simple)
  ├── pipeline.toml        INTERNAL — documented phase/drain contracts, budgets, and validated invariants (system-defined; not user-overridable control flow)
  └── artifacts.toml       INTERNAL — artifact acceptance contracts and prompt/artifact metadata per drain (system-defined)
```

The `agents.toml` model is already correct and does not change: users define chains and bind them to drains. That is all a user needs to understand.

What this proposal should extract first is the **policy surface around** the reducer — explicit drain bindings, documented phase/drain relationships, continuation budgets, artifact acceptance rules, and parallel-work constraints. Reducer-owned sequencing remains in Rust until the phase model, recovery semantics, and artifact identity model are stable enough to externalize safely.

The `agents.toml` example below shows the INTERNAL system metadata per drain (role, policy_mode, drain_class, artifact_type). Users do not write these — the runtime enforces them. Users only set `chain` per drain. The example is included to make the internal contract visible, not to show user-facing config.

### `agents.toml`: explicit drain contracts

Example:

```toml
# INTERNAL SYSTEM METADATA — do not edit
# These fields are enforced by the runtime per built-in drain.
# Users only configure `chain`.

[agent_drains.planning]
chain = "planner"         # ← USER CONFIGURES THIS ONLY

[agent_drains.development]
chain = "developer"

[agent_drains.analysis]
chain = "reviewer"

[agent_drains.review]
chain = "reviewer"

[agent_drains.fix]
chain = "fixer"

[agent_drains.commit]
chain = "committer"
```

The internal metadata (role, policy_mode, drain_class, artifact_type) is shown to make the drain contract visible, but users only configure `chain` per drain. The system enforces that planning is read-only, fix is write-capable, analysis produces a decision artifact, and so on — users cannot misconfigure these invariants, only override which chain backs them.

### `pipeline.toml`: phase transitions and orchestration rules

This file should start as a **validated contract snapshot**, not as a new runtime source of truth. The runtime already knows the real sequencing today and enforces it in reducer/orchestration code. The purpose of `pipeline.toml` is to make that contract visible, testable, and internally normalized without pretending that users can safely override phase transitions, recovery ordering, or termination behavior before those semantics are disentangled from the reducer.

This file holds:

- top-level reducer-visible phases
- embedded drain-owned decision points inside those phases
- continuation / retry / loop-detection budgets
- artifact acceptance expectations
- documented parallel-dispatch constraints derived from planning artifacts
- invariants that config validation must enforce before startup succeeds

Example:

```toml
[top_level_phases]
sequence = ["planning", "development", "review", "commit_message", "final_validation", "finalizing"]
recovery_phase = "awaiting_dev_fix"

[embedded_decision_points]
development = ["analysis"]
review = ["fix", "analysis"]

[decision_routes]
development_analysis = ["development", "planning", "review"]
fix_analysis = ["fix", "planning", "review", "commit_message"]

[budgets]
max_development_cycles = 5
max_review_cycles = 2
max_dev_continuations = 3
max_fix_continuations = 10
loop_detection_threshold = 100

[artifact_acceptance]
require_current_run_identity = true
require_current_drain_identity = true
require_current_namespace_when_present = true

[validation]
require_explicit_drain_bindings = true
forbid_sibling_drain_inference = true
preserve_reducer_priority_order = true

[parallel_execution]
source = "planning_artifact_work_units"
require_namespaces = true
require_directory_scopes = true
dispatch_remains_runtime_owned = true
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

[analysis]
prompt_template = "analysis.txt"
artifact_type = "analysis_decision"
required_decision_outcomes = ["needs_more_work", "needs_replanning", "ready_for_review", "ready_to_commit", "needs_another_review"]
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

This is the behavior the documented policy contract must describe clearly while reducers continue to implement the actual runtime sequencing.

### Phase model: explicit embedded decision points first

The current codebase does **not** model `analysis` or `fix` as top-level `PipelinePhase` variants. They are embedded, drain-owned steps inside `Development` and `Review`, with separate drain/capability behavior but shared top-level phase routing. That means the safe near-term plan is:

- keep `analysis` explicit as a **decision point** inside `Development` and `Review`
- keep `fix` explicit as a **write-capable subflow** inside `Review`
- make analysis outcomes typed and reducer-visible without first splitting the global phase enum

This preserves the current lifecycle invariants around `CommitMessage`, `FinalValidation`, `Finalizing`, and `AwaitingDevFix`, while still fixing the real bug source: ambiguous drain identity and hidden fallback behavior.

A future RFC may still decide to promote `analysis` and/or `fix` into first-class top-level phases, but that should happen only after commit/finalization/recovery semantics are redesigned with the full state machine in view. It is too invasive to treat as a prerequisite for this plan.

### Multi-agent and parallel worker contracts

Parallel execution must be explicit and scope-checked, but the current architecture already has the right ownership split: the **planning artifact** describes work units, and Rust validates and dispatches them deterministically.

That means the plan should not introduce a separate top-level `parallel_planner` state machine node. Instead:

1. the planning artifact remains the place where parallelizable `work_units[]` are declared
2. policy/config declares the constraints under which those work units may fan out
3. Rust validates namespace, directory scope, merge compatibility, and worker count before dispatch

Each worker contract should still specify:

- the parent drain that is allowed to launch it
- the worker role it uses
- the agent pool it may draw from
- the worker count policy
- whether namespacing is required
- the directory scope the worker may touch, if any
- the artifact namespace it may submit into
- the merge strategy that rejoins worker output into the parent drain

But those contracts should be applied to planner-produced work units, not to a second planner layer that duplicates existing planning semantics.

Namespacing is required to be more than a label. The namespace contract must include:

- a unique worker identity key such as `(run_id, phase_instance, parent_drain, worker_id)`
- a declared artifact namespace that cannot collide with parent-drain artifacts
- canonicalized directory scopes with overlap detection after path normalization
- a merge target that is compatible with the worker artifact type and parent drain
- a per-agent assignment generated by the planning artifact so each concurrent agent gets precise instructions rather than a shared vague prompt

### Multi-agent planner requirement

If a phase uses more than one concurrent agent, the **planning artifact itself** must already contain distinct worker assignments or work units precise enough for Rust to validate and dispatch.

That means the requirement is not “add a second planner stage,” but rather:

- the planning phase must emit precise assignment data when it requests fan-out
- Rust must reject vague or overlapping work units at validation time
- merge behavior must remain deterministic and runtime-owned

This is important because “run 5 agents” is not a plan. But adding another planner node would duplicate responsibilities the planning artifact already owns.

The initial limits should be:

- **default concurrent agents:** 5
- **current hard maximum:** 20

Those limits should live in validated policy/config and be enforced by Rust before any fan-out is dispatched.

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
8. the documented policy contract contradicts the reducer-owned phase graph or termination lifecycle
9. retry or continuation policy would switch to a different drain or phase in a way the reducer does not support explicitly
10. any config relies on implicit sibling-drain inference
11. phase, drain, and internal-state identifiers are not mapped canonically
12. the `analysis` drain's declared artifact type does not define the decision outcomes the runtime needs in order to validate embedded routing
13. the documented decision routes do not allow the target flow `planning -> development -> analysis` and `review -> fix -> analysis`
14. cycle policy and decision-route policy disagree about whether analysis may send work back to planning or review
15. a parallel worker contract is declared for a drain that is not allowed to launch parallel work
16. a phase requests more than the configured default/current maximum concurrent agents
17. multi-agent fan-out is enabled but the planning artifact contract cannot produce precise, non-overlapping worker assignments
18. a parallel worker lacks a required namespace while using directory restrictions
19. two parallel worker definitions can write to overlapping canonical directory scopes without an explicit merge strategy
20. a parallel worker can submit artifacts outside its declared namespace
21. the policy crate depends on `ralph-workflow` or imports runtime-only orchestration logic
22. prompt texts, template assets, or templating logic remain owned by `ralph-workflow` instead of `ralph-workflow-policy`
23. a config contains unknown keys, alias identifiers, or mixed naming forms that normalize to the same canonical identifier
24. multiple concurrent workers can receive overlapping assignment scopes from the planning artifact without an explicit override policy

### Explicit bindings first, explicit inheritance only if absolutely necessary

The safest near-term model is still: **every built-in drain gets an explicit chain binding**. That directly removes the current sibling/legacy fallback ambiguity.

If convenience inheritance survives at all, it must be declarative, field-scoped, and visible in normalized output. What is no longer acceptable is hidden runtime behavior like:

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

The normalized output should also include canonical phase ownership and worker-launch permissions so reviewers can see, in one place, whether a drain is resumable, whether it can spawn parallel workers, which artifact namespace it owns, and which planning-artifact constraints must be satisfied before fan-out.

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

**Note:** `ralph-workflow-policy` does not exist yet — it is created in Phase 4. All config type changes in Phases 1–3 land in `ralph-workflow/src/config/`. Phase 4 migrates them into the new crate.

- `ralph-workflow/src/config/unified/types.rs`
- `ralph-workflow/src/config/validation/mod.rs`
- `ralph-workflow/src/config/unified/io_tests/`
- `ralph-workflow/src/agents/fallback.rs`

**Risk:** low to medium

**Why first:** this is the single highest-leverage fix for “planning routes to fixer” style bugs.

---

### Phase 2: Make analysis outcomes explicit within the existing phase model

**Goal:** stop treating analysis as an opaque side effect and make it the explicit decision point that decides whether work loops back or moves forward, without first rewriting the top-level `PipelinePhase` enum.

**Key changes:**

- keep analysis embedded inside `Development` and `Review`, but make its decision outcomes typed and reducer-visible
- allow analysis-after-development to route to development, planning, or review
- allow analysis-after-fix to route to fix, planning, review, or commit message
- ensure session drain identity survives normalization, retries, continuations, and resume
- make normalization reject impossible combinations instead of silently repairing them
- define the typed analysis decision taxonomy with named outcomes: `needs_more_work` (loop back to current phase), `needs_replanning` (route to planning), `ready_for_review` (route to review, post-development), `ready_to_commit` (route to commit message, post-fix), `needs_another_review` (route back to review, post-fix) — these names are the canonical vocabulary; they must appear in `artifacts.toml` and in the validation rules

**Primary files:**

- `ralph-workflow/src/agents/session/mod.rs`
- `ralph-workflow/src/mcp_server/capability_mapping.rs`
- `ralph-workflow/src/reducer/boundary/agent.rs`
- `ralph-workflow/src/reducer/boundary/planning.rs`
- `ralph-workflow/src/reducer/boundary/run_fix.rs`

**Risk:** medium

**Why second:** the biggest missing concept in the current proposal was the actual decision flow. Analysis has to become the explicit branch point before anything else will stay understandable, but it does not need to become a top-level phase yet.

---

### Phase 3: Remove lossy drain-to-role compatibility mappings

**Goal:** stop treating distinct drains as interchangeable because compatibility adapters erase drain identity too early.

**Key changes:**

- remove or narrow lossy drain-to-role compatibility mappings where session/runtime code currently collapses distinct drains
- keep access mode, policy mode, and drain class keyed on drain identity
- ensure session drain identity survives normalization, retries, continuations, and resume
- make normalization reject impossible combinations instead of silently repairing them

**Primary files:**

- `ralph-workflow/src/agents/session/mod.rs`
- `ralph-workflow/src/mcp_server/capability_mapping.rs`
- `ralph-workflow/src/reducer/boundary/agent.rs`
- `ralph-workflow/src/reducer/boundary/planning.rs`
- `ralph-workflow/src/reducer/boundary/run_fix.rs`

**Risk:** medium

**Why third:** once config is strict and analysis outcomes are explicit, runtime identity must match them exactly or retries/continuations will still drift into the wrong drain.

---

### Phase 4: Extract `ralph-workflow-policy` as a standalone crate

**Goal:** move orchestration policy types, TOML schemas, and validation rules out of `ralph-workflow` into a new `ralph-workflow-policy` crate with no dependency on `ralph-workflow`, while deferring prompt rendering/assets that still depend on runtime context.

**Key changes:**

- create the `ralph-workflow-policy` crate with no dependency on `ralph-workflow`
- migrate config types, TOML schema definitions, and validation rules from `ralph-workflow/src/config/` into `ralph-workflow-policy/src/`
- update `ralph-workflow` to consume policy types and rendered prompt interfaces from `ralph-workflow-policy`
- verify the dependency direction: `ralph-workflow` depends on `ralph-workflow-policy`, never the reverse
- explicitly defer prompt assets / rendering code that still depends on `PhaseContext`, capability gates, or runtime-only prompt assembly until a later extraction step

**Primary files:**

- `ralph-workflow-policy/` (new crate — all files)
- `ralph-workflow/src/config/` (files that migrate out)
- `ralph-workflow/Cargo.toml` (add dependency on policy crate)
- `Cargo.toml` workspace (add new crate member)

**Risk:** medium — touches the workspace structure and import boundaries

**Why here:** Phases 1–3 establish correct semantics within `ralph-workflow`. Phase 4 then extracts the policy surface that is already stable enough to stand alone.

---

### Phase 5: Add namespaced parallel worker orchestration around planning-artifact work units

**Goal:** Parallel execution is first-class and driven by the planning artifact's work-unit structure, with Rust validating and dispatching those work units deterministically.

**Key changes:**

- the orchestrator interprets `work_units[]` in the planning artifact
- if parallel work is declared, the planning artifact must already contain per-agent work unit specs (unit_id, description, edit_area, allowed_directories, dependencies)
- the Ralph orchestrator validates those specs, then invokes the configured number of development agents concurrently
- no separate `parallel_planner` phase or config flag is needed — the plan artifact is the control plane, and Rust remains the validator/dispatcher
- worker namespace and directory scope contracts still apply to concurrent invocations
- if a concurrent agent fails, routing goes back to the normal planning/reassessment path, not to a second planner layer

**Primary files:**

- `ralph-workflow/src/config/unified/types.rs`
- `ralph-workflow/src/config/validation/mod.rs`
- `ralph-workflow/src/reducer/orchestration/`
- `ralph-workflow/src/reducer/boundary/planning.rs`

**Risk:** medium

**Why fifth:** once analysis flow is explicit, the next biggest source of ambiguity is uncontrolled parallelism. Multi-agent work must be validated before it is executed, but that validation belongs in Rust, not in an extra planner state machine.

---

### Phase 6: Add reducer-visible artifact acceptance identity

**Goal:** make artifact acceptance stricter than artifact presence before deleting any legacy fallback path.

**Key changes:**

- add reducer-visible artifact identity checks for run, attempt, drain, artifact type, and namespace where applicable
- reject stale or wrongly tagged artifacts even if a file exists
- make completion and retry logic depend on accepted artifact identity rather than raw file presence
- preserve current reducer-owned retry/continuation ordering while strengthening acceptance semantics

**Primary files:**

- `ralph-workflow/src/reducer/state/`
- `ralph-workflow/src/reducer/boundary/json_artifact.rs`
- `ralph-workflow/src/workspace.rs`
- `ralph-workflow/src/reducer/orchestration/`

**Risk:** medium

**Why sixth:** deleting XML fallback before artifact identity is strong enough would just replace one class of ambiguity with another.

---

### Phase 7: Move orchestration to artifact-only completion and retry handling

**Goal:** remove the remaining legacy completion-path assumptions from runtime control flow after artifact acceptance is strong enough to stand on its own.

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

**Why seventh:** strict drain routing still is not enough if runtime completion logic can drift into legacy behavior.

---

### Phase 8: Replace compatibility-era prompt and artifact wiring

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

**Why eighth:** by this point runtime behavior is strict enough that prompt and doc cleanup can align to the same contract.

---

### Phase 9: Rule-table orchestration with invariant checks

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
- the entire `ralph-workflow/src/files/llm_output_extraction/` compatibility module: XSD schema files, XML extraction and validation logic, XML rendering, and all legacy output-file parsers — this infrastructure exists solely for the pre-MCP flow and must be deleted, not left dormant
- all dual-mode boundary code that dispatches to XML extraction as a fallback after checking for a JSON artifact
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
  ├── pipeline.toml schema    documented phase/drain contracts + validated budgets
  ├── artifacts.toml schema   prompt and artifact requirements
  ├── prompt contract types
  └── normalized policy model

Validated startup state
  ├── every built-in drain bound explicitly
  ├── every drain has a valid role/policy/class combination
  ├── every phase expects a declared artifact type
  ├── analysis is declared as an explicit embedded decision point
  ├── every parallel worker has a namespace + directory scope contract
  └── no hidden drain inference remains

Runtime orchestration
  ├── planning -> development (with analysis decision step)
  ├── review (with fix + analysis decision steps) -> commit_message
  ├── final_validation -> finalizing -> complete
  ├── awaiting_dev_fix preserves recovery semantics
  └── retries/continuations preserve or validate drain identity
```

What changes after this plan ships:

| Problem today | End-state behavior |
|---|---|
| Missing drain bindings silently inherit from siblings | Missing drain bindings fail validation immediately |
| Planning can drift into the wrong chain | Planning has an explicit contract and invariant tests |
| Analysis is implicit and hard to reason about | Analysis is an explicit embedded decision point with typed outcomes |
| Fix and review are loosely coupled | Fix keeps its own drain contract and policy validation without requiring a new top-level phase |
| Completion depends on legacy temp files | Completion depends on artifact submission state |
| Prompt/template naming still reflects the old flow | Prompt and artifact contracts reflect MCP-only behavior |
| Parallel workers can overreach when spawned ad hoc | Parallel workers are namespaced, scope-validated, and dispatched only from validated work units |
| Multi-agent fan-out is vague and untestable | Multi-agent fan-out requires precise planning-artifact assignments before Rust dispatches workers |
| Config changes can accidentally alter orchestration | Config changes are normalized and rejected if unsafe |
| XSD/XML compatibility layer still active in all phase boundaries | XSD/XML infrastructure deleted; all completion and retry logic reads artifact state only |
| Parallel execution requires guesswork or ad hoc worker spawning | Parallelism is driven by the planning artifact's `work_units[]` structure; no extra planner phase is introduced |

---

## 12) Success criteria

This proposal succeeds when all of the following are true:

- every built-in drain is declared explicitly or through explicit TOML inheritance
- startup validation rejects partial or ambiguous drain definitions
- planning, development, analysis, review, fix, and commit each have distinct validated drain contracts even when analysis/fix remain embedded in top-level phases
- the target flow `planning -> development -> analysis` and `review -> fix -> analysis` is declared explicitly in documented policy and docs
- analysis can explicitly decide whether work returns to development, planning, review, fix, or commit based on context
- drain normalization never changes a run into a different drain silently
- retry and continuation logic cannot cross drains without a declared transition
- runtime completion no longer depends on legacy temp-file paths
- prompts and artifact routing are MCP-only
- parallel execution is triggered by the planning artifact's `work_units[]` declaration; the orchestrator validates this structure and invokes the correct number of concurrent development agents without introducing a second planner phase
- multi-agent fan-out is available only when validated policy allows it and the planning artifact provides precise worker assignments
- namespaced workers cannot submit artifacts or modify files outside their declared scope
- each concurrent agent receives a precise planning-artifact assignment rather than a shared generic prompt
- orchestration TOML, prompt contracts, prompt texts, template assets, templating logic, and normalized policy types live in `ralph-workflow-policy`, not `ralph-workflow`
- `ralph-workflow-policy` has no dependency on `ralph-workflow`
- legacy structured-output references are removed from runtime code and from this proposal
- regression tests exist for the concrete failure mode where planning previously selected the wrong chain
- regression tests exist for stale or wrongly tagged artifacts being rejected for the current run/attempt/drain/namespace
- regression tests exist for analysis sending work to the wrong next phase
- regression tests exist for parallel workers escaping their namespace or directory scope
- regression tests exist proving that enabling multi-agent execution changes orchestration behavior only through validated planning-artifact work units
- regression tests exist for per-agent assignment generation so workers do not receive overlapping or vague instructions
- a config author can understand exactly why a drain resolved the way it did from validation output alone
- analysis produces typed, named decision outcomes (`needs_more_work`, `needs_replanning`, `ready_for_review`, `ready_to_commit`, `needs_another_review`) that are declared in `artifacts.toml`, validated against documented decision routes at startup, and are the only mechanism by which embedded analysis steps advance or loop work
- the `ralph-workflow/src/files/llm_output_extraction/` XSD schema files and XML extraction/validation modules are deleted; no phase boundary references them
- no runtime codepath contains a dual-mode branch of the form "check artifact, fall back to XML" — the XML fallback is gone

---

## 13) Recommended implementation order

1. **Strict drain contracts and validation**
2. **Explicit analysis decision flow (including typed decision taxonomy)**
3. **Drain/role/session identity separation**
4. **Standalone `ralph-workflow-policy` crate extraction**
5. **Namespaced parallel worker contracts around planning-artifact work units**
6. **Reducer-visible artifact acceptance identity**
7. **Artifact-only completion and retry logic**
8. **Prompt/artifact contract cleanup**
9. **Rule-table hardening and invariant coverage**

That order fixes the buggiest behavior first, then removes the compatibility paths that allowed the bugs to hide.

## 14) Additional invariants that must be explicit in implementation planning

Before implementation begins, the follow-up execution plan should preserve these non-negotiable invariants:

- **Canonical identifiers only.** `development` and `dev`, or `fix` and `fixer`, must not be treated as interchangeable names in user config.
- **Single source of authority per concept.** If documented policy records whether a drain is parallel-capable, the reducer must still remain the authority on runtime sequencing and termination semantics.
- **Artifact acceptance is stricter than artifact presence.** A phase completes only when Ralph accepts an artifact matching the current run, attempt, drain, artifact type, and namespace.
- **Analysis owns branch decisions.** Development and fix phases do work; analysis decides whether that work loops back, requires replanning, or moves forward.
- **Multi-agent execution requires preplanned assignments.** If more than one concurrent agent is launched, the planning artifact must have emitted distinct worker assignments first.
- **Policy is standalone.** `ralph-workflow-policy` may not depend on `ralph-workflow`, import reducer code, or own runtime-only orchestration behavior.
- **Prompts are policy assets.** Prompt texts, template files, and templating behavior belong to `ralph-workflow-policy`, not `ralph-workflow`.
- **Config normalization is one-time, startup-only.** Runtime logic may consume normalized config, but may not silently repair invalid config after validation.
- **Inheritance is field-scoped, not semantic.** Inheriting a chain must not imply inherited role, policy mode, drain class, namespace, or session reuse.
