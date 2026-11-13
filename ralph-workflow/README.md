# ralph-workflow — the AI agent orchestrator and software factory

**The operating system for autonomous coding.**

**Multiple coding agents. Separate worktrees. One mainline.**

Coding agents write code fast. The handoffs around them cap throughput:
babysitting sessions, resolving conflicts, re-running checks, waiting in
review queues.

Ralph Workflow is a free, open-source AI agent orchestrator and software
factory for developers and teams who already have a coding agent: you
supply the specification and the checks you trust, and it runs plan, build,
verify, and fix as a configurable workflow graph across Git worktrees,
returning integrated, checked software on one mainline. At the core is the
simple Ralph loop: plan → build → verify → fix. Around it: agent review,
retries, fallback agent chains, durable handoffs, interruption recovery,
and Git integration.

The factory terms are literal: your specification and checks are the
input, phases are the stations, your checks are the quality gates, agent
chains are the interchangeable workers, worktrees are the parallel
lines, and integration is shipping to the mainline. See
[concepts](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/concepts.md).

**Ralph Workflow manages the harness, not the models.** A harness of
harnesses: coding agents such as Claude Code, Codex, and Cursor are
harnesses around models, and Ralph Workflow is the workflow, review,
recovery, and Git layer around those agents. There is no vendor lock-in:
nine built-in agent backends ship with it, and agent chains can mix them
and fall back to the next when an attempt fails. Switching or combining
vendors is a configuration change, not a rewrite. Ralph Workflow needs no
API key of its own and sells no model access; it launches the agent CLI you
installed and authenticated, so whatever login or subscription that agent
supports is what runs. Each agent defines its own authentication (a login,
an API key, or either), and the agent's vendor determines model access,
rate limits, and cost.

Every default ships with the package (workflow, agent chains, tools,
artifact contracts, retries, recovery, Git sync), and `ralph --init` seeds
your setup, so there is no setup project before the first useful run:
productive on day 1, preconfigured, and yours to tweak. See the
[bundled defaults](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md#bundled-defaults).

It runs on your machine: local, free, AGPL-3.0-or-later, no account, no
hosted service. Your specifications, configuration, and Git history stay
yours. Run one agent by hand and you are the scheduler, reviewer, and
merger; run a plain loop and it repeats one prompt; Ralph Workflow runs
the graph for you and lands checked work on one mainline. Move beyond
pull requests: review and integration happen inside the execution graph.
You define the outcome and the checks; Ralph Workflow runs the workflow;
you judge the software.

It fits work with a clear finish line and checks you trust; it is not
(yet) for open-ended exploration or repositories with no reliable tests
or checks. If that is your repository, build the finish line first:
write down what "done" means and add a test or check you trust, then
come back.

## Install

**Requirements:** Python 3.12 or newer, Git, `pipx`, and one coding agent
CLI you have already installed and authenticated.

```bash
pipx install ralph-workflow
ralph --version
```

`pip install ralph-workflow` in a Python 3.12+ virtual environment also
works.

From a source checkout instead? See [CONTRIBUTING.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/CONTRIBUTING.md#dev-build-vs-stable-build).

## First run

New to Ralph Workflow? [START_HERE.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/START_HERE.md)
walks through a first serious run.

In the git repository you want Ralph Workflow to work on:

```bash
ralph --init          # user-global config, starter PROMPT.md, detected agents
ralph --list-agents   # the agent CLIs Ralph Workflow can see
ralph --diagnose      # pre-flight checks, every line should be green
```

Then edit `PROMPT.md`: state the outcome and the checks you expect, and
remove the `<!-- ralph:starter-prompt ... -->` marker at the top.
Ralph Workflow refuses to run while that sentinel remains. A small first
task looks like this:

```markdown
# Goal
Reject an empty email address in the signup validator with a clear error.

## Acceptance criteria
- An empty email is rejected before a user record is created.
- Valid signup behavior and public interfaces stay unchanged.
- Tests cover both the rejected input and a valid signup.

## Verification
- Run python -m pytest and fix any failures.
```

Start the run with `ralph`. A successful first run completes with the
changes committed in a worktree and either integrated onto the mainline or
reported with the evidence it produced. Then read the summary of what
changed and which checks passed.

## Built-in agent backends

Nine backends ship with Ralph Workflow. Authenticate the CLI yourself;
Ralph Workflow uses each agent's own login or key. For Cursor and AGY,
which run in a private per-run home, it copies your existing local login
files into that home so the agent can use them; see the
[agent CLI lifecycle](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/agents.md).

| Backend | CLI | Authentication | Transport and caveats |
|---|---|---|---|
| **Claude Code** | `claude` | `claude login` or an Anthropic API key | Interactive PTY; the reference agent. |
| **Claude Code (headless)** | `claude` | Same as `claude` | Same binary without a PTY. |
| **Codex** | `codex` | `codex login` or an OpenAI API key | Unattended `codex exec`; no session resume. |
| **OpenCode** | `opencode` | Provider-specific keys | Headless subprocess; model selection uses `-m <provider>/<model>`. |
| **Nanocoder** | `nanocoder` | Local-only TUI, no remote auth | Local-only TUI; a provider is required for model aliases. |
| **AGY (Google Anti Gravity)** | `agy` | `agy login` with a Google account | PTY transport; published model IDs change between releases. |
| **Pi** | `pi` | Provider configuration | Headless `pi --mode json <prompt>`. |
| **Cursor** | `agent` | `CURSOR_API_KEY` or a file-backed login | Headless `--print`; credentials come from file, not the Keychain, unless `AGENT_CLI_CREDENTIAL_STORE=memory` is set explicitly. If no valid Cursor auth file exists, Ralph Workflow copies your existing Cursor IDE login into Cursor's file-backed auth paths. |
| **Kimi Code** | `kimi` | an interactive `kimi` login wires the `managed:kimi-code` subscription provider | Headless; `-m` needs a fully configured model ID from `~/.kimi-code/config.toml`. |

Model strings differ per agent: see [agent compatibility](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/agent-compatibility.md).

## Independent work, shared mainline

Each run works in its own Git worktree and syncs with the configured
mainline at startup, after commits, and at phase boundaries. Integration
rebases the feature onto the mainline, routes conflicts to a resolution
agent chain, falls back to an endpoint merge when resolution fails, and
fast-forwards the mainline to the landed tip once Git proves the
integration finished. Local integration is on by default with `main` as
the default target; remote sync publishes to the configured remote when
one exists and can be turned off for local-only operation; recovery state
is durable, so an interrupted run resumes where it stopped.

Two limits: branch protections and required checks still apply, and
Ralph Workflow does not bypass them. And the worktree holding `main` is
integration infrastructure: Ralph Workflow may snapshot its uncommitted
content to a recoverable ref and reset the worktree. Keep active edits in
feature worktrees. Details: [auto-integration](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md#auto-integration).

## Make it yours

| File | What it configures | Reference |
|---|---|---|
| `ralph-workflow.toml` | agent chains, retries, and Git sync | [Configuration reference](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md) |
| `pipeline.toml` | phases, routing, and loopbacks | [Pipeline configuration](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md) |
| `mcp.toml` | MCP tool servers and web access | [MCP configuration](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/advanced-mcp-configuration.md) |
| `artifacts.toml` | artifact contracts and output types | [Artifact configuration](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/advanced-artifact-configuration.md) |

User-global (`~/.config/`) and project-local (`.agent/`) are separate
scopes: `ralph-workflow.toml` keeps one name in both, while `pipeline.toml`,
`mcp.toml`, and `artifacts.toml` are the project-local names for the
user-global `ralph-workflow-pipeline.toml`, `ralph-workflow-mcp.toml`, and
`ralph-workflow-artifacts.toml`.

## Skills

Ralph Workflow installs its skills through the built-in update mechanism:
artifact skills install into the project scope and are bound to the
pipeline's artifact types: each artifact type carries at most one skill.
Three universal skills
(`test-driven-development`, `systematic-debugging`,
`verification-before-completion`) install user-global and can be turned off.
See the
[configuration reference](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md#skills)
for update cadence and the trust statement.

## Protections

A two-stage commit review acts as a damage gate during the pipeline's
commit phases: the commit review phases run immediately before their
corresponding commit phases. Ralph Workflow's own engine writes are
auto-committed in isolation at the producer boundary: each commit covers
only the exact paths the writer just modified, paths that are already
dirty are skipped and stay in the agent or user commit flow, and an
auto-commit failure is logged but never blocks the run. See [commit review configuration](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/configuration.md#commit-review-configuration)
and the [auto-commit contract](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/docs/agents/deterministic-auto-commit.md).

## Next steps

- **Evaluate it:** [START_HERE.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/START_HERE.md) — a first serious run, end to end.
- **Use it:** the [operator manual](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/docs/sphinx/index.rst) — getting started, configuration, and every reference.
- **Contribute:** [CONTRIBUTING.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/CONTRIBUTING.md) — every pull request must agree to the [Contributor License Agreement](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/CLA.md).

## License

AGPL-3.0-or-later: you may run, read, and modify it; if you offer a
modified version over a network, you publish your changes.
The Ralph loop pattern is credited to Geoffrey Huntley; Ralph Workflow is
an independent implementation built on that core. No agent vendor named
above endorses this project.

## Project home

- **Repository:** <https://github.com/Ralph-Workflow/Ralph-Workflow> — **PyPI:** <https://pypi.org/project/ralph-workflow/>
- **Issue tracker:** <https://github.com/Ralph-Workflow/Ralph-Workflow/issues/new>
- **License:** [LICENSE](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/LICENSE) — **Code of conduct:** [CODE_OF_CONDUCT.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/CODE_OF_CONDUCT.md)
- **Contributing:** [CONTRIBUTING.md](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/ralph-workflow/CONTRIBUTING.md) — every pull request must agree to the [Contributor License Agreement](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/CLA.md)
