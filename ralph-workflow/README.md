# Ralph Workflow (Python)

> Vendor-neutral AI coding workflow orchestration — unattended, auditable, and configured in your repo.

Ralph Workflow is a Python 3.12+ CLI package for running AI coding work through a configurable workflow.
You decide which agent runs which phase, keep the workflow configuration in repo-local TOML,
and let Ralph Workflow plan, implement, review, fix, and commit work for you.

The package exposes two entry points:

- `ralph` — the main CLI
- `ralph-mcp` — the standalone MCP server runtime

## What Ralph Workflow is

Ralph Workflow sits across AI coding vendors rather than locking you into one tool.
It can route work across Claude Code, Codex, OpenCode, and any OpenCode-wrapped model,
so you can use frontier models where reasoning matters and cheaper models where they are enough.

Key differentiators:

- **Vendor-neutral orchestration** — choose different agents for planning, development, review, fix, and commit
- **Cost arbitrage** — route frontier models to planning/review and cheaper models to development/fix
- **Unattended execution** — walk away and come back to a reviewed diff instead of babysitting an agent
- **Workflow config in repo** — phase graph, agent chains, retry budgets, and recovery rules live in versioned config
- **Recovery and verification discipline** — checkpoint/resume, failure classification, and evidence-based phase completion

## Install

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

Requires Python 3.12+.

### pipx

```bash
python -m pip install pipx
python -m pipx ensurepath
pipx install ralph-workflow
ralph --help
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
pip install -e .
ralph --version
```

## Quick start

```bash
cd /path/to/your/project
ralph --init
# edit PROMPT.md
ralph
```

`ralph --init` is the canonical form. Compatibility labels such as `default` are deprecated,
ignored, and no longer recommended in docs or scripts.

## First-run configuration

On first run, Ralph Workflow creates the standard project and user config files from bundled templates.

**User-global (created once, reused across projects):**
- `~/.config/ralph-workflow.toml` — main Ralph Workflow configuration
- `~/.config/ralph-workflow-mcp.toml` — MCP servers, web search, and web visit configuration

**Project-local (created by `ralph --init`, lives in your project directory):**
- `.agent/ralph-workflow.toml` — project-local main config override, including agent chains and drain bindings
- `.agent/mcp.toml` — project-local MCP override
- `.agent/pipeline.toml` — phase graph and orchestration settings
- `.agent/artifacts.toml` — MCP artifact contracts per drain

**Override precedence (highest to lowest):**
CLI flags → project-local (`.agent/`) → user-global (`~/.config/`) → bundled defaults

To reset configs from the bundled defaults (existing files are backed up to `<name>.bak`), run:

```bash
ralph --regenerate-config
```

Before your first real run, it is a good idea to validate your environment:

```bash
ralph --diagnose
```

## How a run works

When you run `ralph`, the workflow moves through a structured sequence of phases:

1. **Planning** — a planning agent reads `PROMPT.md` and produces a structured plan
2. **Development** — a developer agent implements the work
3. **Development analysis** — the workflow decides whether to iterate or continue
4. **Development commit** — changes are committed
5. **Review** — a reviewer agent inspects the result and produces issues if needed
6. **Review analysis** — the workflow decides whether to loop to fix or continue
7. **Fix** — a fix agent resolves issues found during review
8. **Review commit** — final changes are committed
9. **Complete** — the workflow ends successfully

If review finds significant problems, the review → fix cycle repeats up to the configured limit.

## Compatible agents

Ralph Workflow supports three built-in transport families and several naming forms on top of them.

| Identifier form | What it means | Example |
|---|---|---|
| `claude` | Claude Code using your currently selected Claude Code model/profile | `planning = ["claude"]` |
| `claude/<family>` | Force a Claude model family for that chain entry | `planning = ["claude/opus"]` |
| `codex` | OpenAI Codex CLI transport | `review = ["codex"]` |
| `opencode` | Base OpenCode transport | `development = ["opencode"]` |
| `opencode/<provider>/<model>` | OpenCode with an explicit provider/model target | `development = ["opencode/minimax/MiniMax-M2.7-highspeed"]` |
| `ccs/<alias>` | Claude Code Switch alias resolved dynamically | `planning = ["ccs/work"]` |
| custom `[agents.*]` name | Your own named agent definition in `ralph-workflow.toml` | `review = ["my-reviewer"]` |

Built-in transports:

| Transport | Strong at | Setup |
|---|---|---|
| Claude Code | Planning, complex reasoning, large context | `npm install -g @anthropic/claude-code` |
| Codex CLI | Structured review, cost-effective analysis | `npm install -g @openai/codex` |
| OpenCode | Multi-provider execution across OpenCode-supported models | [opencode.ai](https://opencode.ai) |
| CCS | Profile-based Claude Code switching and aliasing | Use `ccs/<alias>` directly |

## When Ralph Workflow fits

- Multi-step coding tasks that do not fit in one prompt
- Refactors, test suites, docs, or features that take longer unattended runs
- Work where you want to walk away and come back to reviewed commits
- Teams that need cost-controlled, auditable, or workflow-configured agent execution
- Anyone tired of paying frontier-model rates for grunt work cheaper models handle fine

## When it does not fit

- One-shot prompts you can answer interactively
- Pair-programming sessions where you want to steer in real time
- Tasks that finish manually before setup overhead pays off
- Workflows that need unpredictable mid-run human input

## Standalone MCP runtime

The package also ships `ralph-mcp`, a standalone MCP HTTP server runtime:

```bash
ralph-mcp --help
```

Use it when you want Ralph Workflow's MCP tool surface without running the full `ralph` pipeline.

## Built-in web tools

### Web search (`web_search`)

Enabled by default. Uses a multi-backend fallback chain (ddgs, Tavily, Brave, Exa, SearXNG).
Configure via `[web_search]` in `mcp.toml`.

### URL fetching (`visit_url`)

A built-in `visit_url` tool fetches a single HTTP/HTTPS page and returns readable extracted text.
Requires the optional extras:

```bash
pip install "ralph-workflow[web-visit]"
```

Configure via `[web_visit]` in `mcp.toml`.
See [`docs/mcp/web-visit.md`](docs/mcp/web-visit.md) for the full reference.

For multi-page or JavaScript-rendered crawling, wire in [Crawl4AI](https://docs.crawl4ai.com/)
as an upstream MCP server — see [`docs/mcp/mcp-servers.md`](docs/mcp/mcp-servers.md).

## Multimodal MCP support (opt-in)

Ralph Workflow supports image-reading MCP tools via `read_image`. This feature is disabled by default.

Enable it in `.agent/mcp.toml`:

```toml
[media]
enabled = true
max_inline_bytes = 5242880  # 5 MiB default
```

When enabled:
- supported formats are PNG, JPEG, GIF, and WebP
- `read_image` only appears for clients that declare multimodal/image/media capability
- text-only clients keep the pre-multimodal tool set unchanged

## Parallel mode

When the planning phase produces two or more work units, Ralph Workflow can fan development out across
multiple workers in parallel in the same checkout.

Quick configuration:

```toml
[pipeline.parallel_execution]
max_parallel_workers = 4
max_work_units = 50
```

See [`docs/sphinx/parallel-mode.md`](docs/sphinx/parallel-mode.md) for the full guide.

## Recovery

Ralph Workflow treats failure recovery as a first-class concern. It supports checkpoint/resume,
failure classification, retry budgets, and connectivity-aware pause/resume behavior.

See [`docs/sphinx/recovery.md`](docs/sphinx/recovery.md) for the full guide.

## Long-content display

When agent output gets large, Ralph Workflow keeps the terminal readable by summarizing oversized content.
The deterministic headline summary layer is **enabled by default** and activates once content exceeds **4000** display cells.
That summary appears before the condensed output and gives you a stable, deterministic headline instead of making you scroll through a giant block.

If no clean headline can be extracted, Ralph Workflow shows the placeholder **`(no headline available)`**.
Inline summary lines are capped at **200** characters, and streaming end-line summaries are capped at **120** characters.

To disable the deterministic headline layer, use any of these values for `RALPH_LONG_CONTENT_SUMMARY`:
`0`, `false`, `no`, or `off`.
It is already on by default, so you do not need an "enable" value for it.

Ralph Workflow also supports an optional AI-generated summary layer labelled **`↳ ai-summary:`**.
That layer is controlled separately through the `RALPH_LONG_CONTENT_AI_SUMMARY` opt-in environment variable.

## Documentation

The full documentation lives in `docs/sphinx/` and is published under `/docs` at <https://ralphworkflow.com>.
Useful pages:

- [`docs/sphinx/getting-started.md`](docs/sphinx/getting-started.md) — step-by-step first-run walkthrough
- [`docs/sphinx/quickstart.md`](docs/sphinx/quickstart.md) — install, init, and run in five minutes
- [`docs/sphinx/concepts.md`](docs/sphinx/concepts.md) — phases, drains, agents, MCP artifacts, checkpoints
- [`docs/sphinx/cli.md`](docs/sphinx/cli.md) — all CLI flags and sub-commands
- [`docs/sphinx/configuration.md`](docs/sphinx/configuration.md) — config files, precedence, and FAQ
- [`docs/sphinx/reference.md`](docs/sphinx/reference.md) — operator-facing reference index
- [`docs/sphinx/recovery.md`](docs/sphinx/recovery.md) — failure classification, retry budgets, and recovery behavior
- [`docs/sphinx/parallel-mode.md`](docs/sphinx/parallel-mode.md) — same-checkout parallel execution for multi-work-unit plans
- [`docs/sphinx/troubleshooting.md`](docs/sphinx/troubleshooting.md) — common issues and FAQ

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs.
Use it commercially. Use it privately. Use it however you want.
