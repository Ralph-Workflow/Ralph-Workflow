# Concepts

> **New to Ralph Workflow?** See [Getting Started](getting-started.md) before reading this page — it introduces the workflow before the terminology.

This page explains the terms you will see most often when running Ralph Workflow. It is written for operators and users, not for people working on Ralph Workflow internals.

## PROMPT.md

`PROMPT.md` is the task file Ralph Workflow reads when you start a run.

In practice, it should describe:

- the goal
- the acceptance criteria
- any important constraints or context

If `PROMPT.md` still contains the starter template created by `ralph --init`, Ralph Workflow refuses to run until you replace it with a real task.

## Phase

A **phase** is one step in the workflow.

Typical phases include planning, development, review, fix, commit, and completion. Which phases exist depends on your active `.agent/pipeline.toml` policy.

The important mental model is simple: Ralph Workflow moves through named phases in order, and each phase has a specific job.

## Agent

An **agent** is an external AI coding tool Ralph Workflow runs for a phase.

Examples include Claude Code, Codex, and OpenCode-based setups. Ralph Workflow does not require one specific vendor — you decide which agents are available and which phases use them.

## Agent chain

An **agent chain** is an ordered fallback list of agents for a given kind of work.

If the first agent fails or cannot complete the phase, Ralph Workflow can retry and then fall over to the next agent in the chain instead of stopping immediately.

## Drain

A **drain** is the named routing layer between a phase and an agent chain.

Most users only need to know that a phase does not directly hard-code one specific agent. Instead, the phase points to a drain, and the drain resolves to the configured chain for that type of work.

If you are just running Ralph Workflow, you usually do not need to think about drains often. They matter mainly when you are customizing workflow policy.

## Policy

The **policy** is the configuration that defines how Ralph Workflow behaves.

The most important policy files live under `.agent/`:

- `.agent/pipeline.toml` — workflow phases and routing
- `.agent/artifacts.toml` — artifact expectations
- `.agent/ralph-workflow.toml` — optional project-local overrides for agent chains and main settings
- `.agent/mcp.toml` — MCP server configuration

If you want to understand or change how a run is routed, the policy files are the source of truth.

## Artifact

An **artifact** is a structured output produced during a phase.

Examples include a plan, development result, review issues list, or analysis decision. Ralph Workflow uses artifacts to decide whether a phase succeeded and what should happen next.

A useful rule of thumb:

- JSON artifacts are for the orchestrator
- Markdown handoff files are for humans and downstream agents

## MCP

**MCP** stands for **Model Context Protocol**.

In day-to-day use, this is the tool layer Ralph Workflow exposes to agents so they can read files, write outputs, submit artifacts, and use other approved capabilities.

If you only operate Ralph Workflow, you usually do not need to know the protocol details. The important part is that MCP is how Ralph Workflow gives agents controlled access to your project and tools.

## Checkpoint

A **checkpoint** is Ralph Workflow's saved resume state.

Ralph Workflow writes checkpoint state as the run progresses so it can continue from the last completed phase after an interruption, crash, or transient failure.

Useful commands:

- `ralph --inspect-checkpoint` — show what would be resumed
- `ralph --no-resume` — ignore the saved checkpoint and start fresh

## Recovery

**Recovery** is Ralph Workflow's built-in ability to keep a run moving through interruptions and failures.

Recovery includes things like:

- retrying transient failures
- falling over to the next agent in a chain
- pausing safely when connectivity drops
- resuming from checkpointed state

For most users, recovery is automatic. You usually do **not** need to trigger anything manually unless you explicitly want to start over instead of resuming.

See [Recovery](recovery.md) for the operator guide.

## Recovery cycle

A **recovery cycle** is one full pass through Ralph Workflow's recovery logic after a phase chain is exhausted.

Most users do not need to tune this unless they are customizing policy. The main practical point is that Ralph Workflow has limits that prevent it from retrying forever without making progress.

## Work unit

A **work unit** is a sub-task inside a larger plan that can be executed independently.

When a planning phase identifies multiple independent pieces of work, Ralph Workflow can split them into work units and run them in parallel.

## Parallel mode

**Parallel mode** lets Ralph Workflow execute multiple work units concurrently.

This is useful when a task naturally splits into independent chunks. Parallel mode is controlled by workflow policy and is described in more detail on the [Parallel Mode](parallel-mode.md) page.

## Transcript

The **transcript** is the live terminal output from a run.

It shows what phase Ralph Workflow is in, which agent is active, and what kinds of progress or warnings occurred during the run.

You do not need to memorize every line format. In normal use, the transcript is mainly there to answer three questions:

- What is Ralph Workflow doing right now?
- Did it make progress?
- Did anything fail or need attention?

## Verbosity

**Verbosity** controls how much output Ralph Workflow prints.

Common choices:

- quieter output for routine runs
- more detailed output for debugging or investigation

Use `--quiet`, `--debug`, or `--verbosity <level>` depending on how much detail you want.

## `ralph --explain-policy`

`ralph --explain-policy` prints a human-readable summary of the active workflow policy.

Use it when you want to answer questions like:

- Which phases does this project use?
- Which agents are assigned to each kind of work?
- Where do retries, loopbacks, or terminal failures route?

It is the quickest way to understand what the current configuration will do before you run it.

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough
- [Recovery](recovery.md) — automatic recovery, checkpoints, retries, and resume behavior
- [Parallel Mode](parallel-mode.md) — running independent work units concurrently
- [Policy Explanation](policy-explanation.md) — understanding the active configured workflow
- [Developer Reference](developer-reference.md) — internals and implementation-oriented material
