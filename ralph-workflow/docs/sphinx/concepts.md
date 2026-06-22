# Concepts

> **New to Ralph Workflow?** See [Getting Started](getting-started.md) first if you want the setup flow before the terminology.

This page explains the terms you will see most often when running Ralph Workflow.

## The simplest mental model

A normal run looks like this:

1. You write the task in `PROMPT.md`
2. Ralph Workflow plans and implements the work
3. Ralph Workflow records analysis and review output during the run
4. You inspect the resulting changes, logs, and artifacts afterward

The terms below help explain how Ralph Workflow does that.

## `PROMPT.md`

`PROMPT.md` is the task file Ralph Workflow reads when you start a run.

In practice, it should describe:

- the goal
- the acceptance criteria
- any important constraints or context

If `PROMPT.md` still contains the starter template created by `ralph --init`, Ralph Workflow refuses to run until you replace it with a real task.

## Phase

A **phase** is one step in the workflow.

Common phases include planning, development, review, fix, commit, and completion. Which phases exist depends on the active policy, but the practical idea is simple: each phase has one job.

## Agent

An **agent** is an external AI coding tool Ralph Workflow runs for a phase.

Examples include Claude Code, Codex, OpenCode, Pi.dev, and Google Anti Gravity-based setups. Ralph Workflow does not lock you into one provider.

## Agent chain

An **agent chain** is an ordered fallback list of agents for one kind of work.

If the first agent fails or exhausts its retries, Ralph Workflow can move to the next one instead of stopping immediately.

## Drain

A **drain** is the routing label between a phase and an agent chain.

Most users do not need to think about drains often. The practical takeaway is that a phase is not hard-wired to one specific agent. You can change routing in config without rewriting the workflow itself.

## Policy

The **policy** is the configuration that defines how Ralph Workflow behaves.

The main policy files live under `.agent/`:

- `.agent/pipeline.toml` — workflow phases and routing
- `.agent/artifacts.toml` — artifact expectations
- `.agent/ralph-workflow.toml` — optional project-local overrides for agent chains and main settings
- `.agent/mcp.toml` — MCP server configuration

If you want to change how a run is routed or validated, these files are the source of truth.

## Artifact

An **artifact** is a structured output produced during a phase.

Examples include a plan, a development result, a review issues list, or an analysis decision.

A useful rule of thumb:

- JSON artifacts help Ralph Workflow decide what to do next
- Markdown handoff files are easier for humans and downstream agents to inspect

## Review output

Ralph Workflow can record review output during the run, depending on the active workflow policy.

The important distinction is:

- **agents** can review work while the run is in progress
- **humans** inspect the completed work, logs, and artifacts afterward in their normal git workflow

That distinction keeps the automation loop clear without pretending the run replaces human judgment.

## MCP

**MCP** stands for **Model Context Protocol**.

In day-to-day use, this is the tool layer Ralph Workflow exposes to agents so they can read files, write outputs, submit artifacts, and use other approved capabilities.

If you only operate Ralph Workflow, you usually do not need the protocol details.

## Checkpoint

A **checkpoint** is Ralph Workflow's saved resume state.

Ralph Workflow writes checkpoint state as the run progresses so it can continue from the last completed step after an interruption, crash, or transient failure.

Useful operator commands:

These are operator-side shell commands for the human running Ralph Workflow, not instructions for an agent inside a Ralph-managed session to launch Ralph again.

- From the human operator shell, `ralph --inspect-checkpoint` shows what would be resumed.
- From the human operator shell, `ralph --no-resume` ignores the saved checkpoint and starts fresh.

## Recovery

**Recovery** is Ralph Workflow's built-in ability to keep a run moving through interruptions and failures.

Recovery includes things like:

- retrying transient failures
- falling over to the next agent in a chain
- pausing safely when connectivity drops
- resuming from checkpointed state

For most users, recovery is automatic.

**Interrupt dispatch.** When a user presses Ctrl+C during a run, the
`InterruptDispatcher` (in `ralph.interrupt.dispatcher`) coordinates orderly
shutdown: the first SIGINT routes through
`InterruptController.begin_interrupt(kill_label='invoke:')` to send SIGTERM
to the agent's process group, then a poll thread escalates to SIGKILL if
the agent makes no CPU-time progress within `hard_kill_budget_s` (default
1.5s). A second SIGINT calls `InterruptController.force_exit(bridge_pids=...)`
which terminates tracked processes and exits with code 130. The CLI catches
in `ralph.cli.commands.run` and `ralph.cli.main` also call the dispatcher
with `block=True`, so the orderly shutdown happens even when the interrupt
is raised outside the pipeline loop.

## Work unit

A **work unit** is a sub-task inside a larger plan that can be executed independently.

When planning finds multiple independent pieces of work, Ralph Workflow can split them into work units and run them in parallel.

## Parallel mode

**Parallel mode** lets Ralph Workflow execute multiple work units concurrently.

This is useful when a task naturally splits into independent chunks. See [Parallel Mode](parallel-mode.md) for the operator guide.

## Transcript

The **transcript** is the live terminal output from a run.

In normal use, it mainly answers three questions:

- What is Ralph Workflow doing right now?
- Did it make progress?
- Did anything fail or need attention?

## Verbosity

**Verbosity** controls how much output Ralph Workflow prints.

Use `--quiet`, `--debug`, or `--verbosity <level>` depending on how much detail you want.

## `ralph --explain-policy`

From the human operator shell, `ralph --explain-policy` prints a human-readable summary of the active workflow policy.

Use it when you want to answer questions like:

- Which phases does this project use?
- Which agents are assigned to each kind of work?
- Where do retries or loopbacks route?

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough
- [Recovery](recovery.md) — retries, checkpoints, and resume behavior
- [Parallel Mode](parallel-mode.md) — running independent work units concurrently
- [Policy Explanation](policy-explanation.md) — understanding the active configured workflow
- [Developer Reference](developer-reference.md) — implementation-oriented material
