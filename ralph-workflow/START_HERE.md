# Start Here: Try Ralph Workflow on One Real Backlog Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> Use the GitHub mirror if GitHub is where you already track projects: <https://github.com/Ralph-Workflow/Ralph-Workflow>

If you want to know whether Ralph Workflow is worth keeping, do not start with a vague demo.

Start with one real task you already want done, run it unattended, and judge the result like a normal code review.

## What Ralph Workflow is

Ralph Workflow is a **free and open-source** CLI that orchestrates the coding agents you already use **on your own machine**.

You write the task in `PROMPT.md`, Ralph Workflow runs planning, implementation, and review, and you come back to code changes, logs, and artifacts you can inspect in your normal git workflow.

## Who it is for

Ralph Workflow is for developers and technical teams with engineering work that is **too big to babysit and too risky to trust blindly**.

If a task needs more than one prompt, more than one verification step, or more trust than you want to place in a single agent session, Ralph Workflow is the right kind of tool to test.

## Why it is different

A normal AI coding chat gives you a transcript and a claim that the task is done.

Ralph Workflow is built to leave you with something **reviewable**:

- changed files in your repo
- logs and artifacts from the run
- review context you can inspect afterward
- a result you can judge with the same merge standards you already use

## Why try it now

Because it is free and open source, works with the agents you already trust, and gives you a clean first test:

**pick one real task tonight, run it, and decide tomorrow whether you would merge the result.**

That is a better evaluation than reading more marketing copy.

If you already know your first question is really about tool fit, do not dig through the full docs first:

- Already using one agent and want the lowest-friction setup? Read [Which Agent Should I Start With?](docs/sphinx/which-agent-should-i-start-with.md)
- Already splitting work across Claude Code and Codex? Read [Claude Code + Codex Workflow](docs/sphinx/claude-code-codex-workflow.md)
- Want proof before setup? Open the [Example Review Bundle](docs/sphinx/example-review-bundle.md)

If you want to inspect the project where you already follow open-source work, Ralph Workflow is published on Codeberg and mirrored on GitHub:

- Primary repo: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- GitHub mirror: <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Pick the right first task

Good first tasks are:

- narrow feature slices
- bounded refactors with tests
- documentation or cleanup work with clear verification
- repetitive implementation work where `done` is easy to judge

Bad first tasks are:

- vague exploration
- risky production surgery
- broad multi-part epics
- anything where nobody agrees what success looks like

## Write the task like a one-paragraph spec

Your `PROMPT.md` should make four things obvious:

1. what should change
2. what should stay untouched
3. what counts as done
4. what checks prove it worked

Minimal example:

```md
# Goal

Add a /health endpoint that returns HTTP 200 with {"status": "ok"}.

## Acceptance criteria

- GET /health returns HTTP 200
- response body is valid JSON with status == ok
- a new test covers the endpoint
```

## Run the smallest honest test

```bash
pipx install ralph-workflow
cd /path/to/your/repo
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

## Judge the result honestly

Do not ask whether the agent sounded smart.

Ask:

- does the diff match the task?
- are the changes small enough to review?
- did the checks actually run?
- **would I merge this?**

If yes, Ralph Workflow earned a bigger task.
If no, you learned something useful without a subscription or a risky migration.

## Next links

- [Example Review Bundle](docs/sphinx/example-review-bundle.md) — inspect a public sample prompt, handoff notes, and review/fix artifacts before your own first run
- [Getting Started](docs/sphinx/getting-started.md)
- [Quickstart](docs/sphinx/quickstart.md)
- [Docs site](https://ralphworkflow.com/docs)
- [Source on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
- [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow)
