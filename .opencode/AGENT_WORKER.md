# AGENT WORKER REFERENCE

Quick lookup: all background workers defined in `.opencode/opencode.json`.

> **`build` is the orchestrator, not a worker. Do not dispatch it as a background agent.**

---

## Naming Rule

Every worker has two variants:

| Situation | Use |
|---|---|
| Agent has **compilation errors** | `{name}-cargo` — has `cargo build/check/test` access |
| Agent has **only lint/clippy errors** | `{name}` — no cargo access needed |

The `-cargo` suffix goes in `subagent_type` only. It changes nothing else about the agent.

---

## Workers

| Worker | What it owns | Source paths |
|---|---|---|
| `workflow-gui` | Angular frontend UI | `ralph-gui/**` |
| `workflow-core` | App bootstrap, lib.rs, main.rs, CLI entry | `ralph-workflow/src/*` (root files) |
| `workflow-reducer` | State transitions, pipeline, checkpoints, phases | `ralph-workflow/src/reducer/**`, `pipeline/**`, `checkpoint/**`, `phases/**` |
| `workflow-execution` | Task executor, process/thread management | `ralph-workflow/src/executor/**`, `runtime/**` |
| `workflow-io` | File I/O, network I/O boundary | `ralph-workflow/src/io/**`, `files/**` |
| `workflow-workspace` | Workspace discovery, boundary interfaces | `ralph-workflow/src/workspace/**`, `boundary/**` |
| `workflow-git` | Git diff parsing, commit analysis, branch detection | `ralph-workflow/src/git_helpers/**` |
| `workflow-config` | Config loading, CLI argument parsing | `ralph-workflow/src/config/**`, `cli/**` |
| `workflow-app` | Main app coordination layer | `ralph-workflow/src/app/**` |
| `workflow-logging` | Log construction, log output sinks | `ralph-workflow/src/logging/**`, `logger/**` |
| `workflow-monitoring` | Metrics, diagnostics, error reporting | `ralph-workflow/src/monitoring/**`, `diagnostics/**` |
| `workflow-misc` | Shared types, platform code, rendering, templates | `ralph-workflow/src/common/**`, `platform/**`, `rendering/**`, `templates/**` |
| `workflow-future` | Benchmarks, interrupt, language detection, review metrics | `ralph-workflow/src/benchmarks/**`, `interrupt/**`, `language_detector/**`, `review_metrics/**` |
| `workflow-agents` | AI agent abstractions/integrations | `ralph-workflow/src/agents/**` |
| `workflow-prompts` | Prompt templates, guidelines | `ralph-workflow/src/guidelines/**`, `prompts/**` |
| `workflow-json` | JSON parsing and serialization | `ralph-workflow/src/json_parser/**` |
| `workflow-cloud` | Cloud service integrations | `ralph-workflow/src/cloud/**` |
| `test-helpers` | Shared test fixtures and utilities | `test-helpers/**` |
| `xtask` | Build automation scripts | `xtask/**` |
| `workflow-tests` | Integration and end-to-end tests | `tests/**` |
| `workflow-lints` | Custom dylint linting rules | `lints/**` |
| `workflow-docs` | Architecture docs, style guides, API docs | `docs/**` |

Each entry above also has a `-cargo` variant with the same name plus `-cargo` suffix (e.g. `workflow-reducer-cargo`).

---

## Universal Constraints (all workers)

- **Cannot** read or write `.opencode/**`
- **Cannot** run git commits, pushes, or destructive git commands
- **Cannot** spawn sub-agents (`task` denied)
- **Cannot** access the internet (`webfetch`/`websearch` denied)
- **Can** read `tmp/**` and `docs/**`
- **Can** read the full repo (glob/grep/list allowed everywhere except `.opencode/**`)
