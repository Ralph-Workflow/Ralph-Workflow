# Start Here: Use Ralph Workflow on One Real Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> Use the GitHub mirror if GitHub is where you already track projects, but treat Codeberg as the main home: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a **free and open-source** tool that orchestrates the coding agents you already use **on your own machine**.

It is for developers and technical teams with work that is **too big to babysit and too risky to trust blindly**.

What makes it different is the handoff: Ralph Workflow is built to bring back a **reviewable result** — a real diff, checks, artifacts, and enough context to decide whether you would merge it.

Why use it now? Because you can install it for free, hand off one real backlog task tonight, and judge the result honestly tomorrow.

Before you start: Ralph Workflow does **not** replace Claude Code, Codex CLI, OpenCode, or whichever coding agent you want to use. It orchestrates the agent you already have on **your own machine**. For the fastest honest first run, make sure one supported agent CLI is already installed and already authenticated before you continue.

If you want to know whether Ralph Workflow is useful, do not start with a vague demo.

Start with **one real backlog task** you already care about.

If the only thing you are stuck on right now is agent choice, read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md) and pick the agent that is already working on your machine.

If you want to inspect the project before you install anything, open the [primary Codeberg repo](https://codeberg.org/RalphWorkflow/Ralph-Workflow) first, or use the [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow) if that is where you already track projects.

Codeberg is the primary repo and the main adoption surface. If Ralph looks worth trying on your next overnight backlog task, **inspect it on Codeberg first, then star, watch, or fork it there**. If you mainly evaluate projects on GitHub, the mirror is available there too.

Use Codeberg for the real project actions:
- **Source + releases:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Issues / feedback:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

## Convert the first run into one Codeberg action

Once you have a real result, do not stop at a private impression.

- **If the run looks promising:** star or watch the primary repo on Codeberg — <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **If the run exposed friction or missing proof:** open the matching issue form on Codeberg — <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **If you want a two-minute post-run scorecard first:** use [After Your First Ralph Workflow Run](./docs/after-your-first-run.md)

That one branch point is the intended finish: either Ralph Workflow earned a public trust signal on Codeberg, or it earned a useful primary-repo issue.

If you want a little more outside proof before you install, you can also inspect Ralph Workflow on [GitDB](https://gitdb.net/Ralph-Workflow/Ralph-Workflow), [SaaSHub](https://www.saashub.com/ralph-workflow), or [TechTools Launchpad](https://techtools.cz/tools/launchpad/?tool=71). Those are not substitutes for your own judgment, but they do give you independent discovery/comparison surfaces before your first run.

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

If you want the clean handoff standard before you run anything, read [What Good Ralph Workflow Output Looks Like](./docs/reviewable-output.md). If you want the longer version of that artifact, inspect the public [example review bundle](./docs/example-review-bundle.md).

If your first run hits confusing setup, weak docs, or a handoff you would not trust yet, report it on **Codeberg** here: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>. Choose the matching first-run or docs/proof form there. The Codeberg-first issue forms are meant for exactly that feedback.

If the run is promising, use [After Your First Ralph Workflow Run](./docs/after-your-first-run.md) to turn that private first run into the right public Codeberg action.

## The only three steps that matter on a first evaluation

1. **Inspect the primary repo on Codeberg first** — <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. **Run one bounded real task tonight** — use the exact quickstart below instead of reading every doc first
3. **Judge the morning-after handoff with one question** — *would I merge this?*

If those three steps are clear, stop there and run it. The rest of this page is only for the one blocker that still makes the first run feel risky.

A good first run should be easy to judge in five minutes the next morning:

1. **One bounded brief** in `PROMPT.md`
2. **One unattended run** on your own machine with the agent you already use
3. **Checks that actually ran**
4. **Fixes attempted before handoff**, not just a draft dumped back on you
5. **Readable repo-local artifacts** you can open without replaying the terminal session
6. **One merge question:** *would I merge this?*

If that handoff shape sounds right, read [What Good Ralph Workflow Output Looks Like](./docs/reviewable-output.md) or inspect the public [example review bundle](./docs/example-review-bundle.md) before you install or continue below for the fastest honest first run.

## Only read deeper if you still have one blocker

If you want a stronger answer to "what is this, who is it for, why is it different, and why should I try it now?" before you install, start with these:

- [What Good Ralph Workflow Output Looks Like](./docs/reviewable-output.md) — the shortest trust-page version of the morning-after handoff standard.
- [How to Tell if an AI Coding Task Is Actually Done](./docs/how-to-tell-if-an-ai-coding-task-is-actually-done.md) — how to judge whether the morning-after result is actually trustworthy.
- [Claude Code + Codex Workflow: Plan, Build, Review](./docs/claude-code-codex-workflow.md) — the cleanest long-form explanation of the plan/build/review split.
- [When Unattended AI Coding Actually Works](./docs/when-unattended-coding-fits.md) — the quickest fit check before you spend a night on the wrong task.

They all lead back to the same honest test: install Ralph Workflow for free, run one real backlog task on your own machine, and decide tomorrow whether you would merge it.

## The fastest honest first run

If you want the shortest path from curiosity to a real evaluation, use this exact flow in a real repo you already care about:

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

Need the shortest post-run scorecard? Use [After Your First Ralph Workflow Run](./docs/after-your-first-run.md).

## Only use these extra guides if the main path still leaves one question

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
- anything where no one agrees what “done” means

If you are still hesitating over Claude Code vs Codex vs OpenCode, read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).
If Claude Code is already your default and the real thing you want is a better automation / unattended path, read [docs/claude-code-automation.md](./docs/claude-code-automation.md).
If your exact problem is "how do I run Claude Code overnight without babysitting the terminal?", read [docs/run-claude-code-overnight-without-babysitting.md](./docs/run-claude-code-overnight-without-babysitting.md).
If Claude Code approval mode or plan mode still leaves you stuck near the terminal, read [docs/claude-code-approval-mode.md](./docs/claude-code-approval-mode.md).
If you want copy-paste starter specs instead of drafting from scratch, read [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).
If you are unsure whether your task belongs in the good or bad bucket, read [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md).
If Claude Code is already your default and you want the clearest reason to add Ralph Workflow instead of just staying in one live session, read [docs/ralph-workflow-vs-claude-code.md](./docs/ralph-workflow-vs-claude-code.md).
If OpenCode is already your default and you want to know when an unattended reviewable handoff is the better fit, read [docs/ralph-workflow-vs-opencode.md](./docs/ralph-workflow-vs-opencode.md).
If Codex CLI is already your default and you want to know when an unattended reviewable handoff is the better fit, read [docs/ralph-workflow-vs-codex-cli.md](./docs/ralph-workflow-vs-codex-cli.md).
If you already use worktrees or separate agent sessions and want to know what Ralph Workflow adds beyond that, read [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md).
If you already use Claude Code and Codex together and want a cleaner split between implementation and review, read [docs/claude-code-codex-workflow.md](./docs/claude-code-codex-workflow.md).
If you already run multiple coding agents and the real pain is review/reconstruction, read [docs/what-breaks-first-with-multiple-coding-agents.md](./docs/what-breaks-first-with-multiple-coding-agents.md).
If the handoff still feels hard to judge at merge time, read [docs/review-ai-coding-output-before-merge.md](./docs/review-ai-coding-output-before-merge.md).
If the core trust question is still whether the task is actually done, read [docs/how-to-tell-if-an-ai-coding-task-is-actually-done.md](./docs/how-to-tell-if-an-ai-coding-task-is-actually-done.md).
If you want the cleanest possible morning-after re-entry point, read [docs/what-a-good-ai-coding-finish-receipt-looks-like.md](./docs/what-a-good-ai-coding-finish-receipt-looks-like.md).
If the thing you keep calling "remote supervision" is really a finish-state trust problem, read [docs/remote-supervision-of-coding-agents.md](./docs/remote-supervision-of-coding-agents.md).
If you are evaluating category fit first and want the clearest Codeberg-first positioning, read [docs/open-source-ai-coding-orchestrator.md](./docs/open-source-ai-coding-orchestrator.md).
If your exact search is "unattended coding agent" and you want the shortest trust-first answer before setup, read [docs/unattended-coding-agent.md](./docs/unattended-coding-agent.md).
If you already use Aider and want to know when Ralph Workflow is the better fit, read [docs/ralph-workflow-vs-aider.md](./docs/ralph-workflow-vs-aider.md).

## Run the fastest honest first test

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

Use a real repo and a real backlog item. The point is not to watch the run live.
The point is to come back to something you can review like normal engineering work.

## Write the task like a one-paragraph spec

Before the run starts, write down:
- what needs to change
- what should stay untouched
- what done looks like
- what checks matter

Use a simple structure like this in `PROMPT.md`:

```markdown
# Goal

Add validation so the CLI rejects empty project names before creating files.
Keep the rest of the create flow unchanged.

## Acceptance criteria

- Empty or whitespace-only project names fail with a clear error
- No project files are created for invalid names
- Existing valid-name behavior stays unchanged
- Tests cover the new validation
```

That level of specificity is enough for a strong first run.
If you want more ready-made shapes for feature work, validation, refactors, tests, or docs, use [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).

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
- [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md)
- [docs/claude-code-automation.md](./docs/claude-code-automation.md)
- [docs/run-claude-code-overnight-without-babysitting.md](./docs/run-claude-code-overnight-without-babysitting.md)
- [docs/claude-code-approval-mode.md](./docs/claude-code-approval-mode.md)
- [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md)
- [docs/free-open-source-proof.md](./docs/free-open-source-proof.md) — see the concrete artifact bundle and morning-after review path
- [docs/review-ai-coding-output-before-merge.md](./docs/review-ai-coding-output-before-merge.md) — use the five-minute merge check on the morning-after handoff
- [docs/what-a-good-ai-coding-finish-receipt-looks-like.md](./docs/what-a-good-ai-coding-finish-receipt-looks-like.md) — see the exact short handoff that should save you from replaying the whole night
- [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md)
- [docs/ralph-workflow-vs-claude-code.md](./docs/ralph-workflow-vs-claude-code.md)
- [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md)
- [docs/claude-code-codex-workflow.md](./docs/claude-code-codex-workflow.md)
- [docs/what-breaks-first-with-multiple-coding-agents.md](./docs/what-breaks-first-with-multiple-coding-agents.md)
- [docs/ralph-workflow-vs-opencode.md](./docs/ralph-workflow-vs-opencode.md)
- [docs/ralph-workflow-vs-aider.md](./docs/ralph-workflow-vs-aider.md)
