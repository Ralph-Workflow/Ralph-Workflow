# Configurable Orchestration Layer (Move Orchestration Policy from Rust into Config)

**Date:** 2026-04-09
**Status:** Proposed
**Scope:** Define the intended orchestration flow explicitly and move orchestration policy decisions out of implicit Rust control-flow code and into validated, visible configuration files. Rust should keep execution mechanics; configuration should express the orchestration policy. Replace the remaining legacy structured-output assumptions with an MCP-artifact-only orchestration contract, and make drain routing, phase wiring, artifact contracts, analysis decision routes, parallel-work policy, and orchestration invariants legible in TOML so bad agent selection fails fast instead of silently falling through to the wrong chain.

> This proposal is about making orchestration visible and configurable: logic that is currently buried across Rust reducers, drain resolution, and continuation rules should be expressed as validated configuration wherever it represents policy. Rust should remain responsible for execution mechanics, deterministic dispatch, and invariant enforcement — not for being the only place where orchestration decisions live. Its job is to define that target clearly enough that implementation and validation cannot drift.

---

## 1) What flow this proposal is defining

Before any detail, the main point of this document should be obvious:

- **today, too much orchestration policy is hidden in Rust code paths and fallback logic**
- **the target state is for orchestration policy to be visible in configuration files**
- **Rust should execute and validate that policy, not remain the only place where orchestration decisions are encoded**

In other words, this proposal is not merely about adding more settings. It is about taking orchestration behavior that is currently implicit in Rust and making it explicit, inspectable, and validated in configuration.

The current document must do two things clearly:

1. define the intended orchestration flow in plain language
2. define the config and validation model that enforces that flow

The intended target flow is:

```text
planning -> development -> development_analysis
                 ├── back to development
                 └── on successful cycle completion: development_commit
                                                    ├── if development cycles remain: planning
                                                    └── if development budget is exhausted: review

planning (with parallel work_units[]) -> [ralph orchestrator invokes multiple development agents] -> development_analysis
                                                             ├── back to development according to the declared loopback policy
                                                             └── on successful cycle completion: development_commit
                                                                                                ├── if development cycles remain: planning
                                                                                                └── if development budget is exhausted: review

review -> fix -> review_analysis
              ├── back to fix
              └── on successful cycle completion: review_commit
                                                 ├── if review cycles remain: review
                                                 └── if review budget is exhausted: complete
```

More precisely:

- **Planning** produces the work plan for the current development cycle. Planning can produce either a sequential plan (single work unit) or a parallel plan (multiple `work_units[]` declared). There is no separate top-level `parallel_planner` phase in the target model: the planning artifact itself carries the work-unit structure, and the Ralph orchestrator takes those declared assignments and invokes the configured number of development agents concurrently. If a concurrent agent fails, routing goes back to normal planning/reassessment according to the declared policy. The orchestrator interprets the planning artifact's structure — not a config flag — to determine whether parallel invocation is warranted.
- **Development** performs implementation work for that cycle, either sequentially (single agent) or concurrently (multiple agents invoked by the Ralph orchestrator from the work-unit assignments declared in the planning artifact).
- **DevelopmentAnalysis** decides whether the development work needs another dev attempt, or whether the current development iteration completed successfully and must pass through **DevelopmentCommit** before the development counter advances and guarded post-commit routing sends the workflow either back to planning or forward to review.
- **Review** inspects the resulting changes for the current review cycle.
- **Fix** addresses findings from review.
- **ReviewAnalysis** decides whether the fix needs another fix attempt, or whether the current review/fix iteration completed successfully and must pass through **ReviewCommit** before the review counter advances and guarded post-commit routing sends the workflow either into another review pass or to completion.
- **DevelopmentCommit** is the mandatory checkpoint after every successful completed development iteration.
- **ReviewCommit** is the mandatory checkpoint after every successful completed review iteration.
- **Complete** is reached only after the relevant guarded post-commit rule says no further planning/review cycle is required.
- recovery/error-handling paths are runtime-owned Ralph mechanics, not part of the policy-visible happy-path contract.

That explicit decision structure is the core of this proposal. The rest of the document exists to make that flow enforceable.

### Important note about current implementation vs target flow

The current codebase does **not** model this cleanly yet.

Repo evidence shows that today:

- `analysis` is often treated as a drain/sub-phase rather than as distinct `development_analysis` and `review_analysis` decision points
- planning re-entry is mostly tied to iteration boundaries and recovery resets
- the review/fix loop is implemented, but its transition logic is spread across reducers and continuation rules instead of being declared as one clear orchestration contract

This proposal therefore defines the **target orchestration model** that implementation should converge on, while using the current codebase only as evidence for where the existing behavior is muddy or wrong.

## 2) Why this proposal exists now

Right now, a reader still has to inspect Rust code to understand too much of the orchestration contract. Drain routing, fallback behavior, phase wiring, continuation budgets, and artifact expectations are split across reducers, normalization logic, and compatibility branches. That makes orchestration hard to reason about, hard to review, and too easy to misconfigure without noticing.

This proposal exists to change that. The intended end state is:

- **policy is visible in config files**
- **policy decisions are configured there, rather than being implicit in Rust branching logic**
- **validation rejects impossible or ambiguous orchestration up front**
- **Rust remains responsible for execution mechanics and invariant enforcement, but not for hiding the effective orchestration policy**

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

The top-level goal is simple: **move orchestration policy decisions out of scattered Rust implementation details and into validated configuration files, while keeping Rust responsible for execution mechanics.**

More concretely, this proposal now has **three equally important goals**.

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

This is the configurability goal: orchestration should be understandable by reading the config surface, not by reverse-engineering Rust reducers and fallback helpers.

It must also be a **typed policy contract** rather than a best-effort bag of strings. Where the contract is user-editable (for example `agents.toml`), Ralph should typecheck it against the policy schema and fail with precise, field-level error messages that explain what is wrong, where it is wrong, and what valid shape/value is expected. Where the contract is internal and shipped by the policy layer (for example `pipeline.toml` and `artifacts.toml`), those defaults must still validate against the same schema and remain truthful to the orchestration behavior the system enforces.

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

3. **System invariants are enforced by the runtime; configuration expresses policy.**
   Configuration should express the orchestration policy surface: which chain backs each drain, which decision routes are legal, which budgets apply, which artifact contracts exist, and under what policy conditions parallel fan-out is allowed. The runtime still enforces non-negotiable invariants such as safety checks, drain capability correctness, deterministic dispatch, and artifact identity validation.

4. **Reject invalid orchestration up front.**
   Startup validation is cheaper than debugging a run that invoked the wrong agent.

   Rejection must be user-friendly, not just strict. Invalid TOML should produce actionable diagnostics tied to the exact file/section/key that failed schema or policy validation.

5. **TOML is the visible policy contract; Rust executes it mechanically.**
   TOML should make orchestration contracts visible and testable so the effective orchestration logic is not hidden in Rust. Decision policy — routes, allowed loopbacks, budgets, artifact expectations, drain bindings, and parallel-work constraints — should live in config. Rust should execute, validate, and enforce that policy deterministically rather than quietly inventing or repairing policy at runtime.

6. **Policy must live outside `ralph-workflow`.**
The end state is a standalone `ralph-workflow-policy` crate that owns orchestration policy types, TOML schemas, prompt-template mappings, prompt texts, template assets, templating logic, and validation rules without depending on `ralph-workflow` at all.

7. **Compatibility fallbacks must be temporary and measurable.**
   Any remaining migration path must be explicit, isolated, and removable.

8. **Parallel execution must be explicitly scoped.**
   If orchestration launches parallel workers, each worker must declare its namespace, allowed directory scope, artifact scope, and completion contract up front.

9. **Parallel execution is driven by the planning artifact, not by config flags.**
   If the planning artifact declares `work_units[]` (parallel plan elements), the orchestrator invokes multiple development agents concurrently. If the artifact is sequential, the orchestrator invokes a single agent. Users do not enable or disable parallelism via a config flag — the plan declares what the work is, and the orchestrator enacts it.


---

## 7) Proposed config split

This config split is the mechanism by which orchestration becomes visible. The goal is that a reader can inspect the policy files and understand the intended orchestration contract without having to reconstruct it from Rust implementation details.

The original three-file split still makes sense, but the ownership boundary changes.

### Standalone crate boundary

The end goal is to move orchestration policy into a dedicated crate:

```text
ralph-workflow-policy/
  ├── src/
  │   ├── policy types
  │   ├── TOML schema + loaders
  │   ├── validation rules
  │   ├── prompt template mapping types
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
- the end state still places prompt templates themselves under `ralph-workflow-policy` ownership, even if some runtime-heavy rendering helpers are extracted later than the initial TOML/schema move

Most importantly, this crate boundary is meant to make `ralph-workflow-policy` the **single source of truth for orchestration policy and the policy-owned prompt/template mapping that travels with it**. The prompt templates themselves are part of that policy-owned surface, not an afterthought owned elsewhere. That means a reader should be able to answer the following questions by reading policy types, prompt/template definitions, and `agents.toml`, `pipeline.toml`, and `artifacts.toml`, without reverse-engineering reducer code:

- which drain/agent role starts first
- which top-level phase comes after which
- which phases embed analysis as a decision point
- where analysis success routes next and where analysis failure/loopback routes next
- which commit checkpoints are mandatory between cycles
- when a development cycle increments and when a review cycle increments
- which artifact contracts and parallel-work constraints apply at each drain
- which prompt template and variable set is used for each drain and decision point as part of that policy surface

If any of those answers still live primarily in `ralph-workflow` reducer branching or runtime-only prompt assembly, then the migration is incomplete. `ralph-workflow` should execute the policy mechanically; it should not remain the place where the effective orchestration contract or the policy-owned prompt/template mapping is discovered.

That separation prevents circular dependency, keeps normalization/validation testable without booting the whole workflow engine, and avoids forcing runtime-heavy prompt assembly into a crate boundary before its dependencies are understood.

At the user/project level, the policy crate still materializes the same contract shape. In other words, `pipeline.toml` and `artifacts.toml` are not outside the policy crate's responsibility; they are part of the policy crate's owned contract surface, schema, and validation boundary.

Because these are INTERNAL policy documents, shipping default versions of them is a **must**, not an optional convenience. The default `pipeline.toml` and `artifacts.toml` owned by the policy crate must describe the exact orchestration behavior this proposal is trying to implement. In particular, the default `pipeline.toml` must match the Section 1 target flow rather than merely serving as placeholder schema coverage:

- the default phase/drain ordering must reflect the intended flow from Section 1
- the default embedded analysis decision points must match the intended development and review/fix loops from Section 1
- the default decision routes must express where analysis success and loopback paths go next
- the default cycle-budget semantics must agree with the Section 1 definition of when development and review cycles complete and increment
- if the shipped defaults do not match Section 1, then the policy layer is lying about the orchestration contract and the migration is incomplete

That requirement applies equally to the default prompt/template mappings referenced by those policy files: the shipped internal defaults must correspond to the same orchestration behavior they declare.

The contract shape is:

```text
.agent/
  ├── agents.toml          USER   — chain definitions and drain bindings
  ├── pipeline.toml        USER+SYSTEM — phase registry, transition graph, cycle policy, and validated orchestration invariants
  └── artifacts.toml       USER+SYSTEM — artifact acceptance contracts and prompt/artifact metadata per drain/phase
```

The `agents.toml` model is one part of the policy surface: users define chains and bind them to drains. `pipeline.toml` is the complementary orchestration surface: it defines which phases exist, which drains they use, which transitions are legal, where analysis is embedded, and how cycles advance. That means the user-facing policy is no longer limited to choosing chains for a fixed built-in graph.

What this proposal should extract first is the **policy surface around** the reducer — explicit drain bindings, explicit phase definitions, documented phase/drain relationships, decision routes, continuation budgets, artifact acceptance rules, and parallel-work constraints. Those rules are orchestration policy and should be visible in config instead of being discoverable only by reading Rust. Rust should then consume that policy and carry out the execution mechanics deterministically.

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

`pipeline.toml` is part of the `ralph-workflow-policy` contract surface. It is the concrete TOML artifact through which the policy crate declares and validates orchestration flow. It should not be thought of as a separate runtime-owned file format; it is one of the primary ways the policy crate expresses orchestration policy to the rest of the system.

Because it is the orchestration contract, `pipeline.toml` cannot be limited to a hidden built-in phase graph. The policy layer should ship a normative default phase graph, but user-defined phases must be represented by the exact same phase model, transition model, and validation rules as the shipped defaults. The distinction is origin, not type: shipped defaults and user-authored phase definitions are the same kind of policy object once loaded.

This file is the visible high-level policy contract for orchestration decisions. It should describe the allowed phase sequencing, embedded decision points, legal analysis routes, cycle budgets, and parallel-work policy in a form the runtime validates and executes. More concretely, this is where the system should declare the outer orchestration graph and the documents that participate in it: which phases exist, which drains they use, which decision points they expose, which successful outcomes must pass through `commit_message`, which post-commit destinations are allowed next, and any phase-level side effects that are part of the intended orchestration contract. The detailed route statements themselves should still live in the owning phase documents rather than being duplicated as a central string table here. Rust remains responsible for execution mechanics, but the orchestration policy itself should be readable from this policy surface instead of being recoverable only from reducer/orchestration code.

Backend-only failure handling is not the job of `pipeline.toml`. If the planning agent errors, times out, hits an internal fallback path, or triggers backend retry/recovery behavior, Ralph handles that internal execution logic directly. Policy should describe the intended orchestration path and the logical transition contract exposed by validated agent/artifact results; backend-only operational failure handling remains runtime-owned unless it is intentionally promoted into an explicit policy-level transition.

The key boundary is:
- **policy-visible errors/outcomes** are contract-level results that affect logical routing (for example an MCP result field like `status = partial` or `status = failed`, if those fields are part of the validated schema)
- **runtime-only errors** are operational failures such as network loss, auth failure, process crashes, timeouts, MCP transport errors, local I/O errors, or backend retry/fallback behavior

Only the first category belongs in policy. The second category remains Ralph-owned internal execution logic.

Stated more bluntly: `pipeline.toml` should be the **high-level orchestration contract**, not an all-knowing god document. It is where the project should be able to see the phase order, which agent/drain transitions are legal, the drains that require analysis, the legal next steps after analysis succeeds or fails, the mandatory commit checkpoints between cycles, the exact points at which development and review cycles are considered complete and incremented, and the full registered phase graph regardless of whether a phase came from shipped defaults or user-authored policy.

Lower-level policy details should be delegated to the policy document that owns them best. In particular:

- `pipeline.toml` owns the high-level lifecycle and routing graph
- `agents.toml` owns chain/agent bindings
- `artifacts.toml` owns named prompt/artifact contracts, decision vocabularies, prompt/template references, and the documented variable contract exposed to those templates
- prompt/template definitions own the actual template file content, and those template assets live in the `ralph-workflow-policy` crate rather than in `ralph-workflow`
- runtime code in `ralph-workflow` still owns execution mechanics, context assembly, and invariant enforcement
- policy may still declare phase-level side effects that Ralph/MCP must execute (for example commit-message materialization and apply-commit)

That means the phase graph is not just a closed enum baked into `ralph-workflow`. The policy crate should define a normalized phase graph model with:

- phase IDs as first-class policy objects regardless of origin
- shipped default phase definitions loaded through the same schema as user-authored phase definitions
- a validated normal transition graph between phases
- per-phase drain bindings and embedded decision points
- per-phase references to prompt-template/artifact profiles via the policy surface, with the referenced templates/variables/artifact metadata owned by `artifacts.toml`
- validation that rejects any phase or transition the runtime cannot legally execute

This file holds:

- the phase registry (all phases, regardless of origin)
- the stable outer lifecycle phases and their legal handoffs
- embedded drain-owned decision points inside those phases
- explicit commit gateways after successful development and review cycles
- explicit post-commit routing for what may happen next after a successful cycle is checkpointed
- explicit cycle-accounting rules describing when counters advance versus when work stays inside the same cycle
- continuation / retry / loop-detection budgets
- only the execution constraints and declared side effects that materially shape orchestration flow
- invariants that config validation must enforce before startup succeeds

It should not try to absorb every lower-level policy detail just because it is the top-level orchestration file. When a concern is primarily about chain binding, artifact shape, prompt content, or render-context assembly, the plan should delegate that concern to `agents.toml`, `artifacts.toml`, or prompt/template assets rather than bloating `pipeline.toml`.

For readability, `pipeline.toml` should be organized by concern rather than by incidental implementation order. A good default organization is:

1. **Phase registry** — all phases, their origins, and owning drains
2. **Lifecycle skeleton** — the default outer happy-path phases and their high-level handoff order
3. **Embedded decision points** — where analysis/fix live inside those phases
4. **Commit-gated routing** — direct loopbacks, success handoffs, and post-commit destinations
5. **Cycle accounting and budgets** — when counters advance vs stay in the same cycle
6. **Execution constraints and side effects** — artifact acceptance, parallel-execution rules, and phase-level side effects
7. **Validation toggles** — startup enforcement switches

Example (minimal Phase 1 / Section 1 core):

This example is intentionally limited to the core success-loop contract that Phase 1 needs to make explicit: planning, development, commit-gated iteration boundaries, review, and the typed routing around those steps. Terminal paths may still be documented as conditional happy-path destinations, but recovery/error-handling paths are intentionally excluded because they remain Ralph-owned runtime mechanics rather than policy-visible orchestration.

```toml
# ---------------------------------------------------------------------------
# 1. Pipeline composition
# ---------------------------------------------------------------------------
phase_documents = [
  "phases/planning.toml",
  "phases/development.toml",
  "phases/review.toml",
  "phases/development_commit.toml",
  "phases/review_commit.toml",
]

# User-authored phases use the same schema and can be added here.
# Example only: this is not part of the current shipped built-in flow.
extra_phase_documents = [
  "phases/security_review.toml",
]

# ---------------------------------------------------------------------------
# 2. Lifecycle skeleton
# ---------------------------------------------------------------------------
[top_level_phases]
default_sequence = ["planning", "development", "development_commit", "review", "review_commit"]

# ---------------------------------------------------------------------------
# 3. Cycle accounting and budgets
# ---------------------------------------------------------------------------
[cycle_accounting]
development_counter_increment_phase = "development_commit"
development_counter_increment_when = "after_development_cycle_completion"
review_counter_increment_phase = "review_commit"
review_counter_increment_when = "after_review_cycle_completion"

# Post-commit guard evaluation happens at the explicit cycle-specific commit phase.
# Development success routes into `development_commit`; review success routes into
# `review_commit`. Each commit phase therefore carries unambiguous post-commit semantics.

[budgets]
max_development_cycles = 5
max_review_cycles = 2
max_dev_continuations = 3
max_fix_continuations = 10
loop_detection_threshold = 100

# ---------------------------------------------------------------------------
# 4. Execution constraints and side effects
# ---------------------------------------------------------------------------
[artifact_acceptance]
require_current_run_identity = true
require_current_drain_identity = true
require_current_namespace_when_present = true

[parallel_execution]
source = "planning_artifact_work_units"
require_namespaces = true
require_directory_scopes = true
dispatch_remains_runtime_owned = true
max_concurrent_agents = 20
default_concurrent_agents = 5

[phase_side_effects.submit_development_commit_artifact]
artifact_type = "commit_message"
output_path = ".agent/tmp/commit_message.json"
mode = "submit_mcp_artifact"

[phase_side_effects.apply_development_commit]
artifact_type = "commit_message"
action = "apply_commit"

[phase_side_effects.submit_review_commit_artifact]
artifact_type = "commit_message"
output_path = ".agent/tmp/commit_message.json"
mode = "submit_mcp_artifact"

[phase_side_effects.apply_review_commit]
artifact_type = "commit_message"
action = "apply_commit"

# ---------------------------------------------------------------------------
# 5. Validation toggles
# ---------------------------------------------------------------------------
[validation]
require_explicit_drain_bindings = true
forbid_sibling_drain_inference = true
preserve_runtime_execution_order = true
reject_unbound_phase_documents = true
reject_unbound_phase_transitions = true
```

Example phase-definition files loaded by `pipeline.toml`:

Every executable phase needs a prompt template. Phase TOML should therefore store a **template lookup key** that points at an entry in `artifacts.toml`, while `artifacts.toml` owns the actual `prompt_template`, `continuation_template`, `template_variables`, and artifact metadata associated with that key. The referenced template files themselves are policy-owned `.txt` assets that live in the `ralph-workflow-policy` crate and contain placeholders such as `{{project_goal}}` or `{{path_contents}}`. Embedded decision points and subflows follow the same rule: the phase document stores the lookup key, and `artifacts.toml` resolves it to the real template file and variable list.

Concretely, `template_key = "planning"` means: look up `[planning]` in `artifacts.toml`, read `prompt_template = "planning.txt"`, load that `.txt` template file from the policy crate, allow only the `template_variables = [...]` declared there to be interpolated into its `{{variable}}` placeholders, and expect the artifact metadata declared in that same table. Nothing is inferred magically from the phase name beyond that direct table lookup.

If the phase also declares `artifact_profile = "planning"`, that is a separate lookup using the same entry name by convention: `template_key` selects the prompt template file and allowed variables, while `artifact_profile` selects the artifact contract the phase is expected to submit. In the shipped defaults those names will often match, but they are conceptually different lookups.

```toml
# phases/planning.toml
phase_id = "planning"
origin = "shipped_default"
drain = "planning"
template_key = "planning"
artifact_profile = "planning"
next = ["development"]

# phases/development.toml
phase_id = "development"
origin = "shipped_default"
drain = "development"
template_key = "development"
artifact_profile = "development"
embedded_decision_points = ["development_analysis"]
decision_point_template_keys = { development_analysis = "development_analysis" }

[[transitions]]
when = { decision_point = "development_analysis", status = "partial" }
next = ["development"]
increment_counter = false

[[transitions]]
when = { decision_point = "development_analysis", status = "failed" }
next = ["development"]
increment_counter = false

[[transitions]]
when = { decision_point = "development_analysis", status = "completed" }
next = ["development_commit"]
increment_counter = false
increment_counter_after_commit = "development"

[[post_commit_routes]]
when = { counter = "development", comparison = "less_than_budget" }
next = ["planning"]

[[post_commit_routes]]
when = { counter = "development", comparison = "budget_exhausted" }
next = ["review"]

# phases/review.toml
phase_id = "review"
origin = "shipped_default"
drain = "review"
template_key = "review"
artifact_profile = "review"
next = ["fix"]
subflow = ["fix", "review_analysis"]
subflow_template_keys = { fix = "fix", review_analysis = "review_analysis" }

[[transitions]]
when = { decision_point = "review_analysis", status = "partial" }
next = ["fix"]
increment_counter = false

[[transitions]]
when = { decision_point = "review_analysis", status = "failed" }
next = ["fix"]
increment_counter = false

[[transitions]]
when = { decision_point = "review_analysis", status = "completed" }
next = ["review_commit"]
increment_counter = false
increment_counter_after_commit = "review"

[[post_commit_routes]]
when = { counter = "review", comparison = "less_than_budget" }
next = ["review"]

[[post_commit_routes]]
when = { counter = "review", comparison = "budget_exhausted" }
next = ["complete"]

# phases/development_commit.toml
phase_id = "development_commit"
origin = "shipped_default"
drain = "commit"
template_key = "development_commit"
artifact_profile = "development_commit"
side_effects = ["submit_development_commit_artifact", "apply_development_commit"]
side_effect_artifact = "commit_message"
side_effect_artifact_path = ".agent/tmp/commit_message.json"

# phases/review_commit.toml
phase_id = "review_commit"
origin = "shipped_default"
drain = "commit"
template_key = "review_commit"
artifact_profile = "review_commit"
side_effects = ["submit_review_commit_artifact", "apply_review_commit"]
side_effect_artifact = "commit_message"
side_effect_artifact_path = ".agent/tmp/commit_message.json"

# Optional user-authored example only — not part of the current shipped flow.
# phases/security_review.toml
phase_id = "security_review"
origin = "user_defined"
drain = "review"
template_key = "security_review"
artifact_profile = "security_review"
next = ["fix"]
subflow = ["fix", "review_analysis"]
subflow_template_keys = { fix = "fix", review_analysis = "review_analysis" }

[[transitions]]
when = { decision_point = "review_analysis", status = "partial" }
next = ["fix"]
increment_counter = false

[[transitions]]
when = { decision_point = "review_analysis", status = "completed" }
next = ["review_commit"]
increment_counter = false
increment_counter_after_commit = "review"

[[post_commit_routes]]
when = { counter = "review", comparison = "less_than_budget" }
next = ["review"]

[[post_commit_routes]]
when = { counter = "review", comparison = "budget_exhausted" }
next = ["complete"]
```

A merely linear phase sequence is not expressive enough for the Section 1 requirements. The policy contract must also declare the cycle-specific commit gateways, the guarded post-commit routes, and the cycle-accounting rules explicitly, otherwise the shipped `pipeline.toml` will still underdescribe the orchestration behavior Ralph is supposed to enforce. Those guarded transition statements should live in the owning phase documents, not in a detached global table of hardcoded route labels. Optional user-authored phases like `security_review` may follow the same schema, but they should not appear in the built-in route examples as if they were part of the current shipped flow.

The shipped/core happy-path model should stop at `complete` rather than inventing extra built-in terminal phases that do not exist in the current flow. Recovery/error-handling paths remain runtime-owned and should not be treated as policy-visible pipeline phases.

The cycle budgets count **completed cycles**, not internal retries or loopbacks. This is part of the policy contract, not an implementation detail hidden in Rust:

- one **development cycle** is `planning -> development -> development_analysis`, followed by the mandatory `development_commit` checkpoint where the development counter advances and a guarded post-commit rule routes either to `planning` (if development budget remains) or to `review` (if development budget is exhausted)
- one **review cycle** is `review -> fix -> review_analysis`, followed by the mandatory `review_commit` checkpoint where the review counter advances and a guarded post-commit rule routes either to `review` (if review budget remains) or to `complete` (if review budget is exhausted)
- `development_analysis -> development` stays inside the current development cycle until a successful post-commit transition completes that cycle
- `review_analysis -> fix` stays inside the current review cycle until a successful post-commit transition completes that cycle
- the development/review counters advance only after the successful post-commit transition, matching the current pipeline lifecycle docs

### `artifacts.toml`: prompt and artifact contracts

Like `pipeline.toml`, `artifacts.toml` is part of the `ralph-workflow-policy` contract surface rather than a separate runtime concern. The policy crate owns the schema, validation rules, and meaning of this file. The same ownership rule applies to the prompt templates referenced by this contract: the template files themselves are policy-owned assets shipped by `ralph-workflow-policy`, not runtime-owned files in `ralph-workflow`.

Since it is also an INTERNAL policy document, the default `artifacts.toml` shipped by the policy layer is likewise normative and must stay aligned with the Section 1 orchestration behavior and the prompt/template contracts paired with that behavior.

This file is not merely metadata about accepted artifacts. It is part of the policy-owned configuration that travels with orchestration: it defines the named template/artifact entries referenced by phases and decision points, so the same policy layer that declares routing also declares what each agent is prompted with and what each agent is expected to submit.

It should also be the place where the documented template-variable contract lives. Phase TOML does not own raw prompt text; instead, each executable phase (and any embedded decision point or subflow that prompts an agent) should reference a lookup key such as `template_key = "development"`, `decision_point_template_keys = { development_analysis = "development_analysis" }`, or `subflow_template_keys = { fix = "fix", review_analysis = "review_analysis" }`. `artifacts.toml` then owns which concrete `prompt_template` that key resolves to, which `template_variables` it may reference, which artifact type it must submit, and any continuation-template or routing vocabulary attached to that same entry. The actual template file content for that entry lives in the `ralph-workflow-policy` crate as a `.txt` template containing `{{variable}}` placeholders. That gives reviewers a stable contract surface without forcing prompt contents themselves into phase config.

The intended rendering model should stay aligned with Ralph's existing text-template system rather than inventing a new syntax in the policy docs. Concretely, prompt assets are plain text template files that use double-curly placeholders like `{{PROMPT}}`, partial includes like `{{> shared/_partial_name}}`, and comment blocks like `{# ... #}`. Existing shipped templates also use simple control tags such as `{% if HAS_GIT_WRITE %}...{% endif %}` where needed. In other words, this is not Ruby ERB (`<%= ... %>`); it is Ralph's current double-curly text-template style, moved under `ralph-workflow-policy` ownership.

That also clarifies where template variables are defined. There are two complementary layers:

- the policy-visible allow-list lives in `artifacts.toml` via `template_variables = [...]`; this is the contract that says which variables a template is allowed to reference
- the template file itself may still self-document those same variables in header comments near the top of the `.txt` asset for human readers, but that header is documentation, not the canonical policy contract

The shipping model should be equally explicit: the policy crate ships (1) the phase/pipeline/artifact TOML defaults, (2) the prompt template files themselves, and (3) any shared partial templates they include. Runtime code still assembles the actual render context values and performs interpolation, but it should do so against the policy-owned template assets and variable allow-lists rather than against hidden runtime-only prompt definitions.

The variable-mapping model should be explicit too. `template_variables = [...]` in `artifacts.toml` is the policy-visible allow-list for a given template entry, but the concrete values are still assembled by runtime into a render-context map at execution time. In other words: policy says which variable names a template may use, runtime fills those names with actual values for the current phase/session/workspace, and the template engine renders the final prompt text from that map. That is the same broad idea as other template systems, but the syntax and renderer should remain Ralph's own existing text-template engine rather than Ruby ERB.

If this is shipped cleanly, a reader should be able to answer the full chain without reading Rust source: which `.txt` template file is shipped for a phase, which shared partials it may include, which variable names are allowed in that template, and which artifact/output contract is paired with it. Rust should implement that lookup and interpolation mechanically, not hide the mapping in ad hoc rendering code.

Because these policy documents are user-configurable, the variable contract should err on the side of exposing the full set of helpful existing render variables rather than an artificially tiny subset. Users should not have to reverse-engineer template code to discover whether values like file paths, path contents, selected files, review issues, diff summaries, prior artifacts, or iteration context are available. The policy layer should document all supported template variables that the renderer can safely provide, and template validation should fail if a template references a variable outside that documented contract.

That said, policy only documents variable availability — it does not control the low-level mechanics of materializing them. If Ralph/runtime exposes a variable like `path_contents`, it may still truncate or summarize that content internally for safety, size, or token-budget reasons. That truncation behavior is a runtime concern, not a policy-level promise that templates always receive full raw file contents.

Crucially, these TOML contracts must be type-checked against the MCP artifact/result schema, not just against ad hoc local strings. If an MCP artifact exposes a typed status such as `completed`, `partial`, or `failed`, policy routing should be able to key off that typed field directly because it is a logical contract outcome. Any additional branch conditions must come from real typed MCP fields that already exist in the schema; the policy layer must not invent local pseudo-fields or pseudo-reasons and pretend they are part of the MCP contract.

This does **not** mean policy should absorb backend failures. An operational failure like an MCP timeout, network outage, auth error, parser crash, or local file-write error is not a policy-routed `partial`/`failed` outcome unless the validated MCP contract explicitly returns it as such. Otherwise it remains Ralph-owned runtime error handling.

This file replaces the old prompt/schema framing with artifact-only rules.

Example:

In this example, the table names are the lookup keys referenced from phase TOML via fields like `template_key = "planning"`, `decision_point_template_keys = { development_analysis = "development_analysis" }`, and `subflow_template_keys = { fix = "fix", review_analysis = "review_analysis" }`.

```toml
[planning]
prompt_template = "planning.txt"
template_variables = ["project_goal", "current_phase", "existing_plan", "workspace_scope", "relevant_paths", "path_contents", "available_phase_ids", "previous_artifacts"]
artifact_type = "plan"
required_sections = ["summary", "steps", "critical_files", "verification_strategy"]
submission_mode = "mcp_artifact"

[development]
prompt_template = "developer_iteration.txt"
continuation_template = "developer_iteration_continuation.txt"
template_variables = ["task", "current_plan", "allowed_directories", "previous_attempt_summary", "relevant_paths", "path_contents", "selected_files", "latest_artifact", "iteration_context"]
artifact_type = "development_result"
submission_mode = "mcp_artifact"

[development_analysis]
prompt_template = "development_analysis.txt"
template_variables = ["task", "latest_artifact", "current_phase", "iteration_context", "relevant_paths", "path_contents", "selected_files", "review_issues"]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
status_field = "status"
allowed_statuses = ["completed", "partial", "failed"]

[review_analysis]
prompt_template = "review_analysis.txt"
template_variables = ["task", "latest_artifact", "current_phase", "iteration_context", "relevant_paths", "path_contents", "selected_files", "review_issues"]
artifact_type = "analysis_decision"
submission_mode = "mcp_artifact"
status_field = "status"
allowed_statuses = ["completed", "partial", "failed"]

# Any additional routing fields referenced here must already exist in the
# MCP schema for this artifact type. The policy layer does not invent them.
# Example:
# route_on = ["status"]

[review]
prompt_template = "review.txt"
template_variables = ["diff_summary", "latest_artifact", "review_scope", "relevant_paths", "path_contents", "selected_files", "current_plan"]
artifact_type = "issues"
submission_mode = "mcp_artifact"

[fix]
prompt_template = "fix_mode.txt"
template_variables = ["review_issues", "latest_artifact", "allowed_directories", "relevant_paths", "path_contents", "selected_files", "current_plan"]
artifact_type = "fix_result"
submission_mode = "mcp_artifact"

[development_commit]
prompt_template = "development_commit_message.txt"
template_variables = ["staged_changes_summary", "selected_files", "latest_artifact", "diff_summary", "relevant_paths", "path_contents"]
artifact_type = "commit_message"
submission_mode = "mcp_artifact"
artifact_output_path = ".agent/tmp/commit_message.json"

[review_commit]
prompt_template = "commit_message.txt"
template_variables = ["staged_changes_summary", "selected_files", "latest_artifact", "diff_summary", "relevant_paths", "path_contents"]
artifact_type = "commit_message"
submission_mode = "mcp_artifact"
artifact_output_path = ".agent/tmp/commit_message.json"
```

This keeps the existing separation of concerns while aligning all three files with the MCP runtime. In particular, development and review now have separate commit phases because they represent different orchestration boundaries even though both submit the same `commit_message` artifact shape. `development_commit` and `review_commit` each submit the canonical MCP artifact to `.agent/tmp/commit_message.json`, and each may declare a follow-up `apply_commit` action that Ralph executes deterministically. The separate `.agent/commit-message.txt` file is a plumbing/CLI convenience path, not the primary pipeline artifact contract.

The important prompt boundary is the same: policy declares which template is used and which variables it may reference, the actual template file lives in the `ralph-workflow-policy` crate as a policy-owned asset, and Ralph owns only the mechanics of assembling/rendering the live context. The documented variable list should be as complete and helpful as the renderer safely allows, including path/path-content style context where that is already available, rather than forcing users into guesswork or undocumented hidden variables. When those variables are large (for example `path_contents`), Ralph may truncate or summarize them internally; policy documents availability, not full-fidelity delivery guarantees.

That is also the cleaner architectural direction: use `.agent/tmp/commit_message.json` as the canonical commit artifact for both policy and runtime orchestration, and treat plain-text commit-message files as compatibility/plumbing helpers rather than as part of the main reducer contract. This reduces reducer-specific file responsibility and keeps commit behavior aligned with the same MCP artifact path used everywhere else.

The intended end state is that a reviewer can read the TOML files and understand the orchestration flow without reverse-engineering reducer code. Rust should implement and validate that contract, not quietly invent missing flow on its own.

More importantly, the crate that defines those TOML files, prompt-template mappings, prompt texts, and templating behavior must be the standalone `ralph-workflow-policy` crate, not `ralph-workflow` itself.

### Canonical target flow

The flow contract should be readable without reverse-engineering reducers. It describes the intended orchestration path and any policy-visible phase side effects, but not every backend failure branch:

```text
planning -> development -> development_analysis

development_analysis decides by typed MCP result fields:
- if the MCP result reports additional development work is required, remain in development
- if the MCP result reports successful completion of the current development iteration, route through `development_commit`, emit the commit-message side effect, increment the development counter, then route to `planning` if development budget remains or to `review` if development budget is exhausted

review -> fix -> review_analysis

review_analysis decides by typed MCP result fields:
- if the MCP result reports the fix is still insufficient, remain in fix
- if the MCP result reports the review/fix iteration succeeded, route through `review_commit`, emit the commit-message side effect, increment the review counter, then return to `review` if review budget remains
- if the MCP result reports the review/fix iteration succeeded and the review budget is exhausted, continue from that same review-specific commit checkpoint into `complete`
```

Any successful analysis outcome that completes the current development or review iteration must route through a cycle-specific commit phase before the next cycle starts. In other words, `planning -> development -> development_analysis` is one development cycle and `review -> fix -> review_analysis` is one review cycle; loopbacks such as `development_analysis -> development` or `review_analysis -> fix` remain inside the current cycle and do not increase the cycle counter, while transitions to another planning cycle, another review pass, or completion happen only after the successful post-commit handoff. The critical point is that both the analysis decision point and the commit phase name should communicate which cycle boundary has just completed: `development_analysis` only chooses between `development` and `development_commit`, `review_analysis` only chooses between `fix` and `review_commit`, `development_commit` owns the development-to-planning/review handoff, and `review_commit` owns the review-to-review/complete handoff. Commit is special because it is not just a routing checkpoint: it also produces a policy-visible side effect contract (submitting the `commit_message` MCP artifact and optionally applying the commit action). The canonical commit side effect should stay artifact-first — `.agent/tmp/commit_message.json` first, plumbing compatibility second — so the reducer does not need to own extra plain-text commit-message file semantics beyond what is necessary for backward-compatible CLI plumbing. This is the behavior the documented policy contract must describe clearly; Rust should execute it, not hide it. Backend-only contingencies like agent crashes, transport failures, or retry/fallback internals remain runtime mechanics unless the policy explicitly models them as first-class transitions.

### Phase model: one phase model, multiple origins

The current codebase does **not** model `analysis` or `fix` as top-level `PipelinePhase` variants. They are embedded, drain-owned steps inside `Development` and `Review`, with separate drain/capability behavior but shared top-level phase routing. The policy direction in this plan is therefore:

- keep the current shipped lifecycle as the default set of phase definitions provided by the policy crate
- treat shipped phases and user-authored phases as the same kind of phase object once loaded
- keep `analysis` explicit as a **decision point** inside any phase that declares it, rather than treating that behavior as exclusive to shipped phases
- keep `fix` explicit as a **write-capable subflow** inside any phase that declares or reuses that pattern
- make analysis outcomes typed and runtime-visible without requiring the shipped defaults to remain the only valid graph
- allow users to define additional phases in separate phase-definition TOML files and include them into `pipeline.toml`, with the same schema and validation rules used for shipped defaults

This preserves the current lifecycle invariants around commit-gated iteration boundaries and completion while moving the phase graph itself into policy ownership instead of leaving it as a closed Rust-only concept. The two commit phases make those boundaries explicit instead of requiring readers to infer cycle type from hidden context. Recovery-only runtime mechanics do not become policy-visible phases just because phase registration becomes configurable.

Promoting `analysis` or `fix` into fully standalone runtime phase mechanics is still a deeper state-machine change. That is separate from whether a phase definition comes from the shipped defaults or from user-authored policy. The plan should therefore support custom phase registration now, while validating all phases through one unified phase model.

### Multi-agent and parallel worker contracts

Parallel execution is the one genuinely new execution mechanic in this plan, and it still needs a visible policy surface. The **planning artifact** describes work units, config defines the policy constraints for when and how fan-out is allowed, and Rust validates and dispatches the work deterministically.

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
8. the documented policy contract contradicts the runtime execution graph or termination lifecycle that the runtime is expected to carry out from validated policy
9. retry or continuation policy would switch to a different drain or phase in a way the runtime execution layer does not support explicitly
10. any config relies on implicit sibling-drain inference
11. phase, drain, and internal-state identifiers are not mapped canonically
12. the `analysis` drain's declared artifact type does not define the decision outcomes the runtime needs in order to validate embedded routing
13. the documented decision routes do not allow the target flow `planning -> development -> development_analysis` and `review -> fix -> review_analysis`
14. cycle policy and decision-route policy disagree about when analysis may loop directly versus when it must hand off through commit before planning or review continue
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

Rules 1–16 and 23 are pure startup/config validation rules. Rules 17–20 and 24 are split across startup and runtime enforcement: startup validation checks that the declared worker/artifact contract is coherent, while runtime validation checks that concrete planning artifacts and worker submissions actually satisfy that declared contract. Rules 21 and 22 are not startup TOML validation rules at all; they are build/CI architecture-conformance checks about crate ownership and dependency direction.

These validation failures must surface as **good configuration diagnostics**, not opaque internal errors. At minimum, each error should identify:

- which file failed (`agents.toml`, `pipeline.toml`, or `artifacts.toml`)
- the exact section/key or line/field path involved
- whether the failure is a schema/type error, an unknown-key error, or a policy/invariant error
- what Ralph expected instead
- enough context for a user to fix the config without reading Rust code

### Explicit bindings first, explicit inheritance only if absolutely necessary

The safest near-term model is still: **every built-in drain gets an explicit chain binding**. That directly removes the current sibling/legacy fallback ambiguity.

If convenience inheritance survives at all, it must be declarative, field-scoped, and visible in normalized output. What is no longer acceptable is hidden runtime behavior like:

- “analysis inherits from development if present”
- “commit inherits from review or fix automatically”
- “planning falls back to development because they are both roughly developer work”

### Generated validation matrix

The config layer should also generate a deterministic validation matrix that can be unit-tested.

That matrix is not only for tests; it should also support explainability. A user should be able to see why a drain resolved the way it did, which defaults or explicit bindings were applied, and which policy constraints are active after normalization.

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
- emit field-precise configuration errors when TOML shape, types, or policy constraints are invalid

**Primary files:**

**Note:** `ralph-workflow-policy` does not exist yet — it is created in Phase 4. All config type changes in Phases 1–3 land in `ralph-workflow/src/config/`. Phase 4 migrates them into the new crate.

- `ralph-workflow/src/config/unified/types.rs`
- `ralph-workflow/src/config/validation/mod.rs`
- `ralph-workflow/src/config/unified/io_tests/`
- `ralph-workflow/src/agents/fallback.rs`

**Risk:** low to medium

**Why first:** this is the single highest-leverage fix for “planning routes to fixer” style bugs.

---

### Phase 2: Make development/review analysis outcomes explicit within the existing phase model

**Goal:** stop treating analysis as an opaque side effect and make `development_analysis` and `review_analysis` explicit binary decision points, without first rewriting the top-level `PipelinePhase` enum.

**Key changes:**

- keep analysis embedded inside `Development` and `Review`, but name those decision points distinctly (`development_analysis` and `review_analysis`) and make their outcomes typed and runtime-visible
- allow `development_analysis` to route only to `development` or to a successful-cycle `development_commit` handoff, with post-commit rules then routing either to `planning` (if more development cycles remain) or `review` (if development budget is exhausted)
- allow `review_analysis` to route only to `fix` or to a successful-cycle `review_commit` handoff, with post-commit rules then routing either back to `review` (if review budget remains) or forward into completion (if review budget is exhausted)
- declare those routes as per-phase transition statements in the relevant phase TOML rather than as global stringly-typed route aliases
- ensure session drain identity survives normalization, retries, continuations, and resume
- make normalization reject impossible combinations instead of silently repairing them
- define analysis outputs as typed MCP result schemas with at least a top-level `status` enum (`completed`, `partial`, `failed`) if that field exists in the MCP contract, plus any additional typed fields that are genuinely part of the MCP schema and needed for branch selection. The policy layer must reference only real MCP fields, validate them against the schema, and use those validated fields in phase-local transition rules

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
- document the allowed template variables exposed to each phase/drain contract so prompt rendering has a stable policy-visible interface even before every render helper is migrated
- expose the full useful set of existing safe render variables (including path/path-content style context where supported) rather than an artificially minimal subset, and validate template references against that documented list

**Primary files:**

- `ralph-workflow-policy/` (new crate — all files)
- `ralph-workflow/src/config/` (files that migrate out)
- `ralph-workflow/Cargo.toml` (add dependency on policy crate)
- `Cargo.toml` workspace (add new crate member)

**Risk:** medium — touches the workspace structure and import boundaries

**Why here:** Phases 1–3 establish correct semantics within `ralph-workflow`. Phase 4 then extracts the policy surface that is already stable enough to stand alone.

---

### Phase 5: Add namespaced parallel worker orchestration around planning-artifact work units

**Goal:** add parallelization as the new execution mechanic while also making its policy visible in configuration, with the planning artifact declaring work units and Rust validating and dispatching them deterministically.

**Key changes:**

- the orchestrator interprets `work_units[]` in the planning artifact
- if parallel work is declared, the planning artifact must already contain per-agent work unit specs (unit_id, description, edit_area, allowed_directories, dependencies)
- config/policy declares when parallel fan-out is permitted and what constraints each worker must satisfy
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

**Why fifth:** once analysis flow is explicit, the next biggest source of ambiguity is uncontrolled parallelism. Parallel execution needs both a configuration policy surface and a Rust execution mechanism: config says when it is allowed and what constraints apply, while Rust validates and dispatches it without introducing an extra planner state machine.

---

### Phase 6: Add runtime-visible artifact acceptance identity

**Goal:** make artifact acceptance stricter than artifact presence before deleting any legacy fallback path.

**Key changes:**

- add runtime-visible artifact identity checks for run, attempt, drain, artifact type, and namespace where applicable
- reject stale or wrongly tagged artifacts even if a file exists
- make completion and retry logic depend on accepted artifact identity rather than raw file presence
- preserve current runtime retry/continuation mechanics while strengthening acceptance semantics

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
- make retries and continuations depend on accepted artifact identity, validated policy, and runtime state rather than legacy file presence
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
  ├── artifacts.toml schema   prompt, artifact, and routed-on MCP result requirements
  ├── prompt template mapping types
  └── normalized policy model

Validated startup state
  ├── every built-in drain bound explicitly
  ├── every drain has a valid role/policy/class combination
  ├── every phase expects a declared artifact type
  ├── analysis is declared as an explicit embedded decision point
  ├── every parallel worker has a namespace + directory scope contract
  └── no hidden drain inference remains

Runtime orchestration
  ├── planning -> development (with analysis decision step and commit on successful cycle completion)
├── review (with fix + analysis decision steps and review commit on successful cycle completion) -> review_commit
├── complete
  └── retries/continuations preserve or validate drain identity while operational failures remain runtime-owned
```

What changes after this plan ships:

| Problem today | End-state behavior |
|---|---|
| Missing drain bindings silently inherit from siblings | Missing drain bindings fail validation immediately |
| Planning can drift into the wrong chain | Planning has an explicit contract and invariant tests |
| Analysis is implicit and hard to reason about | `development_analysis` and `review_analysis` are explicit embedded decision points with typed outcomes |
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
- startup validation typechecks user-edited TOML against the policy schema and returns precise, actionable error messages
- planning, development, review, fix, development_analysis, review_analysis, development_commit, and review_commit each have distinct validated policy contracts even when analysis/fix remain embedded in top-level phases
- the target flow `planning -> development -> development_analysis -> development_commit` and `review -> fix -> review_analysis -> review_commit` is declared explicitly in documented policy and docs, with distinct analysis and commit checkpoints for development and review iterations
- the canonical commit side effect is the MCP artifact `.agent/tmp/commit_message.json`, while any plain-text commit-message file remains a secondary plumbing compatibility path rather than the main policy contract
- `development_analysis` can explicitly decide only between returning to `development` or routing into `development_commit`, and `review_analysis` can explicitly decide only between returning to `fix` or routing into `review_commit`
- drain normalization never changes a run into a different drain silently
- retry and continuation logic cannot cross drains without a declared transition
- runtime completion no longer depends on legacy temp-file paths
- prompts and artifact routing are MCP-only
- parallel execution is triggered by the planning artifact's `work_units[]` declaration; the orchestrator validates this structure and invokes the correct number of concurrent development agents without introducing a second planner phase
- multi-agent fan-out is available only when validated policy allows it and the planning artifact provides precise worker assignments
- namespaced workers cannot submit artifacts or modify files outside their declared scope
- each concurrent agent receives a precise planning-artifact assignment rather than a shared generic prompt
- orchestration TOML, prompt-template mappings, prompt texts, template assets, templating logic, and normalized policy types live in `ralph-workflow-policy`, not `ralph-workflow`
- `ralph-workflow-policy` has no dependency on `ralph-workflow`
- legacy structured-output references are removed from runtime code and from this proposal
- regression tests exist for the concrete failure mode where planning previously selected the wrong chain
- regression tests exist for stale or wrongly tagged artifacts being rejected for the current run/attempt/drain/namespace
- regression tests exist for analysis sending work to the wrong next phase
- regression tests exist for parallel workers escaping their namespace or directory scope
- regression tests exist proving that enabling multi-agent execution changes orchestration behavior only through validated planning-artifact work units
- regression tests exist for per-agent assignment generation so workers do not receive overlapping or vague instructions
- a config author can understand exactly why a drain resolved the way it did from validation output alone
- a config author gets field-level schema/type/policy diagnostics that point to the exact TOML entry to fix
- analysis produces typed MCP results, and any fields used for routing are real validated MCP schema fields rather than plan-invented local vocabulary; those routed-on fields are declared in policy, validated against documented routing rules at startup, and are the only mechanism by which embedded analysis steps advance or loop work
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
- **Single source of authority per concept.** Policy config is the authority on allowed orchestration decisions and constraints; Rust runtime is the authority on executing those validated decisions, enforcing invariants, and handling termination mechanics.
- **Artifact acceptance is stricter than artifact presence.** A phase completes only when Ralph accepts an artifact matching the current run, attempt, drain, artifact type, and namespace.
- **Analysis owns branch decisions.** Development and fix phases do work; analysis decides whether that work loops back, requires replanning, or moves forward.
- **Multi-agent execution requires preplanned assignments.** If more than one concurrent agent is launched, the planning artifact must have emitted distinct worker assignments first.
- **Policy is standalone.** `ralph-workflow-policy` may not depend on `ralph-workflow`, import reducer code, or own runtime-only orchestration behavior.
- **Prompts are policy assets.** Prompt texts, template files, and templating behavior belong to `ralph-workflow-policy`, not `ralph-workflow`.
- **Config normalization is one-time, startup-only.** Runtime logic may consume normalized config, but may not silently repair invalid config after validation.
- **Inheritance is field-scoped, not semantic.** Inheriting a chain must not imply inherited role, policy mode, drain class, namespace, or session reuse.
