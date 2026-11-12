# Ralph Workflow — the AI agent orchestrator and software factory for autonomous coding

**Move beyond pull requests.**

**Multiple coding agents. Separate worktrees. One mainline.**

Coding agents write code fast; the handoffs around them — babysitting a session,
re-explaining context, resolving conflicts, waiting in a review queue — do not scale
the same way. A software factory removes that bottleneck.

**Ralph Workflow is the operating system for autonomous coding: a free, open-source AI agent orchestrator and software factory that anybody can use — you give it a spec and your own checks, it runs plan → build → verify → fix as a configurable workflow graph across Git worktrees, and hands you integrated software to judge.**

It runs on your machine: no account, no hosted service, nothing to pay. Your
specifications, configuration, and Git history stay yours.

**Ralph Workflow manages the harness, not the models.** The coding agents — Claude
Code, Codex, Cursor, and the rest — are harnesses around models; Ralph Workflow is
the harness around those: workflow, review, recovery, and Git integration on top of
whichever agent CLIs you already have. A harness of harnesses.

In plain terms: it is the workflow layer that runs your coding agents, checks their
work, and lands the result on your mainline — not a model provider.

No vendor lock-in. Nine built-in agent backends ship with it — Claude Code, Codex,
Cursor, and more — and agent chains can mix them and fall back to the next one when
an attempt fails: switching or combining vendors is a configuration change, not a
rewrite. Ralph Workflow needs no API key of its own; it launches the agent CLI you
installed and authenticated. Each agent defines its own authentication (a login, an
API key, or either), and its vendor determines model access, rate limits, and cost.

[**Start your first run →**](START_HERE.md)

## A software factory you configure

You supply the specification and the checks you trust; the factory returns
integrated software for you to judge:

- **Stations.** Phases are stations on the line: planning, development, review, fix,
  commit.
- **Gates.** Checks are gates: a station that does not produce the evidence it owes
  returns `fix-needed`, `blocked`, or `retry` instead of opening the next gate.
- **Interchangeable workers.** An agent chain is the worker pool for a station: you
  choose the agent, and which one takes over when an attempt fails.
- **Parallel lines.** Worktrees are the production lines: independent runs work in
  separate Git worktrees without sharing agent context.
- **Shipping.** Git integration ships successful work to the configured mainline.

Adopt the bundled default graph as-is first — a strong default for writing software.
Its phases and routes are in the
[pipeline configuration reference](ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md);
each phase's output contract is in the
[artifacts reference](ralph-workflow/docs/sphinx/artifacts.md).

## From a loop to a workflow graph

A plain agent loop repeats one prompt until the work stops changing: give an agent a
task, let it work, check the result, iterate — that is the whole Ralph loop. By hand,
you supply every decision: the next prompt, the retry, the merge.

A workflow graph keeps that loop and moves those decisions into configuration:

- **Which phase runs next.** A phase ends in a drain: `done` advances, `fix-needed`
  routes into a fix cycle, `blocked` waits for a human, `retry` re-attempts.
- **Which agent handles it.** Routing is policy, not code — a phase is bound to an
  agent chain, so planning can go to one agent and development to another.
- **What evidence is produced.** Each phase declares a contract for what it must
  produce, and the runtime validates it before the phase ends.
- **What happens on failure.** A failed attempt retries, falls over to the next agent
  in the chain, and a stuck or crashed agent is caught by the watchdog.
- **How work resumes.** Checkpoints and durable recovery state carry an interrupted
  run forward, including a half-finished Git operation.

Drain semantics, routing, and recovery are described in the
[concepts reference](ralph-workflow/docs/sphinx/concepts.md).

## Independent work, shared mainline

Separate worktrees give agents room to work. Ralph Workflow handles the Git
coordination that makes that work converge.

- **Synchronize automatically.** Feature work syncs with the configured mainline at
  startup, after commits, and at phase boundaries.
- **Resolve conflicts in the workflow.** Ralph Workflow rebases onto the target and
  routes conflicts to a resolution agent chain, with an endpoint-merge fallback.
- **Land automatically.** The local target fast-forwards to the integrated tip.
  Local integration is on by default and the target defaults to `main`; remote
  sync publishes to the configured remote when one exists, and can be turned
  off for local-only operation.
- **Recover interrupted integration.** Durable recovery state continues an unfinished
  Git operation before ordinary development resumes.

Two facts worth stating plainly. Your repository's branch protections and required
checks still determine what is permitted — Ralph Workflow does not bypass them. And
the worktree that holds `main` is integration infrastructure: when uncommitted content
there blocks an advance, it can be snapshotted to a recoverable ref and reset by
default. Keep active edits in feature worktrees.

The [auto-integration guide](ralph-workflow/docs/sphinx/configuration.md#auto-integration)
covers synchronization, recovery, snapshot restoration, and local-only or manual
Git operation.

## Bring your own agent

Nine backends ship with Ralph Workflow. Authenticate each one yourself; the agent's
own flow is what runs, and Ralph Workflow never reads, stores, or proxies it.

- **Claude Code** (`claude`) — `claude login` or an Anthropic API key.
- **Claude Code (headless)** (`claude`) — the same binary without a PTY; the same
  `claude login` or an Anthropic API key.
- **Codex** (`codex`) — `codex login` or an OpenAI API key.
- **OpenCode** (`opencode`) — provider-specific keys, per provider.
- **Nanocoder** (`nanocoder`) — local-only TUI, no remote authentication.
- **AGY (Google Anti Gravity)** (`agy`) — `agy login` with a Google account.
- **Pi** (`pi`) — provider configuration inside Pi.
- **Cursor** (`agent`) — `CURSOR_API_KEY` or a file-backed Cursor login.
- **Kimi Code** (`kimi`) — an interactive `kimi` login wires the managed Kimi Code
  subscription.

The [agent guide](ralph-workflow/docs/sphinx/agents.md) covers the CLI lifecycle; the
[compatibility reference](ralph-workflow/docs/sphinx/agent-compatibility.md) lists
every CLI and model syntax.

## Is Ralph Workflow for you?

**For** developers and teams who want to run autonomous coding work across Git
worktrees, keep the mainline moving, and spend less time coordinating agents and
merges — and who have tasks with a clear finish line and checks they trust.

**Not (yet) for** open-ended exploration with no defined finish line, or for
repositories with no reliable tests or checks. If the finish line is fuzzy, write
it down first: one sentence saying what "done" means. If the repository has no
checks you trust, build that first — a test suite, a script, or a recognizable diff.

## Get started

You need **Python 3.12+**, **Git**, **pipx**, and one coding agent CLI installed and
authenticated. Ralph Workflow runs locally; the agent you choose determines model
access and cost. The executable is named `ralph`.

Run these in your own terminal, inside the project you want to work on:

```bash
pipx install ralph-workflow
ralph --version
cd /path/to/your/project
ralph --init
ralph --list-agents
ralph --diagnose
```

Confirm your agent appears and every diagnostic line is green. Then edit the
`PROMPT.md` that `ralph --init` created: replace the starter with your task,
acceptance criteria, and verification commands, and remove the
`<!-- ralph:starter-prompt ... -->` marker at the top — Ralph Workflow refuses to run
while it remains.

A small first task in a Python project with pytest:

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

A ready-made first task is in the
[Flask `/health` starter](example-api/README.md).

Start the run:

```bash
ralph
```

A successful first run looks like this: the run completes, the changes are committed
on a branch in a worktree, and the work is either integrated onto the mainline or
reported with the evidence it produced — what changed, which checks ran, what to
review. Then exercise the software and decide what to keep.

No pipx? Use `pip install ralph-workflow` in a Python 3.12+ virtual environment; from
a source checkout, see the
[source installation guide](ralph-workflow/CONTRIBUTING.md#dev-build-vs-stable-build).
The [first-run walkthrough](START_HERE.md) covers each step; the
[getting-started tutorial](ralph-workflow/docs/sphinx/getting-started.md) covers task
picking.

## Make it yours

| You want to… | Go here |
|---|---|
| Complete your first run | [Start here](START_HERE.md) |
| Set agents, Git sync, and project settings in `ralph-workflow.toml` | [Configuration reference](ralph-workflow/docs/sphinx/configuration.md) |
| Change phases and routing in `pipeline.toml` | [Pipeline configuration](ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md) |
| Set the tool surface in `mcp.toml` | [MCP configuration](ralph-workflow/docs/sphinx/advanced-mcp-configuration.md) |
| Define output contracts in `artifacts.toml` | [Artifact configuration](ralph-workflow/docs/sphinx/advanced-artifact-configuration.md) |
| Find an answer or troubleshoot a run | [Docs map](docs/README.md) · [Operator manual](ralph-workflow/docs/sphinx/index.rst) |

Those four names are configurable policy files, not top-level repository files.

## Open source

Ralph Workflow is free and open source under **AGPL-3.0-or-later**: you may run,
read, and modify it, and if you offer a modified version over a network you publish
your changes.

- [LICENSE](LICENSE) · [PyPI package page](https://pypi.org/project/ralph-workflow/) ·
  [Report an issue](https://github.com/Ralph-Workflow/Ralph-Workflow/issues/new)
- [Contribute](CONTRIBUTING.md) — the contributor guide, the
  [code of conduct](CODE_OF_CONDUCT.md), and the
  [Contributor License Agreement](CLA.md) that every pull request must agree to.

The Ralph Loop pattern is credited to [Geoffrey Huntley](https://ghuntley.com/ralph);
Ralph Workflow is an independent implementation built on that core, and it is
independent of the agent vendors it drives.
