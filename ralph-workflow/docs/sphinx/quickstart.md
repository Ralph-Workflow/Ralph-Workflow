# Quickstart

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) if you want the same flow with more explanation.

Ralph Workflow is a **free and open-source** orchestration CLI that runs the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow is built to leave you with a **reviewable result** in your repo instead of just a transcript and a claim that the task is done.

Why use it now? Because you can run one real backlog task tonight, come back to a diff, checks, and artifacts tomorrow, and ask one honest question: **would I merge this?**

Important first-run expectation: Ralph Workflow does **not** replace the coding agent itself. Before you install, have at least one supported agent CLI already installed and already authenticated on your own machine.

## The fastest honest first run

Use this flow in a real repo you already care about.

**Before you start, confirm:**

- Python 3.12+
- a git repo you can safely test in
- at least one supported agent CLI already working on your machine

**If you are unsure which agent to start with**, read [Which Agent Should I Start With?](which-agent-should-i-start-with.md) first — it maps agent setup friction to the right first path.

**For the clearest fit判断 before installing:**

- OpenCode already your default? → [Ralph Workflow vs OpenCode](ralph-workflow-vs-opencode.md)
- Claude Code already your default? → [Claude Code Automation for Real Repo Work](claude-code-automation.md)
- Running Claude Code overnight without babysitting? → [Run Claude Code Overnight Without Babysitting](run-claude-code-overnight-without-babysitting.md)
- Approval mode still leaves you babysitting? → [Claude Code Approval Mode Is Not an Unattended Workflow](claude-code-approval-mode.md)
- Evaluating orchestration tools directly? → [AI Agent Orchestration CLI: A Practical Comparison](ai-agent-orchestration-cli.md)
- Want the spec-first framing? → [Spec-Driven AI Agent: Why the Spec Matters More Than the Prompt](spec-driven-ai-agent.md)

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

If you want the shortest post-run scorecard plus the right public next step, use [After Your First Ralph Workflow Run](after-your-first-run.md).

## Where to go next

**Pick your first real task:**

- [Choose Your First Ralph Workflow Task](first-task-guide.md) — pick a bounded backlog item with a clean pass/fail evaluation
- [First-Task Prompt Templates](first-task-prompt-templates.md) — copy-paste starter specs for common good-fit tasks

**Find the right agent:**

- [Which Agent Should I Start With?](which-agent-should-i-start-with.md) — pick the path with the least setup friction for your machine

**Understand the tradeoffs before you commit:**

- [Ralph Workflow vs OpenCode](ralph-workflow-vs-opencode.md) — baseline: interactive OpenCode setup
- [Ralph Workflow vs Claude Code](ralph-workflow-vs-claude-code.md) — baseline: interactive Claude Code session
- [Ralph Workflow vs Codex CLI](ralph-workflow-vs-codex-cli.md) — baseline: interactive Codex CLI session
- [Ralph Workflow vs Aider](ralph-workflow-vs-aider.md) — baseline: interactive AI pair programming
- [Claude Code + Codex Workflow](claude-code-codex-workflow.md) — split work between both

**Run it overnight:**

- [Run Claude Code Overnight Without Babysitting](run-claude-code-overnight-without-babysitting.md)
- [Bounded Autonomy for Unattended Coding](bounded-autonomy-for-unattended-coding.md)
- [What Breaks First When You Run Multiple Coding Agents?](what-breaks-first-with-multiple-coding-agents.md)

**Judge the result:**

- [How to Review AI Coding Output Before You Merge](review-ai-coding-output-before-merge.md) — five-minute morning-after checklist
- [What a Good AI Coding Finish Receipt Looks Like](what-a-good-ai-coding-finish-receipt-looks-like.md) — the short handoff that tells you what changed, what passed, what still needs judgment
- [After Your First Ralph Workflow Run](after-your-first-run.md) — post-run scorecard
- [What Good Ralph Workflow Output Looks Like](reviewable-output.md) — proof overview and merge test
- [Example Review Bundle](example-review-bundle.md) — public sample prompt, result, review, and artifacts

**Day-one reference:**

- [Getting Started](getting-started.md) — fuller first-run walkthrough
- [CLI Reference](cli.md) — all flags and subcommands
- [Configuration Reference](configuration.md) — config files and precedence
