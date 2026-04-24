# Ralph Workflow

> An opinionated AI agent orchestration framework for dependable unattended execution.

Ralph Workflow is for developers who want more than an AI chat window.
It is built for teams and power users who want AI to execute real software-delivery work with **explicit guardrails, verification, and configurable workflow policy**.

Most AI dev tools are good at helping in the moment. Fewer are good at running unattended without constant supervision, hidden behavior, or hand-wavy success criteria. Ralph exists to make that workflow more dependable.

Ralph Workflow began as a take on the Ralph loop and has evolved into a configurable Python framework for **bounded autonomy**: opinionated about process, configurable in how that process is expressed.

## Why this helps

Ralph is useful when you want AI to do more of the work **without asking you to trust it blindly**.

It helps by giving you:

- **A structured workflow instead of a loose prompt loop** — planning, development, review, fix, checkpoint, and completion are modeled explicitly.
- **Configurable policy instead of hidden behavior** — phase routing, agent chains, MCP access, artifact expectations, and recovery rules are expressed in config.
- **Verification instead of wishful success** — important phases are judged by evidence, artifacts, and real checks, not just a zero exit code.
- **Recovery instead of brittle failure** — retries, fallover, checkpoint/resume, and offline-aware pause/resume are first-class parts of the runtime.
- **A maintainable operating model instead of one-off magic** — you can adapt the workflow to your team without giving up an opinionated delivery structure.

If you want AI to brainstorm in a sidebar, there are many tools for that.
If you want AI to operate within a repeatable, inspectable engineering workflow, that is where Ralph fits.

## What Ralph is

The maintained product in this repository is the Python package in `ralph-workflow/`.
It provides:

- layered user-global and project-local configuration
- policy-driven phase orchestration
- agent chains with retry and fallover behavior
- explicit artifact contracts and Markdown handoffs
- MCP integration, including a standalone `ralph-mcp` runtime
- checkpoint/resume, recovery budgets, and optional parallel worktree execution

This repository also keeps legacy design material from the retired Rust implementation, but the current product is the Python package in `ralph-workflow/`.

## Who it is for

Ralph is a good fit if you want:

- unattended or semi-unattended AI execution with clear boundaries
- configurable workflow behavior that still has a strong opinion about process
- auditable handoffs, explicit artifacts, and predictable recovery behavior
- a tool that fits disciplined engineering workflows rather than bypassing them

Ralph is probably not the best fit if you want:

- instant one-shot code generation with minimal setup
- opaque agent behavior that "just does something"
- a general-purpose chat assistant as the primary experience

## What is current

- **Maintained product**: `ralph-workflow/`
- **Package name**: `ralph-workflow`
- **CLI entry points**: `ralph`, `ralph-mcp`
- **Primary toolchain**: Python 3.12+, `ruff`, `mypy`, `pytest`, `hatch`

## Install

### From PyPI

```bash
pip install ralph-workflow
ralph --help
```

### With pipx

```bash
python -m pip install pipx
python -m pipx ensurepath
pipx install ralph-workflow
ralph --help
```

### From this repository

```bash
cd ralph-workflow
python -m pip install -e ".[dev]"
ralph --version
```

## Quick start

```bash
cd /path/to/your/project
ralph --init feature-spec
# edit PROMPT.md
ralph
```

`ralph --init` seeds the project-local framework files under `.agent/`, including workflow policy, agent-chain, MCP, and artifact configuration. On first run Ralph also bootstraps user-global config in `~/.config/` and can regenerate the defaults later if you want to reset the setup.

## How Ralph works

Ralph still reflects the original Ralph-loop philosophy, but the maintained package exposes that philosophy through configurable orchestration primitives instead of a single fixed loop.

### 1. Layered configuration

Ralph loads embedded defaults, then user-global config, then project-local config, then CLI overrides. The main surfaces are:

- `~/.config/ralph-workflow.toml` — user-global runtime defaults
- `~/.config/ralph-workflow-mcp.toml` — user-global MCP and web-search config
- `.agent/ralph-workflow.toml` — project-local runtime overrides
- `.agent/mcp.toml` — project-local MCP overrides
- `.agent/agents.toml` — agent chains and drain bindings
- `.agent/pipeline.toml` — phase graph and orchestration policy
- `.agent/artifacts.toml` — artifact contracts and handoff expectations

Override order is: **CLI flags → project-local config → user-global config → bundled defaults**.

### 2. Policy-driven workflow orchestration

The framework exposes configurable workflow structure instead of baking one rigid flow into code. Current policy surfaces include:

- phase graph and transitions
- terminal and loopback phases
- post-commit routing
- embedded analysis decisions
- parallel execution limits
- per-drain artifact expectations

That means you can tune how planning, development, review, fix, checkpoint, and completion behavior flow through the pipeline rather than treating the tool as an opaque automation box.

### 3. Agent chains and fallback behavior

Agent execution is configurable through ordered chains and drain bindings. Ralph can route a phase through one agent, retry it within budget, then fall over to the next configured agent when needed. The maintained package also supports provider/model fallback and dynamic agent forms such as model-qualified agent aliases.

### 4. Artifact contracts and handoffs

Ralph treats important phase outputs as explicit artifacts, not just process exit codes. Structured JSON artifacts are used for orchestration logic, while mirrored Markdown handoffs keep results readable for users and downstream agents.

### 5. MCP bridge and tool runtime

The package includes both the main orchestration CLI and the standalone `ralph-mcp` server. MCP behavior is configurable through TOML, including upstream servers, web-search backends, access policy, and opt-in multimodal image reading.

### 6. Recovery, resume, and parallel work

Recovery is a first-class part of the framework. Ralph supports checkpoint/resume flows, failure classification, retry budgets, connectivity-aware pause/resume behavior, and optional parallel worktree fan-out when the plan yields multiple work units.

## User-facing workflows

The maintained CLI covers more than a single run command. Depending on the workflow, Ralph supports:

- project initialization and config bootstrap
- unattended runs from `PROMPT.md`
- config and MCP diagnostics
- checkpoint inspection and resume flows
- cleanup of orchestration leftovers such as orphaned worktrees
- commit-message generation/apply/show plumbing
- standalone MCP runtime via `ralph-mcp`

For the installable package's fuller command and behavior reference, see `ralph-workflow/README.md`.

## Verification

```bash
cd ralph-workflow
make verify
```

Verification passes only when all required checks succeed with **no ERROR/WARNING diagnostics**.

That runs the current Python verification path:

- `ruff check ralph/ tests/`
- `uv run python -m mypy ralph/`
- `uv run --extra docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html -W --keep-going`
- `uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n 8 --cov=ralph --cov-report=term-missing --cov-report=html --cov-fail-under=80`

Useful local narrowing commands:

- `make docs` — build Sphinx HTML into `docs/sphinx/_build/html` with warnings treated as errors
- `make test` — full suite without coverage
- `make test-unit` — `tests/` excluding `tests/integration/`
- `make test-integration` — `tests/integration/` only
- `make dead-code` — separate Vulture audit while the dead-code backlog is still being cleaned up

## Repository map

- `ralph-workflow/README.md` — package install, configuration, orchestration, and API overview
- `ralph-workflow/CONTRIBUTING.md` — Python contributor workflow
- `docs/agents/verification.md` — required verification commands
- `docs/README.md` — current vs legacy documentation map

## Legacy documentation status

Large parts of `docs/`, `CODE_STYLE.md`, and older plans/RFCs were written for the retired Rust implementation. They are kept for migration history and background context, not as the source of truth for the Python package unless a document explicitly says it has been refreshed for Python.

For current behavior, prefer:

1. `ralph-workflow/README.md`
2. `ralph-workflow/CONTRIBUTING.md`
3. `docs/agents/verification.md`
4. the Python source and docstrings under `ralph-workflow/ralph/`

## License

Licensed under AGPL-3.0-or-later.
