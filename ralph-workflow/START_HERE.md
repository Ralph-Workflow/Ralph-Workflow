# Start Here: Try Ralph Workflow on One Real Backlog Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> Use the GitHub mirror only as a secondary follow/read surface if GitHub is where you already track projects: <https://github.com/Ralph-Workflow/Ralph-Workflow>

If you want to know whether Ralph Workflow is worth keeping, do not start with a vague demo.

Start with one real task you already want done, run it unattended, and judge the result like a normal software change.

## What Ralph Workflow is

Ralph Workflow is a **free and open-source** CLI for orchestrating the coding agents you already use **on your own machine**.

You write the task in `PROMPT.md`, Ralph Workflow runs a looped workflow across planning, implementation, verification, and review, and you come back to executable changes, checks, logs, and artifacts you can inspect in your normal engineering workflow.

## Who it is for

Ralph Workflow is for developers and technical teams with engineering work that is **too big to babysit and too risky to trust blindly**.

If a task needs more than one prompt, more than one verification step, or more trust than you want to place in a single agent session, Ralph Workflow is the right kind of tool to test.

## Why it is different

The difference is not that it can hand back something reviewable. Any decent agent can try to do that.

The difference is that Ralph Workflow extends the simple Ralph loop into a **composable orchestration system**:

- plan, build, verify, and review in one workflow
- route different phases across different agents
- keep the workflow repo-native instead of trapped in one session
- start with a strong default software-writing workflow, then compose more complex loops when you need them

## Why try it now

Because it is free and open source, works with the agents you already trust, and gives you a clean first test:

**pick one real task tonight, run it, and decide tomorrow whether it produced working software, real verification, or an honest blocked state.**

That is a better evaluation than reading more marketing copy.

If you already know your first question is really about tool fit, do not dig through the full docs first:

- Comparing orchestration CLIs? Read [AI Agent Orchestration CLI](docs/ai-agent-orchestration-cli.md)
- Want a spec-first evaluation path? Read [Spec-Driven AI Agent](docs/spec-driven-ai-agent.md)
- Specifically evaluating Claude Code automation? Read [Claude Code Automation](docs/claude-code-automation.md)
- Still stuck hovering over Claude Code approval mode? Read [Claude Code Approval Mode](docs/claude-code-approval-mode.md)
- Already using one agent and want the lowest-friction setup? Read [Which Agent Should I Start With?](docs/sphinx/which-agent-should-i-start-with.md)
- Already using OpenCode and wondering whether you still need Ralph Workflow? Read [Ralph Workflow vs OpenCode](docs/ralph-workflow-vs-opencode.md)
- Already splitting work across Claude Code and Codex? Read [Claude Code + Codex Workflow](docs/sphinx/claude-code-codex-workflow.md)
- Want the shortest morning-after trust check before setup? Read [What a Good AI Coding Finish Receipt Looks Like](docs/sphinx/what-a-good-ai-coding-finish-receipt-looks-like.md)
- Want proof before setup? Open the [Example Review Bundle](docs/sphinx/example-review-bundle.md)

If you want to inspect the project before you run anything, start on Codeberg and keep GitHub in reserve as the mirror:

- Primary repo: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- GitHub mirror: <https://github.com/Ralph-Workflow/Ralph-Workflow>

If you want outside proof before you install anything, inspect one of the live third-party surfaces that already point evaluators back into Ralph Workflow:

- ToolWise review page: <https://toolwise.ai/tools/ralph-workflow>
- SaaSHub product page: <https://www.saashub.com/ralph-workflow>
- SaaSHub alternatives page: <https://www.saashub.com/ralph-workflow-alternatives>

If you want the short filter before you even draft `PROMPT.md`, use [Choose Your First Ralph Workflow Task](docs/first-task-guide.md).
If a blank page slows you down, copy one of the [First-Task Prompt Templates](docs/first-task-prompt-templates.md).

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

If you want copy-paste starter shapes instead of drafting from scratch, use [First-Task Prompt Templates](docs/first-task-prompt-templates.md).

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

- does the software actually do the requested thing?
- did unit / integration / build checks run where they should have?
- does the diff match the task?
- are the changes small enough to review?
- **would I merge this?**

If yes, Ralph Workflow earned a bigger task.
If no, you learned something useful without a subscription or a risky migration.

## Turn the first run into a real Codeberg signal

Do not let the first run end as a private opinion.

- If the result looked genuinely useful, put the adoption signal on the **primary Codeberg repo**: star it, watch it, and keep the repo handy for the next real task.
- If the run exposed friction, file it on **Codeberg** so the fix lands on the primary repo instead of disappearing into a private note.
- Use [After Your First Ralph Workflow Run](docs/after-your-first-run.md) for the short scorecard and the exact Codeberg-first next step before you bother with the GitHub mirror.

## Next links

- [Choose Your First Ralph Workflow Task](docs/first-task-guide.md) — use the fastest repo-native filter before you draft the first spec
- [First-Task Prompt Templates](docs/first-task-prompt-templates.md) — copy one proven `PROMPT.md` shape instead of starting from a blank page
- [Example Review Bundle](docs/sphinx/example-review-bundle.md) — inspect a public sample prompt, handoff notes, and review/fix artifacts before your own first run
- [After Your First Ralph Workflow Run](docs/after-your-first-run.md) — turn a promising run or a rough run into the right Codeberg-first action
- [Getting Started](docs/sphinx/getting-started.md)
- [Quickstart](docs/sphinx/quickstart.md)
- [Docs site](https://ralphworkflow.com/docs)
- [Source on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
- [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow)
