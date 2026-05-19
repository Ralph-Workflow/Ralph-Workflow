# Quickstart

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) if you want the same flow with more explanation.

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow is built to leave you with a **reviewable result** in your repo instead of just a transcript and a claim that the task is done.

Why use it now? Because you can run one real backlog task tonight, come back to a diff, checks, and artifacts tomorrow, and ask one honest question: **would I merge this?**

Important first-run expectation: Ralph Workflow does **not** replace the coding agent itself. Before you install, have at least one supported agent CLI already installed and already authenticated on your own machine.

## The fastest honest first run

Use this flow in a real repo you already care about:

Checklist before you start:

- Python 3.12+
- a git repo you can safely test in
- at least one supported agent CLI already working on your machine

If you are unsure which agent to start with, use the one already installed and read [Which Agent Should I Start With?](which-agent-should-i-start-with.md). If Claude Code is already your default and you want the clearest reason to add Ralph, read [Ralph Workflow vs Claude Code](ralph-workflow-vs-claude-code.md). If you already split work between Claude Code and Codex, read [Claude Code + Codex Workflow](claude-code-codex-workflow.md). If you already run multiple agents and the trust gap is in the morning-after handoff, read [What Breaks First When You Run Multiple Coding Agents?](what-breaks-first-with-multiple-coding-agents.md). If the merge decision itself still feels fuzzy, read [How to Review AI Coding Output Before You Merge](review-ai-coding-output-before-merge.md). If the missing piece is a short trustworthy re-entry summary, read [What a Good AI Coding Finish Receipt Looks Like](what-a-good-ai-coding-finish-receipt-looks-like.md). If you want the Codeberg-first category explanation before you install, read [Open-Source AI Coding Orchestrator: What Ralph Workflow Is Actually For](open-source-ai-coding-orchestrator.md).

## Install

```bash
pipx install ralph-workflow
ralph --version
```

## Initialize Ralph Workflow in a repository

Go to your project directory, then run:

```bash
cd <your-project>
ralph --init
```

This creates:

- `PROMPT.md` — the task file in the project root
- `.agent/` — project-local support files (`mcp.toml`, `pipeline.toml`, `artifacts.toml`)
- `~/.config/ralph-workflow.toml` and `~/.config/ralph-workflow-mcp.toml` — user-global defaults created once and reused across projects

If this repository also needs a project-local copy of the main Ralph Workflow config, run the explicit opt-in local-override flow:

```bash
ralph --init-local-config
```

That command creates `.agent/ralph-workflow.toml` as the project-local main-config override.

## Edit `PROMPT.md`

Open `PROMPT.md` and replace the example with one **real, bounded backlog task**. If you are unsure what a good first task looks like, read [Choose Your First Ralph Workflow Task](first-task-guide.md) first. If you want copy-paste prompt shapes instead of starting from a blank page, read [First-Task Prompt Templates](first-task-prompt-templates.md).

A strong first prompt looks like this:

```markdown
# Goal

Add validation so the CLI rejects empty project names before creating files.
Keep the rest of the flow unchanged.

## Acceptance criteria

- Empty or whitespace-only project names fail with a clear error
- No project files are created for invalid names
- Existing valid-name behavior stays unchanged
- Tests cover the new validation
```

**Important:** remove the `<!-- ralph:starter-prompt ... -->` comment at the top after replacing the example content. Ralph Workflow refuses to run while that sentinel is still present.

## Verify the environment

```bash
ralph --diagnose
```

The diagnostic checks the repo, config, agent binaries, MCP definitions, and prompt pre-flight state. Fix any ❌ rows before running.

## Run Ralph Workflow

```bash
ralph
```

Ralph Workflow runs unattended and shows progress inline. In plain terms, it plans the task, implements the work, reviews the result during the run, and leaves you with completed work, logs, and artifacts to inspect afterward.

If the run earns your trust, put the public signal on the primary Codeberg repo first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>. The GitHub mirror stays available here: <https://github.com/Ralph-Workflow/Ralph-Workflow>.

If interrupted, Ralph Workflow saves a checkpoint automatically. Continue from that saved state with:

```bash
ralph --resume
```

## How to judge the result honestly

Do not ask whether the agent sounded convincing.

Ask:

- does the diff match the task?
- did the checks really run?
- are the changes reviewable in one sitting?
- **would I merge this?**

That is the real first-run test.

## Where to go next

- [Choose Your First Ralph Workflow Task](first-task-guide.md) — pick a real first task with a clean pass/fail evaluation
- [Which Agent Should I Start With?](which-agent-should-i-start-with.md) — pick the first agent path with the least setup friction
- [First-Task Prompt Templates](first-task-prompt-templates.md) — copy-paste starter specs for common good-fit tasks
- [Ralph Workflow vs Claude Code](ralph-workflow-vs-claude-code.md) — the clearest comparison if your baseline is an interactive Claude Code session
- [Claude Code + Codex Workflow](claude-code-codex-workflow.md) — practical guide for reducing manual handoff glue between implementation and review
- [What Breaks First When You Run Multiple Coding Agents?](what-breaks-first-with-multiple-coding-agents.md) — practical guide for the trust/reconstruction failures that show up before raw Git conflicts
- [How to Review AI Coding Output Before You Merge](review-ai-coding-output-before-merge.md) — practical five-minute merge checklist for the morning-after handoff
- [What a Good AI Coding Finish Receipt Looks Like](what-a-good-ai-coding-finish-receipt-looks-like.md) — the exact short handoff that should tell you what changed, what passed, and what still needs judgment
- [Ralph Workflow vs Aider](ralph-workflow-vs-aider.md) — the clearest comparison if your current baseline is interactive AI pair programming
- [What Good Ralph Workflow Output Looks Like](reviewable-output.md) — see the shorter proof overview and the merge test
- [Example Review Bundle](example-review-bundle.md) — inspect a public sample prompt, result notes, review feedback, and artifacts before your own first run
- [Getting Started](getting-started.md) — fuller first-run walkthrough
- [Concepts](concepts.md) — terminology and mental model
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration Reference](configuration.md) — config files and precedence
- [Python API Reference](modules.rst) — package documentation
