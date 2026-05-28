#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path('/home/mistlight/.openclaw/workspace')
PRIMARY = Path('/home/mistlight/RalphWithReviewer')
MIRROR = ROOT / 'repos' / 'Ralph-Workflow' / 'github-mirror'

PUBLIC_DOC_ROOTS = [
    MIRROR / 'docs',
    MIRROR / 'ralph-workflow' / 'docs' / 'sphinx',
]

PUBLIC_DOC_EXCLUDE_FILES = {
    'cli.md',
    'concepts.md',
    'developer-reference.md',
    'modules.rst',
    'troubleshooting.md',
    'agents.md',
}

PUBLIC_DOC_EXCLUDE_DIR_PARTS = {
    'superpowers',
    'plans',
    'agents',
    'code-style',
    'tooling',
    'performance',
}

BANNED_REPLACEMENTS = {
    r'reviewable result': 'strong software result',
    r'not just a transcript': 'not just raw logs',
    r'merge decision': 'human review',
    r'would i merge this\??': 'does the implementation hold up?',
    r'bounded diff': 'understandable diff',
    r'finish receipt': 'result summary',
    r'handoff standard': 'handoff shape',
}

REWRITES: dict[Path, str] = {
    PRIMARY / 'README.md': """<!-- AI AGENTS: DO NOT MODIFY. This file is intentionally narrow. Public first-run docs live on the canonical Ralph Workflow surfaces. -->

# Ralph Workflow

**The operating system for autonomous coding.**

Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core.
That core stays simple, but it composes into a stronger composable workflow for substantial, well-specified planning, development, verification, and review.
It also ships with a strong default workflow for writing software before you customize anything.

This source-tree README is **not** the main first-run guide.
Its job is to point you to the canonical public route and to the local source-tree docs that exist in this repository.

## Canonical public route

Use this route in order:

1. Codeberg primary repo: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. `START_HERE.md` in the canonical public docs flow
3. `docs/README.md` as the curated docs switchboard
4. the operator manual when you need deeper configuration or reference detail

## Local source-tree docs in this repo

If you are already inside this source tree, use these local docs for maintainer-oriented detail:

- [docs/quick-reference.md](docs/quick-reference.md)
- [docs/architecture/README.md](docs/architecture/README.md)
- [docs/agent-compatibility.md](docs/agent-compatibility.md)

## What this file is deliberately not doing

This README does not try to be:

- the main product pitch
- another competing onboarding path
- the full operator manual
- a proof or evaluation page

That separation is intentional. Repeated docs drift happened because too many surfaces were trying to do all jobs at once.

## License

Licensed under AGPL-v3.
The license applies to Ralph Workflow itself, not to the code it generates.
""",
    PRIMARY / 'ralph-workflow' / 'README.md': """# Ralph Workflow CLI Source

**The operating system for autonomous coding.**

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
That simple center is what makes the workflow understandable, extensible, and powerful in composition.
The product ships with a strong default workflow for writing software; this source-tree README is narrower than that public story.

## Page role

This file is the **implementation-facing source README** for the CLI directory.
It is not the canonical public first-run guide.

If you want the public product journey, use this route instead:

1. Codeberg primary repo: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. `START_HERE.md`
3. `docs/README.md`
4. the operator manual for configuration/reference questions

## What lives in this directory

This directory contains the CLI implementation and related source for the Ralph Workflow command-line tool, which is built for substantial, well-specified software engineering work.
Use it when you are changing the implementation, building locally, or reading source-level behavior.

## Local development

Build locally:

```bash
cargo build --release
```

Run tests:

```bash
cargo test
```

Format and lint:

```bash
cargo fmt --check
cargo clippy --all-targets --all-features
```

## Source-tree docs

Use these local docs when working on the implementation:

- [../docs/quick-reference.md](../docs/quick-reference.md)
- [../docs/architecture/README.md](../docs/architecture/README.md)
- [../docs/template-guide.md](../docs/template-guide.md)

## Why this file is intentionally narrow

The recurring docs-agent failure was page-role collapse:
multiple READMEs tried to act as product pitch, onboarding path, manual, and proof surface at the same time.
This file is intentionally limited so the public route stays coherent.

## License

Licensed under AGPL-v3.
""",
    MIRROR / 'README.md': """# Ralph Workflow

> **The operating system for autonomous coding.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source **AI agent orchestrator** for substantial, well-specified software engineering on your own machine.

It takes the simple Ralph-loop idea and turns it into a **composable workflow system** for planning, implementation, verification, review, and agent routing.
The core stays simple. That simplicity is what makes more complex workflows easier to build, easier to configure, and easier to extend.

Ralph Workflow also ships with a **strong default workflow for writing software**.
You can use that default as-is, or build on top of it when you need something more advanced.

## The route to use

1. [START_HERE.md](START_HERE.md) — shortest honest first run
2. [docs/README.md](docs/README.md) — curated docs switchboard
3. [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) — operator manual and configuration reference

## Install

```bash
pipx install ralph-workflow
ralph --help
```

Requires Python 3.12+.

## Before your first run

Make sure the agent CLIs you want Ralph Workflow to call are already installed and authenticated.
Ralph Workflow does not replace those coding agents. It orchestrates them.

## License

[AGPL-3.0-or-later](LICENSE).
""",
    MIRROR / 'START_HERE.md': """# Start Here: Run Ralph Workflow on One Real Task

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect and follow Ralph Workflow on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
It runs the coding agents you already use on your own machine, turns that simple loop structure into a stronger composable workflow for substantial, well-specified software engineering work, and gives you a strong default workflow before you customize anything.

If you want the shortest honest first run, this page is it.
Start with one real, well-specified backlog task and judge the outcome by what the software does now and what checks ran.

## Before you start

Have these ready:

- one real git repo you care about
- Python 3.12+
- one supported agent CLI already installed
- working auth for that agent

## Pick the right first task

Good first tasks:

- a substantial feature slice with clear acceptance criteria
- a refactor with tests and clear acceptance criteria
- a verification or test-coverage pass on behavior you already rely on
- a cleanup task with a real finish line

Bad first tasks:

- tiny edits where setup dominates the work
- vague exploration
- risky production surgery
- work that depends on constant mid-run steering

If you are unsure, use [docs/first-task-guide.md](./docs/first-task-guide.md).

## Install and run

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

## What success looks like

After a good first run, you should be able to point to:

- a real repo change that matches the written task
- meaningful checks that ran and reported clear outcomes
- a result you can review without reconstructing the whole run
- a clear sense of whether the default workflow helped enough to keep using

## Next pages only if you need them

- task selection — [docs/first-task-guide.md](./docs/first-task-guide.md)
- docs switchboard — [docs/README.md](./docs/README.md)
- operator manual — [ralph-workflow/docs/sphinx/index.rst](./ralph-workflow/docs/sphinx/index.rst)
""",
    MIRROR / 'docs' / 'README.md': """# Documentation Map

Use this page after [README.md](../README.md) and [START_HERE.md](../START_HERE.md).
Those pages explain what Ralph Workflow is and how to judge one honest first run.
This page routes you to the next page that best matches your question.

## Choose one route

### I want the fastest first successful run

- [START_HERE.md](../START_HERE.md)
- [Choose your first task](./first-task-guide.md)
- [Getting started in the manual](../ralph-workflow/docs/sphinx/getting-started.md)

### I want the maintained operator manual

- [Manual home](../ralph-workflow/docs/sphinx/index.rst)
- [Configuration](../ralph-workflow/docs/sphinx/configuration.md)
- [Reference](../ralph-workflow/docs/sphinx/reference.md)
- [User stories](../ralph-workflow/docs/sphinx/user-stories.md)

### I want product framing before I go deeper

- [AI agent orchestration CLI](./ai-agent-orchestration-cli.md)
- [Why the spec still matters](./spec-driven-ai-agent.md)
- [What unattended use should mean](./unattended-coding-agent.md)

## Keep proof secondary

Use proof-oriented pages only after you already understand the product story or the operator route.
If you need deeper evidence, the manual and linked supporting pages will take you there.

## Primary repo

Codeberg is the primary repo and source of truth:
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>
""",
    MIRROR / 'docs' / 'ai-agent-orchestration-cli.md': """# AI Agent Orchestration CLI: What Matters in Practice

Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core that composes into more complex workflows for substantial, well-specified repo work.
The shipped default workflow is already strong enough to start with, and this page explains why that matters when you compare orchestration CLIs.

If you are comparing AI agent orchestration CLIs, the useful question is not whether a tool can call an agent.
The useful question is whether it gives you a workflow that stays understandable, reviewable, and extensible when the task stops being tiny.

## What Ralph Workflow is trying to solve

A single long coding-agent session can work for small edits.
It gets much shakier when the task needs:

- a real written spec
- explicit planning before implementation
- repeated verification instead of one final guess
- room to swap or extend agent behavior later
- a handoff a human can judge without reverse-engineering the whole run

Ralph Workflow takes the simple Ralph-loop idea and uses it as the center of a larger orchestration model.
The point is not complexity for its own sake.
The point is to keep the center simple so the larger workflow stays easier to reason about.

## Why the default workflow matters

The default workflow matters because most users should not have to design an orchestration system before they can test one.
You should be able to start with the shipped path, run a real task, and only then decide whether to extend it.

That is the practical promise: simple at the center, stronger in composition, useful before customization.

## Where to go next

- for the shortest honest first run: [START_HERE.md](../START_HERE.md)
- for task selection help: [first-task-guide.md](./first-task-guide.md)
- for the operator manual: [Sphinx manual home](../ralph-workflow/docs/sphinx/index.rst)
- for configuration and file locations: [configuration.md](../ralph-workflow/docs/sphinx/configuration.md)
""",
    MIRROR / 'docs' / 'spec-driven-ai-agent.md': """# Spec-Driven AI Agent: Why the Spec Still Matters

Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core that composes into more complex workflows for substantial, well-specified repo work.
Use this page when you need to understand why written scope is what lets the default workflow stay honest instead of just sounding impressive.

If an agent keeps saying it is done before the work actually holds up, the problem is often not raw model capability.
The problem is that the task was never specific enough to verify honestly.

## Why Ralph Workflow leans on specs

Ralph Workflow is designed for ambitious work that already deserves a clear target:

- a real feature slice
- a milestone with acceptance criteria
- a refactor with defined invariants
- a verification pass with concrete failure conditions

That is where a spec stops being ceremony and starts being operational.
It tells the workflow what done means.
It also gives the human a standard to review against afterward.

## What a good spec changes

A serious spec helps in three places at once:

1. **Planning** — the agent can make better choices when constraints are explicit.
2. **Verification** — checks can be judged against something real instead of vibes.
3. **Review** — the human can compare the result to the promised scope instead of reading tea leaves from a transcript.

Without that written target, even a strong model can produce work that sounds plausible while drifting away from what mattered.

## Why this fits the default workflow

Ralph Workflow is not asking for giant design docs on every change.
It is asking you to use the workflow where ambiguity is expensive and a clear finish line matters.
That is one reason the default workflow works better on serious repo tasks than on tiny, vague chores.

## What to read next

- for choosing a strong first task: [first-task-guide.md](./first-task-guide.md)
- for the shortest honest run path: [START_HERE.md](../START_HERE.md)
- for operator setup and configuration: [configuration.md](../ralph-workflow/docs/sphinx/configuration.md)
""",
    MIRROR / 'docs' / 'first-task-guide.md': """# Choose Your First Ralph Workflow Task

Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core that composes into more complex workflows for substantial, well-specified repo work.
Use this page to choose a first task that lets the shipped default workflow prove itself honestly.

## Do not start with a vague demo

The fastest honest test is one substantial backlog slice you already care about.
Choose something that is large enough to benefit from orchestration, but clear enough to judge afterward.

A good first task is:

- substantial enough that setup is not the whole story
- defined enough that success is easy to evaluate afterward
- detailed enough that you can write a serious spec
- real enough that you already want it shipped

## Good examples

Strong first runs usually look like:

- a substantial feature slice with acceptance criteria
- a refactor with tests and known invariants
- a verification pass on behavior you already rely on
- a cleanup project with a concrete finish line

## Weak examples

Avoid starting with:

- tiny edits where orchestration overhead dominates
- vague exploration with no testable goal
- risky production surgery that needs constant live steering
- chores so small that a direct coding session would be simpler

## Why this matters

Ralph Workflow works best when the task is real, ambitious, and well specified.
That is where the simple loop core and strong default workflow have room to show why they are useful.

## Next pages

- for a short first-run path: [START_HERE.md](../START_HERE.md)
- for prompt shaping help: [first-task-prompt-templates.md](./first-task-prompt-templates.md)
- for operator setup: [getting-started.md](../ralph-workflow/docs/sphinx/getting-started.md)
""",
    MIRROR / 'docs' / 'unattended-coding-agent.md': """# Unattended Coding Agent: What It Should Actually Mean

Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core that composes into more complex workflows for substantial, well-specified repo work.
The shipped default workflow is already strong enough to start with; this page explains when unattended use is actually a good fit.

If you are looking for an unattended coding agent, the important question is not just whether a model can keep typing while you are away.
The important question is whether you can come back to software you can judge honestly.

## Unattended should still mean accountable

A useful unattended workflow should leave you with:

- a clear task boundary
- software that actually runs or verifies better than before
- checks that tell you something real
- enough structure that you can understand what happened without replaying everything

That is the standard Ralph Workflow is trying to meet.
It is not promising magic independence from engineering discipline.
It is promising a stronger way to organize serious work.

## When unattended use fits best

Ralph Workflow fits best when you have:

- a backlog task with a written finish line
- a repo with meaningful tests or validation
- enough scope that a simple chat session becomes awkward
- time to evaluate the result after the run

It fits worse when the task is vague, tiny, or dependent on constant mid-run steering.

## Why the default workflow matters

Most people should not have to build an orchestration system before they can judge one.
You should be able to start with the shipped path, learn how it behaves on a real task, and extend it later only if you need to.

## Where to go next

- for the shortest honest first run: [START_HERE.md](../START_HERE.md)
- for choosing a task worth running unattended: [first-task-guide.md](./first-task-guide.md)
- for the maintained manual: [Sphinx manual home](../ralph-workflow/docs/sphinx/index.rst)
- for user-goal routing inside the manual: [user-stories.md](../ralph-workflow/docs/sphinx/user-stories.md)
""",
    MIRROR / 'docs' / 'reviewable-output.md': """# What Good Ralph Workflow Output Looks Like

Use this page after you already understand the workflow and want a review standard for the morning-after handoff.
This page is supporting proof for Ralph Workflow's default unattended coding flow, not the main product pitch.
Start with the product story and operator route first, then use this page to judge whether a run produced something worth trusting.

## What to evaluate first

The first question is not whether the transcript sounds smart.
The first question is what the software does now.

Good output usually means:

- the task scope is recognizable from the result
- the repo is in a better state than before
- meaningful checks ran and their outcome is explicit
- the change can be reviewed against a real written spec
- the human can decide what to do next without reconstructing the whole run

## Supporting evidence, in the right order

Use evidence in this order:

1. **working behavior** — what changed in the software
2. **real checks** — tests, integration checks, or other meaningful validation
3. **written scope** — whether the result matches the promised task
4. **supporting artifacts** — logs, diffs, or deeper traces if you need them

Logs and transcripts can be useful.
They just should not be the main promise.

## What weak output looks like

Be skeptical when a run gives you:

- lots of narration but unclear product change
- a diff with no convincing checks
- a confident summary for a vague task
- artifacts that sound organized but do not make the result easier to judge

Ralph Workflow depends on real engineering guardrails.
If the repo does not have them, the honest outcome may be limited proof rather than full trust.

## Where to go next

- for the shortest first-run path: [START_HERE.md](../START_HERE.md)
- for choosing a task with a real finish line: [first-task-guide.md](./first-task-guide.md)
- for why specs matter to output quality: [spec-driven-ai-agent.md](./spec-driven-ai-agent.md)
""",
    MIRROR / 'ralph-workflow' / 'README.md': """# Ralph Workflow (Python)

Ralph Workflow is a **free and open-source** Python 3.12+ CLI for **AI agent orchestration** on your own machine.
It extends the simple Ralph loop into a **composable loop framework** for real software engineering, and the default workflow is already strong enough to start with before you customize anything.

This README is the **install + operator entrypoint**, not the main product pitch.

## Use this route

1. [START_HERE.md](../START_HERE.md)
2. [docs/README.md](../docs/README.md)
3. [docs/sphinx/index.rst](docs/sphinx/index.rst)

## Install

```bash
pipx install ralph-workflow
ralph --help
```

## Operator docs

- [Getting Started](docs/sphinx/getting-started.md)
- [Quickstart](docs/sphinx/quickstart.md)
- [Configuration](docs/sphinx/configuration.md)
- [Reference](docs/sphinx/reference.md)
- [User stories](docs/sphinx/user-stories.md)
""",
    MIRROR / 'ralph-workflow' / 'docs' / 'sphinx' / 'getting-started.md': """# Getting Started with Ralph Workflow

New to Ralph Workflow? This page takes you from install to one honest unattended run in a repository you already care about.
If you already know the shape of the product and just want the shortest checklist, use [Quickstart](quickstart.md).

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
It turns that simple structure into a stronger composable workflow for substantial, well-specified repo work by moving through planning, implementation, and verification instead of stopping at one long agent session.
The default workflow is already strong for writing software; start there first, then extend later only when you know why.

## What this page gives you

Use this guide when you want the full first-run path, not just the short version:

- what to install before you try a run
- how to choose a task with a clear finish line
- what command to run first
- what good first-run output should look like
- where to go next if you need configuration or deeper operator docs

If you need config answers while reading, open [Configuration Reference](configuration.md).
If you want docs routed by use case instead of by document type, open [End-User Stories](user-stories.md).

## Before you run Ralph Workflow

Start with a real repository and a task you could judge without reading an entire transcript.
Good first tasks usually have these properties:

- the expected change is visible in the repo or product behavior
- meaningful checks already exist, or you can add one small check
- the task is substantial enough to benefit from planning and verification
- the finish line is concrete enough that you can say whether the run succeeded

If you are unsure what counts as a good task, use [First Task Guide](first-task-guide.md) before you run anything.

## First-run flow

1. Install Ralph Workflow and confirm the CLI is available.
2. Pick one real repo and one task with a clear finish line.
3. Start with the default workflow instead of customizing immediately.
4. Let Ralph plan, implement, and verify the change.
5. Judge the result by the software change and the checks, not by transcript confidence alone.

That flow matters because Ralph Workflow is designed to give you a stronger unattended coding loop than a single long agent session.
The point of the first run is to see whether the default loop improves the repo in a way you can actually review.

## Recommended next clicks after your first run

- Need the shortest operator checklist? Use [Quickstart](quickstart.md).
- Need to inspect what trustworthy output looks like? Use [What Good Ralph Workflow Output Looks Like](../../../docs/reviewable-output.md).
- Need to change settings or file locations? Use [Configuration Reference](configuration.md).
- Need docs by goal instead of by section? Use [End-User Stories](user-stories.md).
""",
    MIRROR / 'ralph-workflow' / 'docs' / 'sphinx' / 'quickstart.md': """# Quickstart

Use this page when you already understand the product story and want the shortest path to one honest first run in a real repository.
Its job is to get you through the default workflow quickly, not to restate the full product overview.
If you need fuller explanation, task-selection help, or more context for why the default flow works, go back to [Getting Started](getting-started.md).

## Quickstart checklist

1. Pick a real repo and a task with a visible finish line.
2. Prefer the default workflow before touching advanced config.
3. Run Ralph Workflow on that task.
4. Judge the result by the repo change and the checks that ran.
5. Only customize after you know what the default loop already does well enough.

## Good first-task shape

Your first task should be:

- substantial enough to benefit from planning and verification
- specific enough that success is easy to recognize
- connected to checks you can actually run
- important enough that better unattended workflow would matter

If you need help picking that task, use [First Task Guide](first-task-guide.md).

## What success should look like

A good first quickstart run should leave you with:

- a visible repo change tied to the task you asked for
- checks or verification output you can inspect directly
- a short list of remaining risks or follow-up work
- enough confidence to decide whether the default workflow is useful in this repo

## After the quickstart

- Need the fuller first-run walkthrough? Open [Getting Started](getting-started.md).
- Need config answers? Open [Configuration Reference](configuration.md).
- Need docs routed by use case? Open [End-User Stories](user-stories.md).
- Need to inspect trustworthy output? Open [What Good Ralph Workflow Output Looks Like](../../../docs/reviewable-output.md).
""",
    MIRROR / 'ralph-workflow' / 'docs' / 'sphinx' / 'index.rst': """.. title:: Ralph Workflow manual

Ralph Workflow
==============

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.
It turns that simple structure into a stronger composable workflow for substantial, well-specified repo work, and the default workflow is already good enough to start using before you customize anything.

This page is the maintained operator manual home.
If you are brand new, start with :doc:`getting-started`.
If you need configuration or operator detail, start with :doc:`configuration` or :doc:`reference`.
If you need docs grouped by real user goal, start with :doc:`user-stories`.

Manual paths
============

First run
---------

- :doc:`getting-started`
- :doc:`quickstart`
- :doc:`first-task-guide`

Configuration and customization
-------------------------------

- :doc:`configuration`
- :doc:`reference`
- :doc:`advanced-pipeline-configuration`
- :doc:`advanced-artifact-configuration`
- :doc:`advanced-mcp-configuration`
- :doc:`policy-explanation`

Use-case routing
----------------

- :doc:`user-stories`
- :doc:`which-agent-should-i-start-with`
- :doc:`when-unattended-coding-fits`

.. toctree::
   :hidden:

   getting-started
   quickstart
   first-task-guide
   configuration
   reference
   advanced-pipeline-configuration
   advanced-artifact-configuration
   advanced-mcp-configuration
   policy-explanation
   user-stories
   which-agent-should-i-start-with
   when-unattended-coding-fits
""",
    MIRROR / 'ralph-workflow' / 'docs' / 'sphinx' / 'user-stories.md': """# End-User Stories

This page is the plain-English route map for real user goals.
Use it when you know what you are trying to do but do not care which doc family contains the answer.

## I am brand new and want the fastest honest first run

- [Getting Started](getting-started.md)
- [Quickstart](quickstart.md)
- [Choose Your First Ralph Workflow Task](first-task-guide.md)

## I want to know whether my task is even a good fit

- [When Unattended Coding Fits](when-unattended-coding-fits.md)
- [Choose Your First Ralph Workflow Task](first-task-guide.md)

## I already use Claude Code, Codex, or OpenCode and want a baseline comparison

- [Ralph Workflow vs Claude Code](ralph-workflow-vs-claude-code.md)
- [Ralph Workflow vs Codex CLI](ralph-workflow-vs-codex-cli.md)
- [Ralph Workflow vs OpenCode](ralph-workflow-vs-opencode.md)

## I want to run work overnight without babysitting the terminal

- [When Unattended Coding Fits](when-unattended-coding-fits.md)
- [Run Claude Code Overnight Without Babysitting](run-claude-code-overnight-without-babysitting.md)

## I want to edit `ralph-workflow.toml`

- [Configuration Reference](configuration.md)

Short answer:

- global defaults → `~/.config/ralph-workflow.toml`
- repo-specific override → `.agent/ralph-workflow.toml`
- workflow structure changes → `.agent/pipeline.toml`

## I want to change which agents Ralph uses

- [Configuration Reference](configuration.md)
- [Which Agent Should I Start With?](which-agent-should-i-start-with.md)

## I want one repo to behave differently from my global defaults

- [Configuration Reference](configuration.md)

Then create a project-local override with:

```bash
ralph --init-local-config
```

## I want to understand what my current workflow policy actually does

- [Policy Explanation](policy-explanation.md)

And run:

```bash
ralph --check-policy
ralph --explain-policy
```

## I want advanced docs for `pipeline.toml`

- [Advanced Pipeline Configuration](advanced-pipeline-configuration.md)

## I want advanced docs for `artifacts.toml`

- [Advanced Artifact Configuration](advanced-artifact-configuration.md)

## I want advanced docs for `mcp.toml`

- [Advanced MCP Configuration](advanced-mcp-configuration.md)

## I want to review whether the result is trustworthy after a run

- [How to Review AI Coding Output Before You Merge](review-ai-coding-output-before-merge.md)
- [After Your First Ralph Workflow Run](after-your-first-run.md)

## I want to see proof before I install anything

- [What Good Ralph Workflow Output Looks Like](../../../docs/reviewable-output.md)

## I want the command and flag reference

- [CLI Reference](cli.md)

## I am not an end user — I need internals or implementation detail

- [Developer Reference](developer-reference.md)
""",
}


def public_doc_candidates() -> list[Path]:
    paths: list[Path] = []
    for root in PUBLIC_DOC_ROOTS:
        if not root.exists():
            continue
        for path in root.rglob('*'):
            if not path.is_file():
                continue
            if path.suffix.lower() != '.md':
                continue
            if path.name in PUBLIC_DOC_EXCLUDE_FILES:
                continue
            relative_parts = set(path.relative_to(root).parts)
            if relative_parts & PUBLIC_DOC_EXCLUDE_DIR_PARTS:
                continue
            paths.append(path)
    return sorted(set(paths))


def tailored_intro(path: Path) -> list[str]:
    name = path.name
    text = name.lower()
    if name == 'README.md' and path.parent.name == 'docs':
        return [
            'Use this page after [README.md](../README.md) and [START_HERE.md](../START_HERE.md).',
            'Those pages explain what Ralph Workflow is and how to judge one honest first run; this page only routes you to the right next doc.',
            '',
        ]
    if name == 'getting-started.md':
        return [
            'This guide takes you from install to one honest unattended run in a repository you already care about.',
            'It is the fuller first-run walkthrough for Ralph Workflow\'s default operator path.',
            '',
        ]
    if name == 'quickstart.md':
        return [
            'Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core that composes into more complex workflows for substantial, well-specified repo work.',
            'The shipped default workflow is already strong enough to start with; this page just gets you to one honest first run quickly.',
            '',
        ]
    if name == 'configuration.md':
        return [
            'Ralph Workflow is an AI agent orchestrator with a simple Ralph-loop core that stays understandable while composing into more complex workflows.',
            'Use this reference when your question is which file to edit, which scope wins, or how to validate a config change safely.',
            '',
        ]
    if name == 'reference.md':
        return [
            'Use this reference after the product story is already clear and you need day-to-day operator answers quickly.',
            'It exists to answer commands, files, and behavior questions without sending you back through onboarding copy.',
            '',
        ]
    if name == 'user-stories.md':
        return [
            'This page is the plain-English route map for real user goals.',
            'Use it when you know what you are trying to do but do not care which doc family contains the answer.',
            '',
        ]
    if name == 'first-task-guide.md':
        return [
            'Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core that composes into more complex workflows for substantial, well-specified repo work.',
            'Use this page to choose a first task that lets the shipped default workflow prove itself honestly.',
            '',
        ]
    if name == 'spec-driven-ai-agent.md':
        return [
            'Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core that composes into more complex workflows for substantial, well-specified repo work.',
            'Use this page when you need to understand why written scope is what lets the default workflow stay honest instead of sounding impressive.',
            '',
        ]
    if name == 'unattended-coding-agent.md':
        return [
            'Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core that composes into more complex workflows for substantial, well-specified repo work.',
            'The shipped default workflow is already strong enough to start with; this page explains when unattended use is actually a good fit.',
            '',
        ]
    if name == 'ai-agent-orchestration-cli.md':
        return [
            'Ralph Workflow is an AI agent orchestrator built around a simple Ralph-loop core that composes into more complex workflows for substantial, well-specified repo work.',
            'The shipped default workflow is already strong enough to start with, and this page explains why that matters when you compare orchestration CLIs.',
            '',
        ]
    if text.startswith('advanced-'):
        topic = text.removeprefix('advanced-').removesuffix('.md').replace('-', ' ')
        return [
            'Ralph Workflow keeps a simple Ralph-loop core at the center and composes outward into stronger workflows only when you need them.',
            f'Use this page when the shipped default workflow is already making sense and you need deeper {topic} control for substantial repo work.',
            '',
        ]
    if text.startswith('ralph-workflow-vs-') or '/vs-' in str(path).lower():
        return [
            'Use this comparison page when you already know another coding-agent tool and want the clearest practical difference.',
            'Its job is to explain fit and tradeoffs, not to restate the full manual.',
            '',
        ]
    if any(token in text for token in ['review', 'proof', 'after-your-first-run', 'finish-receipt']):
        return [
            'Use this page after you already understand the workflow and want a review standard or proof surface.',
            'This page is supporting proof for Ralph Workflow, not the product pitch. Start with the product story and operator route first.',
            '',
        ]
    if any(token in text for token in ['first-task', 'task', 'spec-driven', 'unattended', 'which-agent', 'when-unattended']):
        return [
            'Use this page when you are deciding whether Ralph Workflow fits the task in front of you and what to do next.',
            'Its job is to sharpen judgment and routing, not to duplicate install docs.',
            '',
        ]
    return [
        'Ralph Workflow is an AI agent orchestrator for substantial, well-specified repo work on your own machine.',
        'This page exists to explain one specific part of that system without repeating the whole onboarding story.',
        '',
    ]


def replace_generic_intro(path: Path, text: str) -> str:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines[:12]):
        if line.startswith('# '):
            start = i + 1
            break
    if start is None:
        return text
    j = start
    while j < len(lines) and not lines[j].strip():
        j += 1
    intro_heads = (
        'Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core.',
        'Ralph Workflow is a free and open-source AI agent orchestrator built around a simple Ralph-loop core.',
        'Ralph Workflow is a free and open-source AI agent orchestrator built around a simple core loop inspired by the original Ralph loop.',
        'Ralph Workflow is a free and open-source AI agent orchestration system built around a simple core loop inspired by the original Ralph loop.',
    )
    if j >= len(lines) or not any(lines[j].startswith(head) for head in intro_heads):
        return text
    k = j + 1
    while k < len(lines):
        line = lines[k]
        if not line.strip():
            k += 1
            if k < len(lines) and lines[k].strip():
                break
            continue
        if line.startswith('#') or line.startswith('##') or line.startswith('> ') or line.startswith('.. '):
            break
        k += 1
        if k - j >= 4:
            break
    new_lines = lines[:start] + [''] + tailored_intro(path) + lines[k:]
    return '\n'.join(new_lines).rstrip() + '\n'


def dedupe_lead_blocks(text: str) -> str:
    lines = text.splitlines()
    try:
        head_idx = next(i for i, line in enumerate(lines[:12]) if line.startswith('# '))
    except StopIteration:
        return text
    body = lines[head_idx + 1 :]
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in body:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    if len(blocks) < 2:
        return text

    def joined(block: list[str]) -> str:
        return ' '.join(part.strip() for part in block).strip().lower()

    first = joined(blocks[0])
    second = joined(blocks[1])
    if not first or not second:
        return text
    duplicateish = first == second or (first.startswith('use this page') and second.startswith('use this page')) or (first.startswith('this page') and second.startswith('this page'))
    if not duplicateish:
        return text

    rendered = lines[: head_idx + 1] + [''] + blocks[0] + ['']
    rendered_blocks = blocks[2:]
    for idx, block in enumerate(rendered_blocks):
        rendered.extend(block)
        if idx != len(rendered_blocks) - 1:
            rendered.append('')
    return '\n'.join(rendered).rstrip() + '\n'


def collapse_duplicate_opening_pairs(text: str) -> str:
    lines = text.splitlines()
    if len(lines) < 8 or not lines[0].startswith('# '):
        return text
    pair1 = lines[2:4]
    pair2 = lines[5:7]
    if len(pair1) == 2 and len(pair2) == 2 and pair1 == pair2 and not lines[4].strip():
        rebuilt = lines[:5] + lines[7:]
        return '\n'.join(rebuilt).rstrip() + '\n'
    return text


def strip_legacy_intro_blocks(path: Path, text: str) -> str:
    replacements = {
        'quickstart.md': (
            "\n\nUse this page when you already understand the product story and want the shortest path to one honest first run in a real repository.\nIts job is to get you through the default workflow quickly, not to restate the full product overview.\nIf you need fuller explanation, task-selection help, or more context for why the default flow works, go back to [Getting Started](getting-started.md).\n",
            "\n",
        ),
        'reference.md': (
            "\n\nUse this reference after the product story is already clear and you need day-to-day operator answers quickly.\nIt exists to answer commands, files, and behavior questions without sending you back through onboarding copy.\n",
            "\n",
        ),
        'advanced-pipeline-configuration.md': (
            "\n\nUse this page when the default workflow is already making sense and you need deeper pipeline configuration control.\nIt is for deliberate operator customization, not for the first-run path.\n\nUse this page when the default workflow is already making sense and you need deeper pipeline configuration control.\nIt is for deliberate operator customization, not for the first-run path.\n",
            "\n",
        ),
        'advanced-artifact-configuration.md': (
            "\n\nUse this page when the default workflow is already making sense and you need deeper artifact configuration control.\nIt is for deliberate operator customization, not for the first-run path.\n\nUse this page when the default workflow is already making sense and you need deeper artifact configuration control.\nIt is for deliberate operator customization, not for the first-run path.\n",
            "\n",
        ),
        'advanced-mcp-configuration.md': (
            "\n\nUse this page when the default workflow is already making sense and you need deeper mcp configuration control.\nIt is for deliberate operator customization, not for the first-run path.\n\nUse this page when the default workflow is already making sense and you need deeper mcp configuration control.\nIt is for deliberate operator customization, not for the first-run path.\n",
            "\n",
        ),
    }
    old_new = replacements.get(path.name)
    if not old_new:
        return text
    old, new = old_new
    return text.replace(old, new)


def inject_intro_if_needed(path: Path, text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return text
    screen = '\n'.join(lines[:40]).lower()
    needs_ai = not any(needle in screen for needle in ['ai agent orchestrator', 'ai agent orchestration', 'operating system for autonomous coding'])
    needs_comp = 'composable workflow' not in screen and 'compose' not in screen and 'composition' not in screen and 'workflow system' not in screen
    needs_default = 'strong default workflow' not in screen and 'default workflow' not in screen
    if not any([needs_ai, needs_comp, needs_default]):
        return text

    intro = tailored_intro(path)

    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith('# '):
            insert_at = i + 1
            break
    new_lines = lines[:insert_at] + [''] + intro + lines[insert_at:]
    return '\n'.join(new_lines).rstrip() + '\n'


def sanitize_public_doc(path: Path, text: str) -> str:
    updated = replace_generic_intro(path, text)
    for pattern, new in BANNED_REPLACEMENTS.items():
        updated = re.sub(pattern, new, updated, flags=re.IGNORECASE)
    updated = inject_intro_if_needed(path, updated)
    updated = dedupe_lead_blocks(updated)
    updated = collapse_duplicate_opening_pairs(updated)
    updated = strip_legacy_intro_blocks(path, updated)
    updated = re.sub(
        r'(Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core\.\nThat simple core composes into a stronger composable workflow system for substantial, well-specified repo work, and the default workflow is already strong enough to start with before you customize anything\.\n\n)\n*\1',
        r'\1',
        updated,
        flags=re.MULTILINE,
    )
    updated = re.sub(
        r'(Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph-loop core\.\n)\n*\1',
        r'\1',
        updated,
        flags=re.MULTILINE,
    )
    if path.name == 'configuration.md':
        getting_started_note = '> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) before diving into config details.\n\n'
        if 'getting-started.md' not in updated[:1000]:
            updated = updated.replace(
                '# Configuration Reference\n\n',
                '# Configuration Reference\n\n' + getting_started_note,
                1,
            )
        if 'user-stories' not in updated[:1200].lower():
            updated = updated.replace(
                '# Configuration Reference\n\n',
                '# Configuration Reference\n\nUse this page when your question is about files, precedence, validation commands, or configuration edits. If you want docs routed by use case instead of page type, open [End-User Stories](user-stories.md).\n\n',
                1,
            )
    if path.name in {'quickstart.md', 'getting-started.md'} and 'user-stories' not in updated[:1200].lower():
        updated = updated.replace('before you customize anything.\n\n', 'before you customize anything. If you need config answers, open [Configuration Reference](configuration.md). If you want docs routed by use case, open [End-User Stories](user-stories.md).\n\n', 1)
    return updated


def main() -> int:
    changed: list[Path] = []
    for path, content in REWRITES.items():
        current = path.read_text(encoding='utf-8') if path.exists() else None
        if current != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
            changed.append(path)

    for path in public_doc_candidates():
        current = path.read_text(encoding='utf-8')
        updated = sanitize_public_doc(path, current)
        if updated != current:
            path.write_text(updated, encoding='utf-8')
            if path not in changed:
                changed.append(path)

    if not changed:
        print('NO_CHANGES')
        return 0
    print('REMEDIATED')
    for path in changed:
        print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
