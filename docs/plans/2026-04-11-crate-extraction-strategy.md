# Crate Extraction Strategy: Breaking the ralph-workflow Monolith

**Status:** Proposal  
**Date:** 2026-04-11

---

## Problem

`ralph-workflow` is a ~280K-line monolithic crate. Any change to any module forces recompilation of the entire crate (~15.3s even on a powerful remote build server). The critical compilation path runs `reqwest (6.8s) → jsonschema (6.5s) → ralph-workflow (15.3s)` — 28.6s sequentially before any test target can begin.

As the codebase grows, this compounds: agent work slows down, iteration loops get longer, and developer flow suffers. The `mcp-server` extraction proved the pattern works — it compiles in 1.8s independently and no longer forces ralph-workflow recompilation when changed.

This proposal is based on exhaustive analysis of every module's inbound and outbound coupling surfaces — every `use crate::` import across the codebase has been cataloged.

---

## Current Module Landscape

| Module | Lines | Outbound Coupling | Inbound Consumers | Extraction Difficulty |
|--------|-------|-------------------|--------------------|----------------------|
| **reducer** | ~80K | 13+ external modules; boundary imports 23+ | Core of everything | Not feasible yet |
| **files** | ~27K | workspace, logger, common | 47 files | Moderate |
| **json_parser** | ~23K | config::Verbosity, logger::Colors, common::truncate_text, workspace::Workspace (codex only) | Consumed by pipeline/agents | **Easy** |
| **agents** | ~19K | workspace, config, common, io::http_fetch | 165 files | Hard (huge API surface) |
| **prompts** | ~17K | checkpoint, agents::session, workspace, executor | 50+ files | **Hard** (not low coupling) |
| **git_helpers** | ~16K | workspace, executor, files::file_contains_marker, common::GitOid, logger | 9 direct consumers | Moderate |
| **pipeline** | ~16K | executor, files, git_helpers, logger, workspace, config, agents, rendering | 31 files | Not worth it (pure consumer) |
| **config** | ~9K | agents::fallback, common::domain_types | 74 files | Moderate |
| **phases** | ~8K | reducer, checkpoint, agents, config, prompts, pipeline, workspace, guidelines | 60 files (PhaseContext: 44) | Not feasible (glue layer) |
| **checkpoint** | ~7K | agents, config, common, executor, logger, prompts, reducer::state, workspace | 59 files | Hard (bidirectional with reducer) |
| **executor** | ~4.4K | agents::JsonParserType (single import) | 48 files | **Easy** |
| **rendering** | ~2.8K | logger, files::llm_output_extraction, reducer::ui_event | 4 files | Too small |
| **guidelines** | ~2.6K | language_detector::ProjectStack | 4 files | **Trivial** |
| **language_detector** | ~1.9K | workspace::Workspace | 13 files | **Trivial** |
| **workspace** | ~1.6K | (none significant) | **172 files** | **Easy** (foundation) |
| **boundary** | ~1.2K | mcp_server, executor (re-exports) | Implicit (facade) | N/A |

---

## Principles

### 1. Extracted crates MUST NOT depend on ralph-workflow

This is the single most important rule. If a new crate imports from `ralph-workflow`, it is not an extraction — it is just a file move that adds complexity without improving compilation.

The dependency arrow is always: `ralph-workflow → extracted-crate`, never the reverse.

### 2. Shared types live in dedicated type crates

When ralph-workflow and an extracted crate both need the same types, those types move into a small, stable types crate. This crate should be:

- Small and rarely changing
- Free of business logic
- The leaf of the dependency graph

### 3. Dependency injection over concrete coupling

Extracted crates must not reach into ralph-workflow for services. Instead:

- Define traits in the extracted crate for any capability it needs from the outside world
- ralph-workflow provides the concrete implementation when wiring things together
- The extracted crate remains testable in isolation with simple trait mocks

The `mcp-server` extraction is the reference template. It defines `HostSession`, `WorkspaceAdapter`, and `AuditSink` traits. ralph-workflow provides `RalphHostSessionAdapter`, `RalphWorkspaceAdapter`, and `RalphAuditSinkAdapter` in its `mcp_server/` bridge module. The extracted crate never touches ralph-workflow types.

### 4. No god-trait anti-pattern

When defining injected traits, keep them narrow and purpose-specific. A `trait Context` that provides file access, git operations, config, and logging is just the monolith hidden behind indirection. Each concern gets its own trait.

Counter-example from the current codebase: `PhaseContext` holds references to config, registry, logger, workspace, audit trail, timer, and more. It is imported by 44 files. This is exactly what extracted crates must NOT replicate — it couples everything to everything.

### 5. Feature flags for optional heavyweight dependencies

Currently, `ralph-workflow` has **zero feature-gated dependencies**. Every dependency (git2, jsonschema, quick-xml, ureq, notify, frunk) compiles unconditionally. Independent of crate extraction, feature-gating heavyweight deps that not all code paths need is an orthogonal win:

| Dependency | Used By | Feature-Gatable? |
|------------|---------|-------------------|
| `quick-xml` | files/llm_output_extraction only | Yes (`xml-validation`) |
| `jsonschema` | files/llm_output_extraction only | Yes (`json-schema`) |
| `notify` | files/protection only | Yes (`file-monitoring`) |
| `frunk` | Functional helpers | Yes (`functional-helpers`) |
| `strsim` | Fuzzy suggestions | Yes (`fuzzy-suggestions`) |
| `git2` | git_helpers throughout | No (too pervasive) |
| `ureq` | agents/opencode_api only | Yes (`opencode-api`) |

### 6. Extract leaves first

Start with modules that have the fewest outbound dependencies on ralph-workflow internals. Save deeply-coupled modules for later, after foundations are in place.

### 7. The adapter bridge pattern

Every extraction creates a thin bridge module inside ralph-workflow (like `ralph-workflow/src/mcp_server/`). This bridge:

- Implements the extracted crate's traits using ralph-workflow internals
- Translates between the extracted crate's types and ralph-workflow's domain types
- Handles lifecycle (construction, shutdown, audit accumulation)
- Is the **only** place where both type systems meet

---

## Extraction Candidates

Ordered by extraction feasibility based on measured coupling.

### Phase 0: Foundation crates (enable everything else)

#### `ralph-workspace` (~1.6K lines)

The `Workspace` trait is imported by **172 files** — the most pervasive abstraction in the codebase. It has near-zero outbound coupling (only `crate::interrupt` in tests). Extracting it creates a foundation crate that all other extractions can depend on without importing ralph-workflow.

**Measured coupling:**
- Outbound: None significant (pure trait + path constants + `ArtifactEnvelope` struct)
- Inbound: 172 files across every module

**What moves:** `Workspace` trait, `WorkspaceFs` impl, `MemoryWorkspace` test impl, `ArtifactEnvelope`, `DirEntry`, well-known path constants, `ValidationError`/`ErrorCode`.

**DI surface:** None needed — this IS the injected trait that other modules consume.

#### `ralph-executor` (~4.4K lines)

The `ProcessExecutor` trait is imported by 48 files. Its only outbound coupling is a single import: `agents::JsonParserType`. That enum can move to a shared types location or be injected.

**Measured coupling:**
- Outbound: `crate::agents::JsonParserType` (1 import)
- Inbound: 48 files (git_helpers, checkpoint, pipeline, phases, diagnostics)

**What moves:** `ProcessExecutor` trait, `RealProcessExecutor`, `MockProcessExecutor`, `AgentChild`/`AgentChildHandle`, `AgentSpawnConfig`, `SpawnedProcess`, `ChildProcessInfo`, `ProcessOutput`, process introspection (bfs, ps, macos).

**DI surface:** `JsonParserType` must be defined in a shared location (ralph-workspace or a tiny ralph-common crate) or passed as a generic/string.

### Phase 1: Leaf modules (low outbound coupling, immediate wins)

#### `ralph-json-parser` (~23K lines)

The streaming NDJSON parsers for Claude, Codex, Gemini, and OpenCode.

**Measured coupling (exhaustive):**
- `crate::config::Verbosity` — affects output verbosity (enum with 5 variants)
- `crate::logger::{Colors, CHECK, CROSS, ColorEnvironment}` — terminal color/symbol output
- `crate::common::{truncate_text, is_sensitive_key}` — 2 utility functions
- `crate::workspace::Workspace` — **only in Codex parser** for workspace operations

That's it. Four coupling points, all trivially injectable.

**DI surface:**
- Define a `trait ParserConfig { fn verbosity(&self) -> Verbosity; fn colors(&self) -> Colors; }` or simply accept these as constructor parameters (they're already passed to `new()`)
- Move `Verbosity` and `Colors` to ralph-workspace or ralph-common (they're simple enums/structs)
- Inline or copy `truncate_text` and `is_sensitive_key` (pure utility functions, ~20 lines total)
- Codex parser's `Workspace` usage: already depends on ralph-workspace after Phase 0

**Internal structure is self-contained:** StreamingSession, DeltaDeduplicator, HealthMonitor, DeltaRenderer, IncrementalNdjsonParser, and all parser-specific types are internal to json_parser with no external references.

#### `ralph-language-detector` (~1.9K lines) and `ralph-guidelines` (~2.6K lines)

These are the most trivially extractable modules in the codebase.

**language_detector coupling:** `crate::workspace::{Workspace, WorkspaceFs, MemoryWorkspace}` — nothing else.

**guidelines coupling:** `crate::language_detector::ProjectStack` — nothing else.

**Combined inbound:** 13 files depend on language_detector, 4 on guidelines.

These are small but zero-risk extractions that prove the pattern and reduce the monolith incrementally. After Phase 0, language_detector depends only on ralph-workspace.

### Phase 2: Modules with moderate coupling

#### `ralph-files` (~27K lines)

File management, backup, integrity, LLM output extraction, XSD validation.

**Measured coupling (exhaustive):**
- Outbound: `crate::workspace::Workspace`, `crate::logger::Logger`, `crate::common` utilities, `crate::reducer::state::pipeline::ExcludedFile` (single type reference)
- Inbound: 47 files (19 in reducer/boundary, 5 in rendering, 5 in phases, 5 in app, 3 in git_helpers)

**Why this is Phase 2, not Phase 1:** The `ExcludedFile` reference from `reducer::state` creates a small but real coupling to the reducer's type system. Also, `Logger` would need to be either extracted or injected. The 44-function public API surface (`llm_output_extraction` alone re-exports 40+ functions) means the bridge adapter layer will be substantial.

**DI surface:**
- `Workspace` → from ralph-workspace (Phase 0)
- `Logger` → define a `trait Log` in the crate or extract logging
- `ExcludedFile` → move to ralph-workspace as a shared type, or accept as generic

**Carries heavyweight deps:** `quick-xml`, `jsonschema`. Extracting files isolates these from the critical path for non-file changes.

#### `ralph-git` (~16K lines)

Git operations wrapping libgit2.

**Measured coupling (exhaustive):**
- Outbound: `crate::workspace::{Workspace, WorkspaceFs}`, `crate::executor::ProcessExecutor`, `crate::files::file_contains_marker`, `crate::common::domain_types::GitOid`, `crate::logger::Logger`
- Inbound: 9 direct consumer files (app, cli, pipeline, reducer)

**Why this is Phase 2:** Depends on files module (`file_contains_marker` for hook verification) and executor (for `ProcessExecutor` in identity resolution). After Phase 0 extracts workspace and executor, the remaining coupling is:
- `files::file_contains_marker` — a single function that reads a file and checks for a string. Can be inlined (~5 lines) or the dependency inverted via a trait.
- `GitOid` — a newtype wrapper around `String`. Move to ralph-workspace or ralph-common.
- `Logger` — same as files module.

**Carries heavyweight dep:** `git2` (libgit2 C binding). Extracting git_helpers isolates this expensive compilation from non-git changes.

### Phase 3: Hard extractions (require type foundation work)

#### `ralph-prompts` (~17K lines) — **CORRECTION: This is NOT low coupling**

The initial draft described prompts as "low coupling, essentially pure functions." That was wrong. Exhaustive analysis reveals:

**Measured coupling (exhaustive):**
- `crate::checkpoint::{ResumeContext, execution_history::{ExecutionHistory, StepOutcome, IssuesSummary, ModifiedFilesDetail}, state::PipelinePhase}` — resume note generation depends on checkpoint types
- `crate::agents::session::{AgentSession, SessionDrain, Capability, CapabilitySet, PolicyFlag, PolicyFlagSet}` — capability-driven template variable generation
- `crate::agents::tool_manifest::visible_mcp_tool_names` — MCP tool name rendering in prompts
- `crate::agents::AgentRegistry` — re-exported for backward compatibility
- `crate::workspace::Workspace` — file reading for conflict resolution
- `crate::executor::ProcessExecutor` — git operations in rebase conflict prompts

**Inbound:** 50+ files depend on prompts. Key types consumed everywhere: `TemplateContext` (100+ imports), `PromptScopeKey` (15+), `PromptHistoryEntry` (20+), `SessionCapabilities` (25+).

**Why this can't be Phase 1:** The template engine's pure rendering core (template_parsing.rs) IS extractable. But the prompt generation functions (`generate_commit_prompt`, `build_conflict_resolution_prompt`, capability-driven variable injection) depend on checkpoint types, agent session types, and execution history — all of which live deep in ralph-workflow.

**Extraction strategy:** Split into two layers:
1. A `ralph-template-engine` crate containing only the pure parsing/rendering machinery (~5K lines, zero coupling)
2. Prompt generation stays in ralph-workflow, consuming the template engine

Or defer until a `ralph-types` crate exists that can hold the shared session/checkpoint types.

#### `ralph-checkpoint` (~7K lines) — **NOT self-contained**

The initial draft described checkpoint as "self-contained." That was wrong.

**Measured coupling (exhaustive):**
- Depends on: `agents::AgentRegistry`, `config::{Config, ReviewDepth}`, `common::GitOid`, `executor::ProcessExecutor`, `logger::Logger`, `prompts::PromptHistoryEntry`, `reducer::state::{PromptInputsState, PromptPermissionsState}`, `workspace::Workspace`
- **Bidirectional with reducer:** `state/pipeline/checkpoint_conversion.rs` imports FROM checkpoint, AND checkpoint serializes `PipelineState`. This is a mutual dependency.

**Why this is Phase 3:** Checkpoint cannot be extracted without first extracting the state types it shares bidirectionally with the reducer. This requires a `ralph-types` crate.

#### `ralph-types` (extracted from reducer state/event/effect definitions)

Prerequisite for checkpoint extraction and eventual reducer decomposition.

**What would move:** `PipelinePhase`, `PipelineEvent`, `PipelineState`, `Effect`, `EffectResult`, `AgentRole`, `SessionDrain`, `Capability`, `CapabilitySet`, and the other enums/structs shared across module boundaries.

**Risk:** This is the hardest extraction because it touches every module. The type surface must be designed carefully to avoid becoming a kitchen-sink crate.

---

## What NOT to Extract

Based on exhaustive coupling analysis, these modules should stay in ralph-workflow:

- **`app/` and `cli/`** — Integration wiring layer. They exist to compose everything.
- **`pipeline/`** — Pure consumer (imports 10+ modules, defines only Timer/PipelineRuntime/AgentPhaseGuard). Extraction adds a crate without reducing ralph-workflow's size meaningfully.
- **`phases/`** — Glue layer between reducer and everything else. `PhaseContext` is imported by 44 files and holds references to config, registry, logger, workspace, audit trail. This is inherently the composition root.
- **`reducer/`** (for now) — 80K lines importing from 23+ modules. Boundary handlers call into phases, agents, files, prompts, checkpoint, workspace. The pure `reduce()` function and `determine_next_effect()` orchestration are extractable in theory, but the boundary handlers cannot move without most of ralph-workflow coming along. Requires `ralph-types` first.
- **`agents/`** (for now) — 165 inbound consumers. Self-contained outbound coupling, but the public API surface is massive (AgentRegistry, AgentSession, FallbackConfig, AgentDrain, SessionDrain, Capability, PolicyFlag, etc.). Extracting it would require moving dozens of types to a shared crate.
- **`config/`** — 74 inbound consumers. Depends on `agents::fallback::{FallbackConfig, AgentDrain}`, creating a circular extraction problem with agents.
- **`rendering/`** — Only 2.8K lines, 4 consumers. Overhead of a new crate exceeds benefit.
- **`boundary/`** — Thin re-export facade, not a real module.

---

## Dependency Graph (Target State)

```
ralph-workflow (app, cli, pipeline, phases, reducer, agents, config, 
                checkpoint, prompts, boundary, rendering)
  ├── ralph-json-parser ──► ralph-workspace
  ├── ralph-files ──────────► ralph-workspace
  ├── ralph-git ────────────► ralph-workspace, ralph-executor
  ├── ralph-guidelines ─────► ralph-language-detector
  ├── ralph-language-detector ► ralph-workspace
  ├── ralph-executor ───────► ralph-workspace (for shared types only)
  ├── ralph-workspace        (foundation: Workspace trait, path constants)
  ├── mcp-server ───────────► test-helpers
  └── test-helpers
```

All arrows point downward. No extracted crate depends on ralph-workflow. Cross-crate dependencies exist only where there is a genuine domain relationship (guidelines needs language_detector; git needs executor and workspace).

---

## Build Impact Analysis

### Current State (from `cargo build --timings`)

```
t=0s   ────────────── External deps compile in parallel ──────────────
       tokio (6.3s), regex-automata (8.3s), reqwest (6.8s), ...
t=28s  ────────────── Sequential critical path begins ────────────────
       jsonschema (6.5s, blocked on reqwest)
t=35s  ralph-workflow (15.3s, blocked on jsonschema + all deps)
t=50s  ralph binary (0.4s)
t=52s  ────────────── Done ───────────────────────────────────────────
```

### After Phase 0+1 Extractions

```
t=0s   ────────────── External deps compile in parallel ──────────────
       tokio, regex-automata, reqwest, git2, ...
t=2s   ralph-workspace (tiny, unblocks everything)
t=3s   ralph-executor, ralph-language-detector (parallel, unblocked)
t=4s   ralph-json-parser (parallel with deps, ~3s estimated)
       ralph-guidelines (parallel, tiny)
t=28s  ────────────── Sequential critical path ───────────────────────
       jsonschema (still on critical path if files not yet extracted)
t=35s  ralph-workflow (~10-12s, reduced by ~25% from extracted code)
t=47s  ────────────── Done ───────────────────────────────────────────
```

**Clean build improvement:** Modest (~5s), because external deps dominate.

**Incremental build improvement (the real win):** This is where extraction pays off. Today, changing a parser file invalidates 280K lines. After extraction:
- Parser change → recompile ~23K-line parser crate + thin bridge in ralph-workflow
- Language detector change → recompile ~1.9K crate only
- Git operation change → recompile ~16K crate + thin bridge

### After Phase 2 (files + git extracted)

Extracting `ralph-files` moves `jsonschema` and `quick-xml` off the critical path for non-file changes. Extracting `ralph-git` moves `git2` off the critical path for non-git changes.

```
t=0s   ────── Everything compiles in parallel ──────
       ralph-workspace, external deps
t=3s   ralph-executor, ralph-json-parser, ralph-language-detector
t=4s   ralph-files (carries jsonschema, quick-xml)
       ralph-git (carries git2)
       ralph-guidelines
t=10s  ralph-workflow (~8s, significantly reduced)
t=18s  ──── Done ────
```

This is where the clean build improvement becomes significant — the critical path shortens because heavyweight deps compile in parallel in their own crates.

---

## Sequencing

1. **Phase 0:** Extract `ralph-workspace` and `ralph-executor` (foundation crates, enable all subsequent extractions)
2. **Phase 1:** Extract `ralph-json-parser`, `ralph-language-detector`, `ralph-guidelines` (leaf modules, immediate wins, can be done in parallel)
3. **Phase 2:** Extract `ralph-files` and `ralph-git` (moderate coupling, isolate heavy C-binding deps)
4. **Phase 3:** Evaluate `ralph-types` for shared state/event definitions; extract pure template engine from prompts
5. **Deferred:** Reducer decomposition, agents extraction, checkpoint extraction (require `ralph-types`)

Each extraction is a standalone PR. No big-bang refactor. Each PR follows the pattern:
1. Create new crate with extracted code
2. Define traits for injected capabilities
3. Create bridge/adapter module in ralph-workflow
4. Update ralph-workflow imports to use the new crate
5. Verify all tests pass

---

## Alternative/Complementary Strategies

### Feature-gating (orthogonal to extraction)

Even without extracting crates, feature-gating `quick-xml`, `jsonschema`, `notify`, `ureq`, and `frunk` behind cargo features would let developers skip compiling unused heavyweight deps during focused work. This is lower-effort than crate extraction and can be done first.

### Test infrastructure

Tests are already well-split into 5 independent binaries. No changes needed there. However, all 5 binaries depend on ralph-workflow — extracted crates with their own test suites reduce the blast radius of test recompilation.

### Build caching

The remote build server (`rw-build-server`) already provides shared compilation caching. Crate extraction improves cache hit rates because smaller crates change less frequently and their compiled artifacts remain valid longer.
