# Start Here: Use Ralph Workflow on One Real Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> Use the GitHub mirror if GitHub is where you already track projects: <https://github.com/Ralph-Workflow/Ralph-Workflow>

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow is built to bring back a **reviewable result** — a real diff, checks, artifacts, and enough context to decide whether you would merge it.

Why use it now? Because you can install it for free, hand off one real backlog task tonight, and judge the result honestly tomorrow.

Before you start: Ralph Workflow does **not** replace Claude Code, Codex CLI, OpenCode, or whichever coding agent you want to use. It orchestrates the agent you already have on **your own machine**. For the fastest honest first run, make sure one supported agent CLI is already installed and already authenticated before you continue.

If you want to know whether Ralph Workflow is useful, do not start with a vague demo.

Start with **one real backlog task** you already care about.

## The only three steps that matter on a first evaluation

1. **Inspect the primary repo on Codeberg first** — <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. **Run one bounded real task tonight** — use the quickstart below
3. **Judge the morning-after handoff with one question** — *would I merge this?*

If those three steps are clear, stop there and run it. The rest of this page is only for the one blocker that still makes the first run feel risky.

## Convert the first run into one Codeberg action

Once you have a real result, do not stop at a private impression.

- **If the run looks promising:** star or watch the primary repo on Codeberg — <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **If the run exposed friction or missing proof:** open the matching issue form on Codeberg — <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **If you want a two-minute post-run scorecard first:** use [After Your First Ralph Workflow Run](./docs/after-your-first-run.md)

## What a good first handoff looks like

Before you install, this is the shape you should expect back from a real overnight run:

```text
Task: Add empty-project-name validation to the CLI create flow

Changed files:
- cli/create.py
- tests/test_create.py

Checks run:
- unit tests for create flow
- lint / formatting checks if applicable

Open questions:
- should reserved names be rejected too?
- should whitespace be trimmed before validation?
```

That is the standard to hold Ralph Workflow to: **a reviewable result, not a transcript and a promise**.

If you want the longer version of that artifact before you run anything, inspect the public [example review bundle](./docs/example-review-bundle.md).

## The fastest honest first run

Checklist before you run it:

- Python 3.12+
- a git repo you can safely test in
- at least one supported agent CLI already working on your machine

If you are unsure which one to start with, use the one you already trust and see [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

Paste a small real spec into `PROMPT.md`:

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

Then review the result like normal engineering work and ask one question:

> **Would I merge this?**

If yes, give Ralph Workflow a harder task tomorrow night — and star or watch Ralph Workflow on **Codeberg**: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
If not, tighten the task or checks and run again — or report the friction on **Codeberg**: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>

## Pick the right first task

Choose something that is:
- small enough to judge in one sitting
- real enough to matter
- bounded enough that rollback is cheap
- clear enough that success is easy to define

Good first tasks:
- a small feature slice
- a bounded refactor with tests
- a backlog item with clear acceptance criteria
- repetitive implementation work with obvious verification

Bad first tasks:
- a vague product idea
- risky production surgery
- mixed multi-part work
- anything where no one agrees what "done" means

## Only read deeper if you still have one blocker

- **Still hesitating over agent choice?** Read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).
- **Want a better Claude Code automation path?** Read [docs/claude-code-automation.md](./docs/claude-code-automation.md).
- **Need to run Claude Code overnight without babysitting?** Read [docs/run-claude-code-overnight-without-babysitting.md](./docs/run-claude-code-overnight-without-babysitting.md).
- **Claude Code approval mode still leaves you stuck?** Read [docs/claude-code-approval-mode.md](./docs/claude-code-approval-mode.md).
- **Want copy-paste starter specs?** Read [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).
- **Not sure whether your task is a good fit?** Read [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md).
- **Already use Claude Code and want the clearest reason to add Ralph?** Read [docs/ralph-workflow-vs-claude-code.md](./docs/ralph-workflow-vs-claude-code.md).
- **Already use OpenCode and want to know when unattended handoff is better?** Read [docs/ralph-workflow-vs-opencode.md](./docs/ralph-workflow-vs-opencode.md).
- **Already use Codex CLI and want to know when unattended handoff is better?** Read [docs/ralph-workflow-vs-codex-cli.md](./docs/ralph-workflow-vs-codex-cli.md).
- **Already use worktrees and want to know what Ralph adds beyond that?** Read [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md).
- **Already use Claude Code + Codex and want a cleaner split?** Read [docs/claude-code-codex-workflow.md](./docs/claude-code-codex-workflow.md).
- **Run multiple agents and the real pain is review/reconstruction?** Read [docs/what-breaks-first-with-multiple-coding-agents.md](./docs/what-breaks-first-with-multiple-coding-agents.md).
- **The handoff still feels hard to judge at merge time?** Read [docs/review-ai-coding-output-before-merge.md](./docs/review-ai-coding-output-before-merge.md).
- **Core trust question is whether the task is actually done?** Read [docs/how-to-tell-if-an-ai-coding-task-is-actually-done.md](./docs/how-to-tell-if-an-ai-coding-task-is-actually-done.md).
- **Want the cleanest possible morning-after re-entry point?** Read [docs/what-a-good-ai-coding-finish-receipt-looks-like.md](./docs/what-a-good-ai-coding-finish-receipt-looks-like.md).
- **Keep calling "remote supervision" but really need finish-state trust?** Read [docs/remote-supervision-of-coding-agents.md](./docs/remote-supervision-of-coding-agents.md).
- **Evaluating category fit first?** Read [docs/open-source-ai-coding-orchestrator.md](./docs/open-source-ai-coding-orchestrator.md).
- **Searching for "unattended coding agent"?** Read [docs/unattended-coding-agent.md](./docs/unattended-coding-agent.md).
- **Already use Aider and want to know when Ralph is the better fit?** Read [docs/ralph-workflow-vs-aider.md](./docs/ralph-workflow-vs-aider.md).

## How to judge the result honestly

Do not ask whether the agent looked smart.

Ask:
- does the diff match the task?
- are the changes small enough to review?
- did the checks really run?
- **would I merge this?**

That is the whole evaluation.

## What a good run should hand back

A useful Ralph Workflow run should leave you with:
- a scoped result
- a real diff
- changed files you can inspect
- checks that actually ran
- a reasoning trail
- open questions called out clearly

## Next reading

- [README.md](./README.md)
- [docs/quick-reference.md](./docs/quick-reference.md)
- [docs/free-open-source-proof.md](./docs/free-open-source-proof.md) — see the concrete artifact bundle and morning-after review path
- [docs/review-ai-coding-output-before-merge.md](./docs/review-ai-coding-output-before-merge.md) — use the five-minute merge check on the morning-after handoff
- [docs/what-a-good-ai-coding-finish-receipt-looks-like.md](./docs/what-a-good-ai-coding-finish-receipt-looks-like.md) — see the exact short handoff that should save you from replaying the whole night
- [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md)
