# Configurable Orchestration Layer

**Date:** 2026-04-09
**Status:** Proposed
**Scope:** Extract hardcoded orchestration policy, phase step sequences, prompt wiring, and output schema bindings into a declarative TOML configuration layer, making pipeline behavior changeable without Rust code modifications.

> **Honest scope boundary:** This plan moves *parameters*, *step sequences*, *template routing*, and *schema bindings* to config. It does **not** attempt to move *conditional logic* (branching decisions within phases) or *new phase creation* to config — those stay in Rust because they benefit most from compile-time safety. See §7 for the full gap analysis.

---

## 1) Motivation

The reducer/event/effect architecture provides strong correctness guarantees through exhaustive pattern matching and pure state transitions. However, changing pipeline *policy* (retry budgets, phase composition, prompt content, output schemas) currently requires Rust code changes across 5-8 files, recompilation, and redeployment.

This creates friction in three areas:

1. **Iteration velocity** — Tuning retry counts, prompt wording, or agent drains requires a full compile cycle even though these are fundamentally data, not logic.
2. **Experimentation** — Testing a new review depth or a different XSD schema means modifying Rust enums and handlers rather than editing a config file.
3. **Contributor accessibility** — Non-Rust contributors (prompt engineers, project leads) cannot adjust pipeline behavior without touching Rust code.

### What we already have

- **`~/.config/ralph-workflow.toml`** (external) — user-facing config: agent chains, drains, general settings, some budgets (`developer_iters`, `reviewer_reviews`, `max_dev_continuations`, `max_commit_residual_retries`).
- **`.agent/agents.toml`** (internal) — per-project agent registry, lives in the repo's `.agent/` directory.
- **Custom template engine** — already supports variables, conditionals, loops, partials, and defaults. 21 template files already exist in `src/prompts/templates/`.
- **XSD schemas** — already stored as separate files in `src/prompts/xsd/` (5 schemas: `plan.xsd`, `development_result.xsd`, `issues.xsd`, `fix_result.xsd`, `commit_message.xsd`).

The infrastructure is partially there. This plan is about **completing the extraction** — moving the remaining hardcoded policy into the existing config surface and wiring the existing template/schema files to be config-driven rather than code-driven.

---

## 2) External vs Internal Config Split

The project already establishes a two-tier config pattern:

| Tier | Location | Purpose | Example |
|------|----------|---------|---------|
| **External** (user-facing) | `~/.config/ralph-workflow.toml` | User preferences, agent chains, credentials, drains | "I want codex as my primary reviewer" |
| **Internal** (pipeline-facing) | `.agent/*.toml` | Pipeline mechanics, defaults, schemas, templates | "Planning phase uses plan.xsd and planning_xml.txt" |

This plan follows the same split:

### External: `~/.config/ralph-workflow.toml` (user overrides)

Things the **user** controls — what they want the pipeline to do differently from defaults:

```toml
[orchestration]
max_xsd_retries = 15              # "I want more patience on XSD failures"
timeout_strategy = "fallback_next" # "Don't retry timeouts, just move to next agent"

[phases]
sequence = ["planning", "development", "commit"]  # "Skip review for this workflow"

[phases.review]
enabled = false                    # Alternative way to skip review
```

### Internal: `.agent/pipeline.toml` (pipeline contract)

Things the **pipeline** defines — the default shape of orchestration, phase ordering, budgets, and recovery strategy. This is the "factory settings" for the pipeline engine:

```toml
[orchestration]
max_xsd_retries = 10
max_same_agent_retries = 2
max_dev_continuations = 3
max_fix_continuations = 10
loop_detection_threshold = 100
timeout_strategy = "retry_with_backoff"

recovery_levels = [
  { attempts = 3, strategy = "retry_same" },
  { attempts = 6, strategy = "reset_phase" },
  { attempts = 9, strategy = "reset_iteration" },
  { attempts = 999, strategy = "hard_reset" },
]

[phases]
sequence = ["planning", "development", "review", "commit", "finalization"]

# =========================================================================
# PHASE STEP SEQUENCES
# =========================================================================
# Each phase defines an ordered list of named effects (steps).
# The Rust engine maps each step name to a concrete Effect variant.
#
# Steps can have conditions — a small finite set of named predicates,
# each backed by a Rust function. This is NOT a general-purpose expression
# language; the condition names are an exhaustive enum.
#
# Available conditions:
#   "always"               — unconditional (default if omitted)
#   "not_xsd_retry"        — skip when in XSD retry mode
#   "not_xsd_retry_gt_1"   — skip when XSD retry count > 1
#   "is_continuation"      — only when continuing from partial/failed
#   "has_diff"             — only when git diff is non-empty
#   "no_diff"              — only when git diff is empty
#   "is_fix_drain"         — only when runtime drain is Fix
#   "is_review_drain"      — only when runtime drain is Review (not Fix)
#   "has_iterations_left"  — only when iteration < total_iterations
#   "recovery_success"     — only when recovery just completed
#
# Adding a new condition requires adding a variant to the Rust
# StepCondition enum and a one-line evaluator. This is intentionally
# limited — if you need arbitrary predicates, that's a scripting
# language and we chose not to go there (see §7).

[phases.planning]
enabled = true
output_format = "xml"
cleanup_files = [".agent/tmp/plan.xml"]
steps = [
  { effect = "initialize_chain" },
  { effect = "ensure_gitignore" },
  { effect = "cleanup_context", when = "not_xsd_retry" },
  { effect = "materialize_inputs" },
  { effect = "prepare_prompt" },
  { effect = "cleanup_xml", when = "not_xsd_retry_gt_1" },
  { effect = "invoke_agent" },
  { effect = "extract_xml" },
  { effect = "validate_xml" },
  { effect = "write_markdown" },
  { effect = "archive" },
]

[phases.development]
enabled = true
output_format = "xml"
cleanup_files = [".agent/tmp/development_result.xml"]
drains = ["development", "analysis"]   # two-drain workflow: dev then analysis
steps = [
  { effect = "write_continuation_context", when = "is_continuation" },
  { effect = "initialize_chain" },
  { effect = "cleanup_context", when = "not_xsd_retry" },
  { effect = "materialize_inputs" },
  { effect = "prepare_prompt" },
  { effect = "cleanup_xml", when = "not_xsd_retry_gt_1" },
  { effect = "invoke_agent" },
  { effect = "invoke_analysis_agent" },
  { effect = "extract_xml" },
  { effect = "validate_xml" },
  { effect = "write_markdown" },
  { effect = "archive" },
]

[phases.review]
enabled = true
output_format = "xml"
cleanup_files = [".agent/tmp/issues.xml"]
# Review has two sub-sequences depending on drain.
# When runtime_drain == Fix, the fix steps run instead.
steps = [
  { effect = "cleanup_context", when = "not_xsd_retry" },
  { effect = "materialize_inputs" },
  { effect = "prepare_prompt" },
  { effect = "cleanup_xml", when = "not_xsd_retry_gt_1" },
  { effect = "invoke_agent" },
  { effect = "extract_xml" },
  { effect = "validate_xml" },
  { effect = "write_markdown", when = "is_review_drain" },
  { effect = "extract_snippets", when = "is_review_drain" },
  { effect = "apply_fix", when = "is_fix_drain" },
  { effect = "archive" },
]

[phases.review.fix]
cleanup_files = [".agent/tmp/fix_result.xml", ".agent/tmp/development_result.xml"]

[phases.commit]
enabled = true
output_format = "xml"
cleanup_files = [".agent/tmp/commit_message.xml"]
steps = [
  { effect = "initialize_chain" },
  { effect = "check_diff" },
  { effect = "skip_phase", when = "no_diff" },
  { effect = "materialize_inputs" },
  { effect = "prepare_prompt" },
  { effect = "cleanup_xml", when = "not_xsd_retry_gt_1" },
  { effect = "invoke_agent" },
  { effect = "extract_xml" },
  { effect = "validate_xml" },
  { effect = "create_commit" },
  { effect = "archive" },
]

[phases.finalization]
enabled = true
# No prompts or schemas — pure internal effects
steps = [
  { effect = "finalize" },
]
```

**What step sequences buy you:** Reordering effects within a phase, adding/removing steps, and changing conditions — all without touching Rust. The Rust engine becomes a generic step executor: read the step list, evaluate conditions, emit the named effect.

**What they don't buy you:** New effect types (those still need a Rust `Effect` variant and handler). New conditions (those need a Rust `StepCondition` variant). But both are single-point additions — one enum variant + one match arm — not the current 100-200 line phase-specific orchestration modules.

### Internal: `.agent/prompts.toml` (prompt routing and schema binding)

The third TOML — defines the contract between the pipeline and agents: which templates get sent, which schemas validate output, which variables get injected. This is the file a prompt engineer edits:

```toml
# =============================================================================
# TEMPLATE RESOLUTION
# =============================================================================
# Templates are resolved in order:
#   1. .agent/templates/<name>       (per-project override)
#   2. compiled-in defaults          (built-in)
#
# Schema resolution follows the same pattern:
#   1. .agent/schemas/<name>         (per-project override)
#   2. compiled-in defaults          (built-in)

# =============================================================================
# PLANNING PHASE
# =============================================================================

[planning]
prompt_template = "planning_xml.txt"
xsd_retry_template = "planning_xsd_retry.txt"
output_schema = "plan.xsd"

[planning.vars]
PROMPT_MD = { source = "file", path = "PROMPT.md" }

# =============================================================================
# DEVELOPMENT PHASE
# =============================================================================

[development]
prompt_template = "developer_iteration_xml.txt"
continuation_template = "developer_iteration_continuation_xml.txt"
xsd_retry_template = "developer_iteration_xsd_retry.txt"
xsd_retry_continuation_template = "developer_iteration_xsd_retry_continuation.txt"
output_schema = "development_result.xsd"

[development.vars]
ITERATION = { source = "state", field = "iteration" }
TOTAL_ITERATIONS = { source = "config", field = "general.developer_iters" }
CONTEXT_LEVEL = { source = "config", field = "general.developer_context" }
PROMPT_MD = { source = "file", path = "PROMPT.md" }
PLAN_MD = { source = "file", path = ".agent/PLAN.md" }

# =============================================================================
# REVIEW PHASE
# =============================================================================

[review]
prompt_template = "review_xml.txt"
xsd_retry_template = "review_xsd_retry.txt"
output_schema = "issues.xsd"

[review.vars]
PROMPT_MD = { source = "file", path = "PROMPT.md" }
PLAN_MD = { source = "file", path = ".agent/PLAN.md" }
REVIEW_DEPTH = { source = "config", field = "general.review_depth" }

# Sub-phase: fix (runs when review finds issues)
[review.fix]
prompt_template = "fix_mode_xml.txt"
xsd_retry_template = "fix_mode_xsd_retry.txt"
output_schema = "fix_result.xsd"

[review.fix.vars]
PROMPT_MD = { source = "file", path = "PROMPT.md" }
PLAN_MD = { source = "file", path = ".agent/PLAN.md" }
ISSUES_MD = { source = "file", path = ".agent/ISSUES.md" }

# =============================================================================
# COMMIT PHASE
# =============================================================================

[commit]
prompt_template = "commit_message_xml.txt"
simplified_template = "commit_simplified.txt"
xsd_retry_template = "commit_xsd_retry.txt"
output_schema = "commit_message.xsd"

[commit.vars]
DIFF = { source = "computed", provider = "git_diff" }
FILES_CHANGED = { source = "computed", provider = "files_changed_list" }
BRANCH_NAME = { source = "computed", provider = "current_branch" }

# =============================================================================
# ANALYSIS (sub-phase of development, validates dev output)
# =============================================================================

[analysis]
prompt_template = "analysis_system_prompt.txt"

[analysis.vars]
PROMPT_MD = { source = "file", path = "PROMPT.md" }
PLAN_MD = { source = "file", path = ".agent/PLAN.md" }

# =============================================================================
# OUTPUT EXTRACTION (per-phase field mappings)
# =============================================================================
# Defines which fields to extract from agent output after schema validation.
# Used by the generic extractor to populate phase-specific result structs.

[extraction.planning]
root_element = "plan"
fields = [
  { name = "summary", xpath = "/plan/summary", required = true },
  { name = "steps", xpath = "/plan/steps/step", required = false, list = true },
]

[extraction.development]
root_element = "development-result"
fields = [
  { name = "status", xpath = "/development-result/status", required = true },
  { name = "continuation_needed", xpath = "/development-result/continuation-needed", required = false, default = "false" },
  { name = "files_changed", xpath = "/development-result/files-changed/file", required = false, list = true },
]

[extraction.review]
root_element = "issues"
fields = [
  { name = "issue", xpath = "/issues/issue", required = false, list = true },
]

[extraction.commit]
root_element = "commit-message"
fields = [
  { name = "subject", xpath = "/commit-message/subject", required = true },
  { name = "body", xpath = "/commit-message/body", required = false },
]
```

### The three-file summary

```
.agent/
  ├── agents.toml      WHO  — agent definitions, capabilities, CLI flags
  ├── pipeline.toml    WHEN — phase ordering, budgets, retry policy, recovery
  └── prompts.toml     WHAT — templates, schemas, variables, extraction rules
```

Each file has a single concern:
- **`agents.toml`**: "Who can do work?" (already exists)
- **`pipeline.toml`**: "How does the pipeline behave?" (new)
- **`prompts.toml`**: "What do agents see and produce?" (new)

### Merge semantics

Same pattern as the existing config loader (CLI > env > external TOML > internal TOML > compiled defaults):

```
Compiled defaults (current const fns)
  ← overridden by .agent/pipeline.toml (internal pipeline)
  ← overridden by .agent/prompts.toml  (internal prompts)
    ← overridden by ~/.config/ralph-workflow.toml (external user prefs)
      ← overridden by CLI flags / env vars
```

Field-level merge, not file-level. If the user sets `max_xsd_retries = 15` externally but doesn't touch `timeout_strategy`, the internal default for `timeout_strategy` still applies.

The external `ralph-workflow.toml` can override fields from both `pipeline.toml` and `prompts.toml`. For example, a user can set `[orchestration]` overrides (pipeline concern) and `[phases.review] enabled = false` (also pipeline concern) and `[development] prompt_template = "my_custom_dev.txt"` (prompt concern) all in their external config. The loader routes each section to the appropriate internal config struct.

### Template and schema file resolution

Templates and schemas referenced in `prompts.toml` are resolved with per-project override support:

```
Resolution order for "planning_xml.txt":
  1. .agent/templates/planning_xml.txt     ← per-project override (if exists)
  2. compiled-in src/prompts/templates/     ← built-in default

Resolution order for "plan.xsd":
  1. .agent/schemas/plan.xsd               ← per-project override (if exists)
  2. compiled-in src/prompts/xsd/           ← built-in default
```

This means a project can customize its prompt templates or XSD schemas by dropping files into `.agent/templates/` or `.agent/schemas/` without touching the binary or the global config.

---

## 3) Design Principles

1. **Data stays data, logic stays Rust.** Move constants, paths, and wiring out of Rust. Keep state machines, effect handling, and type safety in Rust.
2. **Follow the established external/internal split.** User preferences in `~/.config/ralph-workflow.toml`, pipeline mechanics in `.agent/*.toml`.
3. **Backward compatible defaults.** Every new config field has a default matching current behavior. Existing configs work without changes.
4. **No new runtime dependencies.** No scripting languages, no embedded interpreters. The existing template engine and TOML parser are sufficient.
5. **Incremental adoption.** Each phase below can be shipped independently and provides standalone value.
6. **One TOML per concern.** Agent definitions in `agents.toml`, pipeline shape in `pipeline.toml`, prompt routing in `prompts.toml`. Each file is self-contained and focused.

---

## 4) Implementation Phases

### Phase 1: Consolidate Orchestration Budgets into `.agent/pipeline.toml`

**Goal:** All retry/continuation/recovery magic numbers live in `.agent/pipeline.toml` with user overrides possible via `~/.config/ralph-workflow.toml`.

**Current state:** Some budgets are already in external TOML (`max_dev_continuations`, `max_commit_residual_retries`). Others are hardcoded as `const fn` defaults in `reducer/state/continuation/state.rs`:
- `max_xsd_retry_count = 10`
- `max_same_agent_retry_count = 2`
- `max_continue_count = 3`
- `max_fix_continue_count = 10`
- `DEFAULT_LOOP_DETECTION_THRESHOLD = 100`

**Changes:**

1. Create `.agent/pipeline.toml` with the `[orchestration]` section (see Section 2 above for full example).

2. Create an `OrchestrationPolicy` struct in `reducer/state/` that:
   - Loads from `pipeline.toml` `[orchestration]` via the existing config loader
   - Accepts overrides from `~/.config/ralph-workflow.toml` `[orchestration]`
   - Falls back to current hardcoded defaults when fields are absent in both
   - Is passed to `determine_next_effect()` and reducers instead of them reading `const fn` defaults

3. Migrate existing scattered defaults to use `OrchestrationPolicy`:
   - `continuation/state.rs` default functions become fallbacks for missing config
   - `determine_next_effect()` reads thresholds from policy, not consts
   - `reduce_awaiting_dev_fix_event()` reads recovery levels from policy

**Files touched:**
- `config/loader/` — parse `pipeline.toml` and merge `[orchestration]` from both files
- `reducer/state/continuation/state.rs` — use policy defaults
- `reducer/state/mod.rs` — add `OrchestrationPolicy` struct
- `reducer/orchestration/xsd_retry/determine_next_effect.rs` — accept policy
- `reducer/state_reduction/awaiting_dev_fix.rs` — read recovery levels from policy
- `app/core.rs` — thread policy through to orchestration

**Effort:** Low-Medium
**Risk:** Low — purely additive, defaults preserve current behavior

---

### Phase 2: Phase Composition and Step Sequences via `pipeline.toml`

**Goal:** Define which phases run, in what order, and what effects each phase emits — all via `pipeline.toml`. This replaces both the hardcoded phase sequence in `phase_effects/mod.rs` AND the per-phase 100-200 line match cascades in `planning.rs`, `development.rs`, `review.rs`, `commit.rs`.

**Current state:** Phase ordering is implicit in `derive_phase_specific_effect()`. But more importantly, **each phase has a dedicated orchestration module** (100-200 lines each) that decides which effect to emit next based on the current sub-state within that phase. These are the files you touch most when changing pipeline behavior:

- `planning.rs`: 12-step effect sequence
- `development.rs`: two-drain workflow (dev + analysis) with continuation logic
- `review.rs`: dual-drain switching (fix vs review, different step lists per drain)
- `commit.rs`: conditional skip on empty diff, XSD retry special handling

**Changes:**

1. Add the `[phases]` section to `pipeline.toml` with step sequences (see Section 2 above for the full example).

2. Create a `PhaseConfig` struct and `StepConfig` struct:
   - `PhaseConfig` holds enabled status, step list, cleanup files, drain list
   - `StepConfig` holds effect name + optional condition name
   - Phase transitions consult the `sequence` list for "next phase"
   - The `PipelinePhase` enum stays in Rust, but the *active subset, order, and internal steps* come from config

3. Create a `StepCondition` enum — a **finite, exhaustive set** of named predicates:

   ```rust
   enum StepCondition {
       Always,
       NotXsdRetry,
       NotXsdRetryGt1,
       IsContinuation,
       HasDiff,
       NoDiff,
       IsFixDrain,
       IsReviewDrain,
       HasIterationsLeft,
       RecoverySuccess,
   }
   
   impl StepCondition {
       fn evaluate(&self, state: &PipelineState) -> bool {
           match self {
               Self::Always => true,
               Self::NotXsdRetry => state.xsd_retry_count == 0,
               Self::NoDiff => state.diff_empty,
               // ... one line per variant
           }
       }
   }
   ```

4. Replace per-phase orchestration modules with a **generic step executor**:

   ```rust
   fn derive_phase_specific_effect(
       state: &PipelineState,
       phase_config: &PhaseConfig,
   ) -> Effect {
       phase_config.steps.iter()
           .filter(|step| step.condition.evaluate(state))
           .find(|step| !state.completed_steps.contains(&step.effect))
           .map(|step| step.effect.to_effect(state))
           .unwrap_or(Effect::PhaseComplete)
   }
   ```

   This replaces the 4 dedicated phase modules (planning.rs, development.rs, review.rs, commit.rs) with one generic executor + config.

5. Users can override in `~/.config/ralph-workflow.toml`:
   ```toml
   [phases]
   sequence = ["planning", "development", "commit"]  # skip review
   
   [phases.review]
   enabled = false
   ```

**What this enables that the previous plan version didn't:**
- Reorder steps within a phase (TOML edit)
- Add/remove steps from a phase (TOML edit)
- Change when a step runs (change `when` condition, TOML edit)
- Change which files get cleaned up per phase (TOML edit)

**What still requires Rust:**
- New effect types (new `Effect` variant + handler)
- New conditions (new `StepCondition` variant + one-line evaluator)
- But both are single-point additions, not the current 100-200 line modules

**Files touched:**
- `config/loader/` — parse `[phases]` with step sequences from `pipeline.toml`
- `reducer/orchestration/phase_effects/mod.rs` — replace phase-specific dispatch with generic step executor
- `reducer/orchestration/phase_effects/planning.rs` — delete (replaced by config)
- `reducer/orchestration/phase_effects/development.rs` — delete (replaced by config)
- `reducer/orchestration/phase_effects/review.rs` — delete (replaced by config)
- `reducer/orchestration/phase_effects/commit.rs` — delete (replaced by config)
- New: `reducer/orchestration/step_executor.rs` — generic step executor
- New: `reducer/orchestration/step_condition.rs` — `StepCondition` enum

**Effort:** Medium-High
**Risk:** Medium — this is the biggest structural change. The per-phase modules encode subtle invariants (e.g., development's two-drain workflow, review's fix/review switching). The step sequence representation must capture these correctly. Extensive testing against current behavior required.

**Migration strategy:** Start with Planning (simplest, linear sequence). Validate. Then Commit. Then Development. Then Review (most complex). Each phase can be migrated independently — the step executor falls through to the old per-phase module for unmigrated phases.

---

### Phase 3: Prompt Routing via `.agent/prompts.toml`

**Goal:** Make the mapping from "pipeline situation" to "template file + variables" fully config-driven via `prompts.toml`, so new prompt strategies don't require Rust changes.

**Current state:** The template engine and templates already exist. But the *selection* of which template to use, and which variables to inject, is hardcoded in `prompts/prompt_dispatch.rs` and phase-specific handler code.

**Changes:**

1. Create `.agent/prompts.toml` with per-phase template bindings and variable declarations (see Section 2 above for the full example).

2. Build a `TemplateResolver` that:
   - Takes a phase + situation (normal, xsd_retry, continuation)
   - Looks up the template name from `prompts.toml`
   - Resolves variable bindings by source type:
     - `source = "file"` — reads file content at render time
     - `source = "state"` — reads from `PipelineState` field
     - `source = "config"` — reads from unified config
     - `source = "computed"` — calls a named provider function (e.g., `git_diff`)
   - Calls the existing template engine with the resolved variables

3. The prompt dispatch code (`prompt_dispatch.rs`) becomes a thin wrapper around `TemplateResolver` instead of per-phase match arms.

**Benefit:** Adding a new variable to a prompt, or changing which template a phase uses, becomes a TOML edit. The Rust `TemplateResolver` is generic — it doesn't know about specific phases.

**Files touched:**
- `config/loader/` — parse `prompts.toml`
- `prompts/prompt_dispatch.rs` — replace per-phase dispatch with `TemplateResolver`
- New: `prompts/template_resolver.rs` — generic template+variable resolution

**Effort:** Medium
**Risk:** Low-Medium — the template engine already works; this is about making the wiring configurable

---

### Phase 4: Schema-Driven Output Extraction via `prompts.toml`

**Goal:** Replace per-phase XML extraction logic with a generic extractor configured by the `[extraction.*]` section in `prompts.toml`.

**Current state:** Each phase has dedicated extraction code (e.g., `ExtractPlanningXml`, `ExtractDevelopmentXml`, `ExtractReviewIssuesXml`) that knows the specific XML structure and field names for that phase. This is the most rigid coupling — adding a new output field means Rust code changes in the extractor.

**Changes:**

1. The `[extraction.*]` mappings in `prompts.toml` (see Section 2 above) define root elements, field XPaths, required/optional status, and defaults.

2. Build a `GenericXmlExtractor` that:
   - Reads the extraction config for the current phase from `prompts.toml`
   - Validates against the XSD (schema path also from `prompts.toml`)
   - Extracts fields by XPath into a `HashMap<String, ExtractedValue>`
   - Returns a generic `ExtractionResult` instead of phase-specific structs

3. Phase-specific extraction effects (`ExtractPlanningXml`, etc.) delegate to `GenericXmlExtractor` with the phase's extraction config.

4. Future-proofs for JSON: the extractor abstraction can support both XML (XPath) and JSON (JSONPath) based on `output_format` in `pipeline.toml`. JSON support is out of scope for this plan but the architecture should not preclude it.

**Files touched:**
- `config/loader/` — parse `[extraction.*]` from `prompts.toml`
- New: `files/llm_output_extraction/generic_extractor.rs` — generic XML extraction
- `reducer/handler/` — extraction effect handlers delegate to generic extractor
- Existing per-phase extractors become thin wrappers or are replaced

**Effort:** High
**Risk:** Medium-High — extraction is correctness-critical. Thorough testing required. The per-phase structs that downstream code depends on would need to either remain (with generic extractor populating them) or be replaced with a generic representation (larger refactor).

**Recommended approach:** Keep per-phase result structs for now. The generic extractor populates them from config-driven field mappings. This preserves type safety downstream while making the extraction *input* (schema, fields) configurable.

---

### Phase 5: Orchestration Rule Table

**Goal:** Replace the priority-ordered if/else cascade in `determine_next_effect()` with a data-driven rule table. This is the complement to Phase 2's step sequences — Phase 2 handles "what effects does this phase emit," Phase 5 handles "what cross-cutting concerns (retries, recovery, loop detection) override phase-specific effects."

**Current state:** `determine_next_effect()` in `xsd_retry/determine_next_effect.rs` is a 15-level priority cascade. Each level is a condition + effect derivation. The priority order is critical — same-agent retry must be checked before XSD retry, loop detection before everything, etc.

**Why this is NOT optional:** The 15-level cascade in `determine_next_effect()` is where most orchestration bugs live. It's the hardest code to modify safely. Structuring it as a rule table makes the priority ordering explicit and each rule independently testable.

**Changes:**

1. Define orchestration rules as an ordered list:

```rust
struct OrchestrationRule {
    name: &'static str,
    priority: u32,
    condition: fn(&PipelineState, &OrchestrationPolicy) -> bool,
    derive: fn(&PipelineState, &OrchestrationPolicy) -> Effect,
}
```

2. The `determine_next_effect()` function becomes:

```rust
fn determine_next_effect(
    state: &PipelineState,
    policy: &OrchestrationPolicy,
    rules: &[OrchestrationRule],
) -> Effect {
    rules.iter()
        .find(|rule| (rule.condition)(state, policy))
        .map(|rule| (rule.derive)(state, policy))
        .unwrap_or_else(|| step_executor::next_step(state, policy))
}
```

3. Rules stay in Rust (not TOML) — these are safety-critical decisions (loop detection, budget exhaustion, connectivity checks). The benefit is structural: adding a new rule is "add one entry to the array" instead of "find the right spot in nested if/else."

4. The rule table's thresholds come from `OrchestrationPolicy` (Phase 1), so the *parameters* are configurable even though the *logic* is in Rust.

**Files touched:**
- `reducer/orchestration/xsd_retry/determine_next_effect.rs` — refactor to rule table
- New: `reducer/orchestration/rules.rs` — rule definitions

**Effort:** Medium
**Risk:** Medium — must preserve exact priority ordering. Each rule should have a focused unit test.

---

## 5) What Stays in Rust (Non-Negotiable)

These are **not candidates for extraction** — they are the engine, not the policy:

| Component | Why it stays |
|-----------|-------------|
| `PipelineState` struct | Type-safe state representation, exhaustive matching |
| `PipelineEvent` / `Effect` enums | Compile-time guarantees that all cases are handled |
| `reduce()` function | Pure state machine logic |
| Effect handlers | I/O boundary — must interact with filesystem, agents, git |
| Event loop (`app/core.rs`) | Core orchestrate-handle-reduce cycle |
| Template engine | Already generic, no phase-specific logic |
| Agent invocation | Process spawning, output parsing, stream handling |
| Cross-cutting rules (Phase 5) | Safety-critical: loop detection, budget exhaustion, connectivity |
| `StepCondition` evaluators | One-line Rust functions, but the *set of available conditions* is a code change |
| Named effect mapping | Mapping step effect names (e.g., `"invoke_agent"`) to `Effect` enum variants |

The Rust layer becomes a **generic pipeline engine**. The TOML layer tells it *what pipeline to run*.

### The engine/policy split

After all phases, the codebase splits cleanly into:

```
TOML (policy — what to do):
  - Which phases run, in what order
  - What steps each phase has, with what conditions
  - What files to clean up
  - What budgets/thresholds to use
  - What templates, schemas, variables per phase
  - What fields to extract from output

Rust (engine — how to do it):
  - Step executor (generic loop: evaluate condition → emit effect)
  - Effect handlers (I/O: invoke agent, read file, write file, git diff)
  - Reducers (state transitions: event → new state)
  - Cross-cutting rules (loop detection, budget exhaustion, retry routing)
  - Condition evaluators (the finite set of predicates steps can use)
  - Template engine (variable substitution, rendering)
```

---

## 6) End-State Architecture

```
~/.config/ralph-workflow.toml          External (user overrides)
  ├── [general]                        Existing: iterations, review depth, etc.
  ├── [orchestration]                  User overrides for budgets/thresholds
  ├── [phases]                         User overrides (e.g., skip review)
  ├── [agent_chains]                   Existing: drain definitions
  ├── [agent_drains]                   Existing: role-to-chain bindings
  └── [ccs] / [agents]                 Existing: agent tool definitions

.agent/                                Internal (pipeline contract)
  ├── agents.toml                      WHO  — agent definitions (existing)
  ├── pipeline.toml                    WHEN — phase ordering, budgets, recovery (new)
  ├── prompts.toml                     WHAT — templates, schemas, variables, extraction (new)
  ├── templates/                       Per-project template overrides (new, optional)
  │   └── planning_xml.txt             Overrides compiled-in default
  └── schemas/                         Per-project schema overrides (new, optional)
      └── plan.xsd                     Overrides compiled-in default

src/prompts/templates/*.txt            Compiled-in defaults (existing)
src/prompts/xsd/*.xsd                  Compiled-in defaults (existing)

src/reducer/                           Rust engine (generic, config-driven)
  ├── state/                           State types + OrchestrationPolicy
  ├── orchestration/                   Rule-based effect derivation
  ├── state_reduction/                 Pure reducers (unchanged)
  ├── effect/                          Effect types (unchanged)
  └── handler/                         Effect handlers (use config for template/schema lookup)
```

**What changing pipeline behavior looks like after this plan:**

| Change | Before | After |
|--------|--------|-------|
| Adjust XSD retry budget | Edit `state.rs`, recompile | Edit `.agent/pipeline.toml` or `~/.config/ralph-workflow.toml` |
| Change recovery escalation | Edit `awaiting_dev_fix.rs`, recompile | Edit `.agent/pipeline.toml` |
| Skip review phase | Not possible without code change | Set `phases.review.enabled = false` in `pipeline.toml` |
| Reorder steps within a phase | Edit 100-200 line `planning.rs`/`development.rs`/etc., recompile | Reorder `steps` array in `pipeline.toml` |
| Add a step to a phase | Add match arm to phase module, recompile | Add entry to `steps` array in `pipeline.toml` |
| Change when a step runs | Edit conditional in phase module, recompile | Change `when` field in `pipeline.toml` |
| Change cleanup files per phase | Edit const array in phase module, recompile | Edit `cleanup_files` in `pipeline.toml` |
| Change prompt wording | Edit `.txt` template | Edit `.txt` template (same, already works) |
| Change which template a phase uses | Edit `prompt_dispatch.rs`, recompile | Edit `.agent/prompts.toml` |
| Change output schema | Edit Rust extractor code, recompile | Edit `.xsd` file + `[extraction.*]` in `prompts.toml` |
| Add variable to prompt | Edit Rust prompt builder, recompile | Add to `[phase.vars]` in `prompts.toml` |
| Reorder phases | Edit `phase_effects/mod.rs`, recompile | Edit `phases.sequence` in `pipeline.toml` |
| Customize prompts per-project | Not possible | Drop overrides in `.agent/templates/` |
| Add new effect type | N/A (already requires Rust) | Still requires Rust: new `Effect` variant + handler |
| Add new step condition | N/A | Requires Rust: new `StepCondition` variant (one-line evaluator) |
| Add entirely new phase | Edit 5-8 files, recompile | Requires Rust for `PipelinePhase` variant + reducer arm, but no dedicated orchestration module — step sequence goes in `pipeline.toml` |

---

## 7) Implementation Priority and Dependencies

```
Phase 1 (Orchestration Budgets)     ← no dependencies, start here
    │
Phase 2 (Phase Composition + Steps) ← depends on Phase 1 (uses OrchestrationPolicy)
    │
    ├── Phase 3 (Template Binding)   ← depends on Phase 2 (uses PhaseConfig)
    │
    ├── Phase 4 (Schema Extraction)  ← depends on Phase 2 (uses PhaseConfig)
    │
    └── Phase 5 (Rule Table)         ← depends on Phase 2 (step executor is fallback)
```

**Recommended order:** 1 → 2 → 5 → 3 → 4. Phases 3 and 4 can be done in parallel once Phase 2 is complete. Phase 5 should follow Phase 2 closely since the step executor and rule table are the two halves of `determine_next_effect()`.

---

## 8) Gap Analysis — What This Plan Does and Does NOT Achieve

### The goal

> Move orchestration logic to config files so we can easily change, add phases, edit phase logic, control what variables represent (path vs content), etc.

### What this plan fully achieves

| Capability | Mechanism |
|-----------|-----------|
| Change retry/recovery budgets without recompiling | `pipeline.toml` `[orchestration]` (Phase 1) |
| Skip/reorder/enable phases without recompiling | `pipeline.toml` `[phases]` (Phase 2) |
| Reorder/add/remove steps within a phase | `pipeline.toml` step sequences (Phase 2) |
| Change when a step runs (e.g., "skip cleanup on retry") | `pipeline.toml` step conditions (Phase 2) |
| Change which template a phase uses | `prompts.toml` (Phase 3) |
| Change what variables a template receives | `prompts.toml` `[phase.vars]` (Phase 3) |
| Control variable semantics (file path vs content) | `prompts.toml` variable `source` type (Phase 3) |
| Change output schema per phase | `prompts.toml` (Phase 4) |
| Change which fields are extracted from output | `prompts.toml` `[extraction.*]` (Phase 4) |
| Per-project template/schema overrides | `.agent/templates/`, `.agent/schemas/` |

### What this plan partially achieves

| Capability | What works | What doesn't |
|-----------|-----------|-------------|
| Add a new phase | Phase ordering and step sequence go in TOML. New phase needs significantly less Rust than before (no dedicated orchestration module). | Still needs `PipelinePhase` enum variant + reducer match arm + any new effect types. |
| Edit phase logic | Step ordering, conditions, and parameters are TOML-editable. | Adding a genuinely new *type of condition* or *type of effect* requires a Rust enum variant. |

### What this plan intentionally does NOT achieve

- **Arbitrary conditional logic in config:** The `StepCondition` set is finite and exhaustive (currently ~10 conditions). Adding a new condition requires one Rust enum variant + one-line evaluator. This is a deliberate tradeoff — a general-purpose expression language in TOML would be a de facto scripting runtime, which we chose not to add.

- **Cross-cutting orchestration rules in config:** The 15-level priority cascade in `determine_next_effect()` (loop detection, budget exhaustion, connectivity checks, retry routing) stays in Rust as a rule table (Phase 5). These are safety-critical decisions — misconfiguration could cause infinite loops or data loss. Their *thresholds* are configurable, but their *logic* is not.

- **Scripting runtime (Lua, Rhai, etc.):** Intentionally excluded. TOML + templates + step sequences cover the known requirements. If the finite condition set proves insufficient for real use cases, Rhai can be added surgically at specific extension points (step condition evaluation, variable computation) without rearchitecting.

- **JSON output format:** The architecture accommodates it (Phase 4 abstracts over format), but implementing JSON extractors is a separate effort.

- **UI/CLI changes:** This plan affects the pipeline engine internals. CLI flags and output formatting are out of scope.

- **Migration tooling:** No automated migration. New fields have defaults matching current behavior.

### Is this worthwhile?

**Yes, with caveats.** The plan moves ~80% of day-to-day pipeline changes from "edit 3-8 Rust files + recompile" to "edit 1-2 TOML files." The remaining ~20% (new effect types, new conditions, new phases) still requires Rust, but the per-change Rust footprint shrinks from 100-200 lines across multiple files to a single enum variant + match arm.

The key insight: the most frequently changed things (budgets, step sequences, prompt routing, schema bindings) are data, not logic. The infrequently changed things (new phase types, safety checks, budget exhaustion) are genuine logic that benefits from compile-time verification. The plan draws the line at the right place.

---

## 9) Success Criteria

- Three-file internal config split: `agents.toml` (WHO), `pipeline.toml` (WHEN), `prompts.toml` (WHAT)
- All current hardcoded budgets/thresholds are configurable via `pipeline.toml` `[orchestration]`
- Phase ordering is driven by `phases.sequence` in `pipeline.toml`
- Phase-internal step sequences are driven by `steps` arrays in `pipeline.toml`
- Per-phase dedicated orchestration modules (`planning.rs`, `development.rs`, `review.rs`, `commit.rs`) are replaced by a generic step executor + config
- Template selection and variable binding per phase is driven by `prompts.toml`
- XSD schema binding and extraction mappings per phase are driven by `prompts.toml`
- External `~/.config/ralph-workflow.toml` can override any field from either internal file
- Per-project template/schema overrides work via `.agent/templates/` and `.agent/schemas/`
- Cross-cutting orchestration rules are structured as a rule table with explicit priority ordering
- All existing tests pass without modification (defaults match current behavior)
- A non-Rust contributor can change retry budgets, step sequences, step conditions, prompt templates, output schemas, and phase ordering by editing TOML and template files alone
- Adding a new condition or effect type requires exactly one enum variant + one match arm in Rust (not 100-200 lines across multiple files)
