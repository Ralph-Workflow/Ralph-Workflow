# Ralph Workflow

> **Unattended AI coding pipelines you actually control.** Mix Claude, Codex, OpenCode, and any model you want — at every phase.

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Most AI coding tools assume you'll pick one vendor and stay there. Ralph Workflow doesn't. You decide which agent runs which phase — Claude Code plans, OpenCode with a cheap model writes the implementation, Codex reviews it, OpenCode fixes what review caught, and Codex re-reviews until it's clean. All unattended. All auditable. All in your git history.

Everything is configurable: prompts, agent chains, phase routing, retry budgets, recovery rules, verification policy. Express it in repo-local TOML files. Diff them. Share them. Run them tomorrow exactly the same way you ran them today.

## A pipeline you actually own

Ralph's runtime config is split by concern: `ralph-workflow.toml` for agent definitions, chain order, drain bindings, and general workflow settings; `pipeline.toml` for the phase graph; and `artifacts.toml`/`mcp.toml` for the rest. Under the hood, a setup looks more like this:

```toml
# .agent/ralph-workflow.toml
[general]
developer_iters = 5
reviewer_reviews = 2
max_retries = 3

[agent_chains]
planning = ["claude/opus"]
development = ["opencode/minimax/MiniMax-M2.7-highspeed", "claude/sonnet", "codex"]
review = ["codex", "claude/sonnet"]
fix = ["opencode/zai-coding-plan/glm-5"]
commit = ["claude"]

[agent_drains]
planning = "planning"
development = "development"
analysis = "planning"
review = "review"
fix = "fix"
commit = "commit"
```

```toml
# .agent/pipeline.toml
[phases.planning]
drain = "planning"
prompt_template = "planning.jinja"
[phases.planning.transitions]
on_success = "development"

[phases.development]
drain = "development"
prompt_template = "developer_iteration.jinja"
[phases.development.transitions]
on_success = "review"

[phases.review]
drain = "review"
prompt_template = "review.jinja"
[phases.review.transitions]
on_loopback = "fix"
on_success = "complete"

[phases.fix]
drain = "fix"
prompt_template = "fix_mode.jinja"
[phases.fix.transitions]
on_success = "review"

entry_phase = "planning"
terminal_phase = "complete"
```

Frontier models where reasoning matters. Cheap models where they're enough. Loop review and fix until the reviewer signs off. The whole pipeline policy lives in your repo, not in a vendor's cloud.

## Why this exists

**No single vendor will build this for you.** Anthropic isn't going to ship "use Codex for review." OpenAI isn't going to ship "use Claude for planning." Cursor isn't going to optimize for routing work to competitor APIs. The orchestration layer that sits *across* vendors has to come from outside any of them.

**Cost arbitrage is real.** A long unattended run on a single frontier vendor can burn through a meaningful AI budget. Routing planning and review to capable frontier models, but development and fix work to cheaper models, frequently cuts that cost dramatically. You decide where capability matters and where price matters.

**Configurable beats opinionated.** Teams have opinions about how planning should work, what reviewers should check, how fixes should be applied, what counts as "done." Generic agent products force one workflow. Ralph encodes yours.

## What you get

- **Vendor-neutral orchestration.** Anthropic, OpenAI, OpenCode + any model it wraps — all behind one config surface.
- **Real unattended execution.** Walk away. Come back to a clean diff and a review, not a process to babysit.
- **Auditable by default.** Every iteration commits. Every phase produces structured artifacts. Run history lives in `.agent/logs/`.
- **Recovery built in.** Checkpoint and resume, failure classification, retry budgets, connectivity-aware pause/resume.
- **Context isolation.** Every iteration starts fresh from `PROMPT.md`. No drift. No accumulating noise.
- **Parallel work.** Optional worktree fan-out for independent work units.
- **MCP-native.** First-class MCP server support, plus a standalone `ralph-mcp` runtime.

## Install

### From PyPI

```bash
pip install ralph-workflow
ralph --help
```

### With pipx (recommended for CLI use)

```bash
pipx install ralph-workflow
ralph --help
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow
cd Ralph-Workflow/ralph-workflow
pip install -e ".[dev]"
ralph --version
```

Requires Python 3.12+.

## Quick start

```bash
cd /path/to/your/project
ralph --init                # seeds .agent/ with config templates
$EDITOR PROMPT.md           # write your task spec
ralph                       # walk away
```

Ralph plans, develops, reviews, and commits while you do something else. Pick up from a clean diff when you return.

### Pipeline depth presets

```bash
ralph -Q     # quick: small fixes, single iteration
ralph        # standard: most features and tasks
ralph -T     # thorough: complex refactors, overnight runs
```

More presets and custom pipelines in the [docs](https://ralphworkflow.com/docs).

## Compatible agents

Ralph Workflow ships with three built-in transport families and several model-qualified naming forms on top of them.

| Identifier form | What it means | Example |
|---|---|---|
| `claude` | Claude Code using your currently selected Claude Code model/profile | `planning = ["claude"]` |
| `claude/<family>` | Force a Claude model family for that chain entry | `planning = ["claude/opus"]` |
| `codex` | OpenAI Codex CLI transport | `review = ["codex"]` |
| `opencode` | Base OpenCode transport | `development = ["opencode"]` |
| `opencode/<provider>/<model>` | OpenCode with an explicit provider/model target | `development = ["opencode/minimax/MiniMax-M2.7-highspeed"]` |
| `ccs/<alias>` | Claude Code Switch alias resolved dynamically | `planning = ["ccs/work"]` |
| custom `[agents.*]` name | Your own named agent definition in `ralph-workflow.toml` | `review = ["my-reviewer"]` |

### Built-in transports

| Transport | Strong at | Setup |
|---|---|---|
| Claude Code | Planning, complex reasoning, large context | `npm install -g @anthropic/claude-code` |
| Codex CLI | Structured review, cost-effective analysis | `npm install -g @openai/codex` |
| OpenCode | Multi-provider execution across OpenCode-supported models | [opencode.ai](https://opencode.ai) |
| CCS | Profile-based Claude Code switching and aliasing | Use `ccs/<alias>` directly |

### Model-qualified syntax

- `claude` uses whatever Claude Code model/profile you last selected.
- `claude/opus` or `claude/sonnet` force those model families for a specific phase.
- `opencode/<provider>/<model>` targets a concrete OpenCode provider/model path, for example:
  - `opencode/minimax/MiniMax-M2.7-highspeed`
  - `opencode/zai-coding-plan/glm-5`
- `ccs/<alias>` resolves dynamically, so names like `ccs/work` or `ccs/personal` work out of the box.

Mix per phase. Mix per repo. Mix per team. Change models when prices shift — change config, not tools.

## How it works

### Layered configuration

```
bundled defaults  →  user-global  →  project-local  →  CLI flags
```

The files that matter:

- `~/.config/ralph-workflow.toml` — your user-global runtime defaults
- `.agent/ralph-workflow.toml` — project-local main config override
- `.agent/pipeline.toml` — phase graph, transitions, entry/terminal phases, parallel policy
- `.agent/artifacts.toml` — what each phase must produce
- `.agent/mcp.toml` — MCP servers, web search, tool access

### Policy-driven phases

You define the phase graph. Ralph executes it. Phases can loop (review → fix → review), branch on analysis output, and terminate on configurable conditions. There's no hidden routing.

### Agent chains with fallback

Each phase has an ordered chain of agents. If the primary fails or hits a retry budget, Ralph falls over to the next. OpenCode model-qualified agents use `opencode/<provider>/<model>` syntax, so a chain can fall from `opencode/minimax/MiniMax-M2.7-highspeed` to `opencode/zai-coding-plan/glm-5` just like any other ordered fallback. Claude model tags are shorter: `claude` uses whatever Claude Code model/profile you last selected, while `claude/opus` or `claude/sonnet` force those model families for that phase.

### Artifact contracts, not exit codes

Phase success means "the artifact satisfies its contract," not "the process returned 0." Structured JSON artifacts drive orchestration; mirrored Markdown handoffs keep results readable for humans and downstream agents.

### Resume and parallel

Interrupt anytime. `ralph --resume` picks up from the last checkpoint. Parallel worktrees fan out independent work units when the plan supports it.

## When Ralph fits

- Multi-step coding tasks that don't fit in one prompt
- Refactors, test suites, docs, or features that take hours of execution
- Work where you want to walk away and come back to reviewed commits
- Teams that need cost-controlled or auditable agent execution
- Anyone tired of paying frontier-model rates for grunt work cheaper models handle fine

## When Ralph doesn't fit

- One-shot prompts you can answer interactively
- Pair-programming sessions where you want to steer in real time
- Tasks that finish manually before setup overhead pays off
- Workflows that need unpredictable mid-run human input

## Repository layout

- `ralph-workflow/` — the maintained Python package (this is the product)
- `ralph-workflow/README.md` — package-level reference: full CLI, config, API
- `ralph-workflow/CONTRIBUTING.md` — Python contributor workflow
- `docs/` — broader documentation; legacy material from the retired Rust implementation is kept for migration history but is not authoritative

For current behavior, prefer (in order):

1. `ralph-workflow/README.md`
2. `ralph-workflow/CONTRIBUTING.md`
3. `docs/agents/verification.md`
4. Source and docstrings under `ralph-workflow/ralph/`

## Verification

```bash
cd ralph-workflow
make verify
```

Runs the full check pipeline:

- `ruff check ralph/ tests/`
- `mypy ralph/`
- `sphinx-build -W` for docs
- `pytest tests/ -q -n 8 --cov=ralph --cov-fail-under=80`

Verification passes only when every required check succeeds with no ERROR/WARNING diagnostics.

Useful narrowing:

- `make docs` — Sphinx HTML, warnings as errors
- `make test` — full suite without coverage
- `make test-unit` — `tests/` excluding `tests/integration/`
- `make test-integration` — integration only

## Mirrors

- **Primary:** [Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
- **Mirror:** [GitHub](https://github.com/mistlight/Ralph-Workflow) *(auto-synced; issues open on Codeberg)*
- **Package:** [PyPI · ralph-workflow](https://pypi.org/project/ralph-workflow/)
- **Site:** [ralphworkflow.com](https://ralphworkflow.com)

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph generates belongs to you — no license encumbrance on outputs. Use it commercially. Use it privately. Use it however.Share
