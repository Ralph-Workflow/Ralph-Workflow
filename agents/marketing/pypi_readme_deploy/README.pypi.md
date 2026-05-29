# Ralph Workflow (Python)

Ralph Workflow is a **free and open-source** Python 3.12+ CLI that orchestrates the
coding agents you already use **on your own machine** for substantial unattended work.

> **Write the spec. Wake up to reviewable output.**

It is for developers and technical teams with engineering tasks that are
**too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding chat is the handoff: Ralph
Workflow keeps the workflow in your repo, runs planning + implementation +
review as one unattended pass, and leaves you with **code changes, logs,
artifacts, and review context you can inspect in your normal git workflow**.

## Install

```bash
pipx install ralph-workflow
ralph --help
```

## Quick Start

```bash
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

## Why Ralph Workflow?

- **Composable loop framework** — planning loop, build loop, verification loop
- **Vendor-neutral** — works with Claude Code, Codex CLI, and OpenCode
- **Repo-native** — workflow files live in your repo, not a product silo
- **Cost model routing** — cheap models for planning, strong models for dev
- **Checkpoint/resume** — interrupted runs pick up where they left off

## When It Fits

Multi-step tasks that outgrow one prompt. Work you want to review after the
fact instead of steering live. Teams that want AI execution to stay in the
repo. Runs where you want to mix stronger and cheaper models by phase.

## Documentation

- **Start Here:** [First-task guide](https://ralphworkflow.com/docs/first-task-guide)
- **Quickstart:** [docs link](https://ralphworkflow.com/docs/quickstart)
- **Full docs:** [ralphworkflow.com](https://ralphworkflow.com)

## Primary Repo

**⭐ Star on Codeberg:** https://codeberg.org/RalphWorkflow/Ralph-Workflow
GitHub mirror: https://github.com/Ralph-Workflow/Ralph-Workflow

Requires Python 3.12+. Free and open source (AGPL/CC0).