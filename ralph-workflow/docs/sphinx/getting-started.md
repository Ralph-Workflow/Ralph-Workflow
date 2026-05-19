# Getting Started with Ralph Workflow

New to Ralph Workflow? This page takes you from install to your first unattended run without assuming you already know the pipeline internals. If you want the same flow in shorter form, use [Quickstart](quickstart.md).

## What Ralph Workflow does

Ralph Workflow is a **free and open-source** repo-native orchestration CLI for bigger AI coding tasks. You describe the task in `PROMPT.md`, Ralph Workflow runs planning, coding, and agent review, and you come back to completed work, logs, and artifacts you can inspect in your normal git workflow.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding chat is the handoff: Ralph Workflow is built to leave you with a **reviewable result** in your repo instead of a long transcript and a claim that the task is done.

Why use it now? Because you can try it with the agents you already trust on your own machine, on one real backlog task, and decide tomorrow whether the result is something you would actually merge.

Important expectation before you install: Ralph Workflow is free and open source, but it does **not** replace the coding agent itself. For the fastest honest first run, have at least one supported agent CLI already installed and already authenticated on your own machine before you continue.

It works well for substantial work in **existing repositories** as well as new ones: feature work, refactors, test expansion, documentation passes, and similar multi-file tasks.

## Choose the right first task

Do not start with a vague demo.

Start with one real backlog task that is:

- small enough to judge in one sitting
- clear enough that success is easy to define
- cheap to roll back if the run misses
- real enough that you already want it done

Good first tasks:

- a bounded feature slice
- a narrow refactor with tests
- a cleanup task with obvious verification
- repetitive implementation work where `done` is easy to judge

Bad first tasks:

- vague product exploration
- risky production surgery
- broad multi-part work with no clear stopping point
- anything where nobody agrees what success looks like

If you want a sharper pass/fail filter before you install, read [Choose Your First Ralph Workflow Task](first-task-guide.md).
If you are unsure whether to start with Claude Code, Codex, or OpenCode, read [Which Agent Should I Start With?](which-agent-should-i-start-with.md).
If Claude Code is already your default and you want the clearest answer to “why add Ralph Workflow at all?”, read [Ralph Workflow vs Claude Code](ralph-workflow-vs-claude-code.md).
If you already use Claude Code and Codex together and want a cleaner split between implementation and review, read [Claude Code + Codex Workflow](claude-code-codex-workflow.md).
If you already run multiple agents and the real pain is review/reconstruction rather than branch collisions, read [What Breaks First When You Run Multiple Coding Agents?](what-breaks-first-with-multiple-coding-agents.md).
If the part you still do not trust is the morning-after merge decision, read [How to Review AI Coding Output Before You Merge](review-ai-coding-output-before-merge.md).
If you want the cleanest possible short handoff before you trust the run, read [What a Good AI Coding Finish Receipt Looks Like](what-a-good-ai-coding-finish-receipt-looks-like.md).
If you want copy-paste starter specs instead of drafting from scratch, read [First-Task Prompt Templates](first-task-prompt-templates.md).
If you already use Aider for interactive work and want the clearest contrast, read [Ralph Workflow vs Aider](ralph-workflow-vs-aider.md).
If you want to inspect a public sample `PROMPT.md`, handoff notes, and review/fix artifacts before you run Ralph Workflow yourself, read [Example Review Bundle](example-review-bundle.md).
If you prefer to inspect or follow the project on GitHub, the public mirror is here: <https://github.com/Ralph-Workflow/Ralph-Workflow> (primary repo: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>).

## Before you start

You will need:

- **Python 3.12 or newer** — check with `python --version`
- **A git repository** — Ralph Workflow runs inside a git repo
- **At least one supported AI agent on your PATH** — usually `claude` (Claude Code), Codex CLI, or `opencode` (OpenCode). If you want the documented non-interactive Claude path, configure `claude-headless`.

If you are unsure which one to start with, use the agent that is already installed, already authenticated, and already familiar, then read [Which Agent Should I Start With?](which-agent-should-i-start-with.md).

Install links:

- Claude Code: <https://docs.claude.com/claude-code>
- OpenCode: <https://opencode.ai>

## Install in 60 seconds

```bash
pipx install ralph-workflow
ralph --version
```

If `pipx` is not available yet:

```bash
python -m pip install pipx
python -m pipx ensurepath
```

## Your first run

### 1. Go to your git repository

```bash
cd /path/to/your/project
```

Most teams use Ralph Workflow in an existing repository they already care about. If you are trying it in a scratch repo instead, create one first:

```bash
git init my-project && cd my-project
```

### 2. Initialize Ralph Workflow

```bash
ralph --init
```

This creates `PROMPT.md` plus the project-local `.agent/` support files Ralph Workflow needs to run.

If this repository also needs a project-local copy of the main Ralph Workflow config, create it explicitly:

```bash
ralph --init-local-config
```

### 3. Edit `PROMPT.md`

Open `PROMPT.md` and replace the example content with your actual task:

```markdown
# Goal

Add a /health endpoint that returns HTTP 200 with {"status": "ok"}.

## Acceptance criteria

- GET /health returns HTTP 200
- Response body is valid JSON with status == ok
- A new test covers the endpoint
```

A strong first spec says:

- what should change
- what should stay untouched
- what `done` looks like
- what checks prove it worked

If a blank page slows you down, start from [First-Task Prompt Templates](first-task-prompt-templates.md) and adapt the closest shape to your repo.

**Important:** remove the `<!-- ralph:starter-prompt ... -->` comment at the top. Ralph Workflow refuses to run while that sentinel is still present so you do not accidentally launch the placeholder task.

### 4. Verify the environment

```bash
ralph --diagnose
```

This is the recommended pre-flight check before the first real run. Fix any ❌ rows before continuing. Common issues:

- No agent on PATH → install `claude` or `opencode`
- Config errors → run `ralph --regenerate-config`

### 5. Start the run

```bash
ralph
```

Ralph Workflow shows progress inline while it runs. When it finishes, you come back to completed work, logs, and artifacts you can inspect before deciding what to do next.

## How to judge the result honestly

Do not ask whether the agent looked smart.

Ask:

- does the diff match the task?
- are the changes small enough to review?
- did the checks really run?
- **would I merge this?**

That is the real product test.

If you want to see what a trustworthy handoff looks like before your first run, read [What Good Ralph Workflow Output Looks Like](reviewable-output.md).

## What happens during a run

You do not need the full internal model to operate Ralph Workflow. The short version is:

1. **Planning** — Ralph Workflow turns your task into a plan
2. **Development** — an implementation agent works through the plan
3. **Analysis and review** — Ralph Workflow checks the result, decides whether more work is needed, and records review output
4. **Completion** — the run ends with the resulting changes, logs, and artifacts saved in the repo

If you later want the deeper mechanics — phases, drains, loopbacks, policy files, and artifact contracts — see [Concepts](concepts.md) and [Configuration](configuration.md).

## When something goes wrong

**The sentinel comment is still in `PROMPT.md`**

```
PolicyValidationError: PROMPT.md is still the starter template
```

Replace the example task and remove the `<!-- ralph:starter-prompt ... -->` line.

**No agents found on PATH**

```
ralph --diagnose
```

Install `claude` or `opencode`, then run the diagnostic again.

**Config errors in `ralph --diagnose`**

```bash
ralph --regenerate-config
```

This rewrites config files from the bundled defaults and keeps backups with a `.bak` suffix.

## Next steps

- [Choose Your First Ralph Workflow Task](first-task-guide.md) — pick a real first task with a clean merge/no-merge evaluation
- [Which Agent Should I Start With?](which-agent-should-i-start-with.md) — choose the first agent path with the least setup friction
- [First-Task Prompt Templates](first-task-prompt-templates.md) — copy-paste `PROMPT.md` shapes for strong first runs
- [Ralph Workflow vs Claude Code](ralph-workflow-vs-claude-code.md) — the clearest comparison if your baseline is a live Claude Code session and you want to know when an unattended handoff is better
- [Claude Code + Codex Workflow](claude-code-codex-workflow.md) — practical guide for keeping the role split but improving the morning-after handoff
- [What Breaks First When You Run Multiple Coding Agents?](what-breaks-first-with-multiple-coding-agents.md) — shared-boundary drift, merged-state checks, finish receipts, and the trust failures that show up before raw merge conflicts
- [How to Review AI Coding Output Before You Merge](review-ai-coding-output-before-merge.md) — practical five-minute merge checklist for the morning-after handoff
- [What a Good AI Coding Finish Receipt Looks Like](what-a-good-ai-coding-finish-receipt-looks-like.md) — exact shape of the short handoff that should save you from transcript archaeology
- [Ralph Workflow vs Aider](ralph-workflow-vs-aider.md) — the clearest comparison if your current baseline is interactive AI pair programming
- [What Good Ralph Workflow Output Looks Like](reviewable-output.md) — shorter proof overview and the merge test
- [Example Review Bundle](example-review-bundle.md) — inspect a public sample prompt, handoff notes, and review/fix artifacts
- [Quickstart](quickstart.md) — shorter repeat-use reference
- [Concepts](concepts.md) — the terms you will see most often
- [CLI Reference](cli.md) — commands and flags
- [Configuration](configuration.md) — config files and precedence
- [Troubleshooting](troubleshooting.md) — common first-run problems
- [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow) — inspect, star, or watch Ralph Workflow where you already follow projects

If your first run gives you a clean diff, real checks, and a result you would merge, hand Ralph Workflow a bigger task the next night.
