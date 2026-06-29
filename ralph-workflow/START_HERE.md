# Start Here: Try Ralph Workflow on One Real Backlog Task

> **Codeberg is the primary repo.** Star, watch, and report issues there:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror.

If you want to know whether Ralph Workflow is worth keeping, do not start with a vague demo.

Start with one real task you already want done, run it unattended, and judge the result like a normal software change.

## What Ralph Workflow is

Ralph Workflow is the **autopilot for coding agents** and a free and open-source
**AI agent orchestrator** for the coding agents you already use, on your own
machine.

You write the task in `PROMPT.md`, Ralph Workflow runs a looped workflow across
planning, implementation, verification, and review, and you come back to
executable changes, checks, logs, and artifacts you can inspect in your normal
engineering workflow.

The default workflow is strong enough to adopt as-is. Customize it later when
you understand your own bottlenecks, not before.

## Who it is for

Ralph Workflow is for developers and technical teams with engineering work
that is **too big to babysit and too risky to trust blindly**.

If a task needs more than one prompt, more than one verification step, or
more trust than you want to place in a single agent session, Ralph Workflow
is the right kind of tool to test.

## Why it is different

The difference is not that it can hand back something reviewable. Any decent
agent can try to do that.

The difference is that Ralph Workflow extends the simple Ralph loop into a
**composable orchestration system**:

- plan, build, verify, fix, and recover in one workflow
- route different phases across different agents
- keep the workflow repo-native instead of trapped in one session
- start with a strong default software-writing workflow, then compose more
  complex loops when you need them

## Why try it now

Because it is free and open source, works with the agents you already trust,
and gives you a clean first test:

**pick one real task tonight, run it, and decide tomorrow whether it
produced working software, real verification, or an honest blocked state.**

That is a better evaluation than reading more marketing copy.

## Before you start

Have these ready:

- one real git repo you care about
- Python 3.12+
- one supported agent CLI already installed and authenticated (see the
  [Agent CLI lifecycle](docs/sphinx/agents.md) page for selection and the
  trust boundary around authentication)
- working auth for that agent

If you are unsure which agent to use, see
[Which Agent Should I Start With?](docs/sphinx/which-agent-should-i-start-with.md).

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

If you want the short filter before you even draft `PROMPT.md`, use
[Choose Your First Ralph Workflow Task](docs/sphinx/first-task-guide.md).

## Run the smallest honest test

```bash
pipx install ralph-workflow
cd /path/to/your/repo
ralph --diagnose
ralph --init
$EDITOR PROMPT.md
ralph
```

Run those commands from a human-operated shell outside any Ralph-managed
agent session.

- `ralph --diagnose` is the pre-flight check; it shows which baseline helpers
  are healthy, missing, unreachable, degraded, or need repair. See the
  [Diagnostics](docs/sphinx/diagnostics.md) page for the full workflow.
- `ralph --init` provisions the default local work surface, web helpers, and
  shipped baseline skills for a first run that is ready to use.

## Judge the result honestly

Do not ask whether the agent sounded smart.

Ask:

- does the software actually do the requested thing?
- did unit / integration / build checks run where they should have?
- does the diff match the task?
- are the changes small enough to review?
- **would I merge this?**

If yes, Ralph Workflow earned a bigger task.
If no, you learned something useful without a subscription or a risky migration.

## Turn the first run into a real Codeberg signal

Do not let the first run end as a private opinion.

- If the result looked genuinely useful, put the adoption signal on the
  **primary Codeberg repo**: star it, watch it, and keep the repo handy for
  the next real task.
- If the run exposed friction, file it on **Codeberg** so the fix lands on the
  primary repo instead of disappearing into a private note.

## Next links

- [Choose Your First Ralph Workflow Task](docs/sphinx/first-task-guide.md)
- [Diagnostics](docs/sphinx/diagnostics.md)
- [Agent CLI lifecycle](docs/sphinx/agents.md)
- [Getting Started](docs/sphinx/getting-started.md)
- [Quickstart](docs/sphinx/quickstart.md)
- [After Your First Run](docs/sphinx/after-your-first-run.md)
- [Agent Subsystem](docs/agents/README.md)
- [Docs site](https://ralphworkflow.com/docs)
- [Source on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
- [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow)
