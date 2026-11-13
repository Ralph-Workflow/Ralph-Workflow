# Ralph Workflow — the AI agent orchestrator and software factory for autonomous coding

**Move beyond pull requests.**

**Multiple coding agents. Separate worktrees. One mainline.**

Coding agents write code fast. The handoffs around them do not scale the
same way: babysitting a session, re-explaining context, resolving conflicts,
waiting in a review queue.

Ralph Workflow is a free, open-source AI agent orchestrator and software
factory — the operating system for autonomous coding — for developers and
teams who already have a coding agent: you
supply the specification and the checks you trust, and it returns
integrated, checked software on one mainline.

**Ralph Workflow manages the harness, not the models.** A harness of
harnesses: coding agents such as Claude Code, Codex, and Cursor are
harnesses around models, and Ralph Workflow is the workflow, review,
recovery, and Git layer around those agents. There is no vendor lock-in:
nine built-in agent backends ship with it, and
[agent chains](ralph-workflow/docs/sphinx/configuration.md) can mix them
and fall back to the next when an attempt fails. Switching or combining
vendors is a configuration change, not a rewrite. Ralph Workflow needs no
API key of its own and sells no model access; it launches the agent CLI you
installed and authenticated, so whatever login or subscription that agent
supports is what runs. Each agent defines its own authentication (a login,
an API key, or either), and the agent's vendor determines model access,
rate limits, and cost.

Every default ships with the package (workflow, agent chains, tools,
artifact contracts, retries, recovery, Git sync), and `ralph --init` seeds
your setup, so there is no setup project: productive on day 1,
preconfigured, and yours to tweak. See the
[bundled defaults](ralph-workflow/docs/sphinx/configuration.md#bundled-defaults).

It runs on your machine: local, free, AGPL-3.0-or-later, no account, no
hosted service, and no metering from Ralph Workflow. Your specifications,
configuration, and Git history stay yours.

Run one agent by hand and you are the scheduler, reviewer, and merger. Run
a plain agent loop and it repeats one prompt. Ralph Workflow runs a
configurable workflow graph with review
[gates](ralph-workflow/docs/sphinx/concepts.md), retries, recovery, and
fallback agent chains, keeps independent runs in separate Git worktrees,
and lands checked work on one mainline.

A fit for tasks with a clear finish line and checks you trust; not (yet)
for open-ended exploration or repositories with no reliable tests. See
[Is Ralph Workflow for you?](#is-ralph-workflow-for-you).

[**Start your first run →**](START_HERE.md)

## A software factory you configure

You supply the specification and the checks you trust; the factory returns
integrated software for you to judge. Every factory claim maps to a real
mechanism:

- **Stations.** Phases are stations on the line: planning, development, review, fix,
  commit.
- **Gates.** Checks are gates: a phase advances only on `on_success`, and its
  declared transitions loop work back (`on_loopback`), send a rejected commit
  review back to development (`on_reject`), or route a failure to
  `failed_terminal` (`on_failure`). See the
  [pipeline configuration reference](ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md).
- **Interchangeable workers.** An agent chain is the worker pool for a station: you
  choose the agent, and which one takes over when an attempt fails; see the
  [configuration reference](ralph-workflow/docs/sphinx/configuration.md).
- **Parallel lines.** Worktrees are the production lines: independent runs work in
  separate Git worktrees without sharing agent context.
- **Shipping.** Git integration ships successful work to the configured mainline.

Adopt the bundled default graph as-is first; it is a strong default for
writing software. Its phases and routes are in the
[pipeline configuration reference](ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md),
and each phase's output contract is in the
[artifacts reference](ralph-workflow/docs/sphinx/artifacts.md).

## Is Ralph Workflow for you?

**For** developers and teams who want to run autonomous coding work across
Git worktrees, keep the mainline moving, and spend less time coordinating
agents and merges, and who have tasks with a clear finish line and checks
they trust.

**Not (yet) for** open-ended exploration with no defined finish line, or
for repositories with no reliable tests or checks. If the finish line is
fuzzy, write it down first: one sentence saying what "done" means. If the
repository has no checks you trust, build that first: a test suite, a
script, or a recognizable diff.

## From a loop to a workflow graph

A plain agent loop repeats one prompt until the work stops changing: give
an agent a task, let it work, check the result, iterate. That is the whole
Ralph loop: plan → build → verify → fix. Run it by hand and you supply every
call yourself: the next prompt, the retry, the merge.

A workflow graph keeps that loop and moves those calls into configuration:

- **Which phase runs next.** Each phase's drain binds it to an agent chain, and
  the transitions declared in `pipeline.toml` pick the next phase; a run ends
  at `complete` or `failed_terminal`.
- **Which agent handles it.** Routing is policy, not code. A phase is bound to an
  agent chain, so planning can go to one agent and development to another.
- **What evidence is produced.** Each phase declares a contract the runtime
  validates before the phase ends. These contracts are the factory's durable
  handoffs: each phase's validated output is handed to the next phase and survives
  an interruption. See the [artifacts reference](ralph-workflow/docs/sphinx/artifacts.md)
  and the [recovery reference](ralph-workflow/docs/sphinx/recovery.md).
- **What happens on failure.** A failed attempt retries, falls over to the next
  agent in the chain, and a stuck or crashed agent is caught by the watchdog.
- **How work resumes.** Checkpoints and durable recovery state carry an interrupted
  run forward, including a half-finished Git operation.

Routing and recovery are described in the
[concepts reference](ralph-workflow/docs/sphinx/concepts.md).

## Independent work, shared mainline

Review and integration happen inside the execution graph, not in a
pull-request queue. You define the outcome and the checks; Ralph Workflow runs
the workflow; you judge the software. Branch protections and required
checks still apply, and nothing lands without the Git result proving it.

Separate worktrees give agents room to work. Ralph Workflow handles the
Git coordination that makes that work converge.

- **Synchronize automatically.** Feature work syncs with the configured
  mainline at startup, after commits, and at phase boundaries.
- **Resolve conflicts in the workflow.** Ralph Workflow rebases onto the
  target and routes conflicts to a resolution agent chain, with an
  endpoint-merge fallback.
- **Land automatically.** The local target fast-forwards to the integrated
  tip once Git proves the integration finished. Local integration is on by
  default and the target defaults to `main`; remote sync publishes to the
  configured remote when one exists, and can be turned off for local-only
  operation.
- **Recover interrupted integration.** Durable recovery state continues an
  unfinished Git operation before ordinary development resumes.

Two facts worth stating plainly. Your repository's branch protections and
required checks still determine what is permitted; Ralph Workflow does not
bypass them. And the worktree that holds `main` is integration
infrastructure: when uncommitted content there blocks an advance, it can be
snapshotted to a recoverable ref and reset by default. Keep active edits in
feature worktrees.

The [auto-integration guide](ralph-workflow/docs/sphinx/configuration.md#auto-integration)
covers synchronization, recovery, snapshot restoration, and local-only or
manual Git operation.

## Bring your own agent

Nine backends ship with Ralph Workflow. Authenticate each one yourself:
Ralph Workflow needs no API key of its own and sells no model access, and
each agent's own login or key is what runs.

- **Claude Code** (`claude`) — `claude login` or an Anthropic API key.
- **Claude Code (headless)** (`claude`) — the same binary without a PTY; the same
  `claude login` or an Anthropic API key.
- **Codex** (`codex`) — `codex login` or an OpenAI API key.
- **OpenCode** (`opencode`) — provider-specific keys, per provider.
- **Nanocoder** (`nanocoder`) — local-only TUI, no remote authentication.
- **AGY (Google Anti Gravity)** (`agy`) — `agy login` with a Google account.
- **Pi** (`pi`) — provider configuration inside Pi.
- **Cursor** (`agent`) — `CURSOR_API_KEY` or a file-backed Cursor login; if no
  valid Cursor auth file exists, Ralph Workflow copies your existing Cursor IDE
  login into Cursor's file-backed auth paths.
- **Kimi Code** (`kimi`) — an interactive `kimi` login wires the
  `managed:kimi-code` subscription provider.

The [agent guide](ralph-workflow/docs/sphinx/agents.md) covers the CLI
lifecycle; the
[compatibility reference](ralph-workflow/docs/sphinx/agent-compatibility.md)
lists every CLI and model syntax.

## Get started

You need **Python 3.12+**, **Git**, **pipx**, and one coding agent CLI
installed and authenticated. Ralph Workflow runs locally; the agent you
choose determines model access and cost. The executable is named `ralph`.

Run these in your own terminal, inside the project you want to work on:

```bash
pipx install ralph-workflow
ralph --version
cd /path/to/your/project
ralph --init
ralph --list-agents
ralph --diagnose
```

Confirm your agent appears and every diagnostic line is green. Then edit
the `PROMPT.md` that `ralph --init` created: replace the starter with your
task, acceptance criteria, and verification commands, and remove the
`<!-- ralph:starter-prompt ... -->` marker; Ralph Workflow refuses to run
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

A successful first run completes with the changes committed on a branch in
a worktree, integrated onto the mainline or reported with the evidence it
produced: what changed, which checks ran, what to review. Then exercise the
software and decide what to keep.

No pipx? `pip install ralph-workflow` in a Python 3.12+ virtual environment
also works; from a source checkout, see the
[source installation guide](ralph-workflow/CONTRIBUTING.md#dev-build-vs-stable-build).
The [first-run walkthrough](START_HERE.md) covers each step; the
[getting-started tutorial](ralph-workflow/docs/sphinx/getting-started.md)
covers task picking.

## Make it yours

| You want to… | Go here |
|---|---|
| Complete your first run | [Start here](START_HERE.md) |
| Set agents, Git sync, and project settings in `ralph-workflow.toml` | [Configuration reference](ralph-workflow/docs/sphinx/configuration.md) |
| Change phases and routing in `pipeline.toml` | [Pipeline configuration](ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md) |
| Set the tool surface in `mcp.toml` | [MCP configuration](ralph-workflow/docs/sphinx/advanced-mcp-configuration.md) |
| Define output contracts in `artifacts.toml` | [Artifact configuration](ralph-workflow/docs/sphinx/advanced-artifact-configuration.md) |
| Find an answer or troubleshoot a run | [Docs map](docs/README.md) · [Operator manual](ralph-workflow/docs/sphinx/index.rst) |

Those four names are configurable policy files, not top-level repository
files.

## Open source

Ralph Workflow is free and open source under **AGPL-3.0-or-later**: you may
run, read, and modify it, and if you offer a modified version over a
network you publish your changes.

Next step, by reader type:

- **Evaluate it**: run the [first-run walkthrough](START_HERE.md).
- **Use it**: configure your setup with the
  [operator manual](ralph-workflow/docs/sphinx/index.rst).
- **Contribute it**: read [CONTRIBUTING.md](CONTRIBUTING.md) and the
  [code of conduct](CODE_OF_CONDUCT.md); every contribution must agree to the
  [Contributor License Agreement](CLA.md).

- [LICENSE](LICENSE) · [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) ·
  [PyPI package page](https://pypi.org/project/ralph-workflow/) ·
  [Report an issue](https://github.com/Ralph-Workflow/Ralph-Workflow/issues/new)

The Ralph loop pattern is credited to [Geoffrey Huntley](https://ghuntley.com/ralph);
Ralph Workflow is an independent implementation built on that core, and it
is independent of the agent vendors it drives.
