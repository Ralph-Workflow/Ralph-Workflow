# Ralph Workflow

> **Write the spec. Wake up to working software.**
>
> **The operating system for autonomous coding.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>

Ralph Workflow is a free and open-source orchestration CLI
for AI coding agents on your own machine.
You write the task in `PROMPT.md`, Ralph runs planning, coding,
and review through the agents you choose,
and you come back to a reviewable result:
a real diff, checks, logs, and concrete artifacts.

## Quick answer: is this for you?

- **What is it?** A CLI for unattended, reviewable AI coding runs.
- **Who is it for?** Developers and technical teams with work too big to babysit and too risky to trust blindly.
- **Why is it different?** It is built to hand back a reviewable result, not just a transcript.
- **Why use it now?** You can try one real backlog task tonight and decide tomorrow whether you would merge the outcome.
- **If you cannot judge a diff or merge decision, this is probably not for you yet.**

## The shortest evaluator path most people actually need

1. **Inspect the primary repo on Codeberg first** — <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. **Choose one real first task** — [docs/first-task-guide.md](./docs/first-task-guide.md)
3. **Run the shortest practical first run** — [START_HERE.md](./START_HERE.md)
4. **Judge the handoff** — [docs/reviewable-output.md](./docs/reviewable-output.md)
5. **Turn the result into a Codeberg action** — [docs/after-your-first-run.md](./docs/after-your-first-run.md)

If the blank page is the blocker, steal a starter spec from [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).

## What you should get back

A useful run should be reviewable before you read a long log.

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

That is the promise: a bounded diff, checks that actually ran, and a clear merge decision.

If the run feels unclear, shaky, or harder than it should, report it on Codeberg: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>

## Tonight's first run in five minutes

Prerequisites:

- Python 3.12+
- a git repo you can safely test in
- at least one supported agent CLI already installed and authenticated

If you are unsure which agent to start with,
use the one already working on your machine,
then read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

Paste a spec this small into `PROMPT.md`:

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

Then ask one question:

> **Would I merge this?**

## Why teams use Ralph Workflow

- **Write a spec, not a babysitting script.**
- **Wake up to reviewable output.**
- **Use the agents you already have.**
- **Keep the workflow in the repo.**
- **Aim past prototypes.**

## Install

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

### pipx

```bash
pipx install ralph-workflow
ralph --help
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
pip install -e ".[dev]"
ralph --version
```

Requires Python 3.12+.

## Before your first run

Make sure the agent CLIs you want Ralph to call are already installed and authenticated. Ralph Workflow does not manage provider login state or touch your credentials.

For detailed usage, start with the [crate README](ralph-workflow/README.md).
For the short docs path, use [docs/quick-reference.md](docs/quick-reference.md).

**What fits best**

Good first tasks:

- a bounded feature slice
- a narrow refactor with tests
- a known cleanup task with clear checks
- repetitive implementation work where done is easy to judge

Bad first tasks:

- vague product exploration
- risky production surgery
- tiny tasks where setup overhead dominates
- workflows that depend on unpredictable mid-run human input

## Need one deeper answer?

Keep this README for onboarding. Then choose only the nearest next page:

- fastest first run — [START_HERE.md](./START_HERE.md)
- handoff standard — [docs/reviewable-output.md](./docs/reviewable-output.md)
- first task selection — [docs/first-task-guide.md](./docs/first-task-guide.md)
- agent choice — [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md)

If you still need the full map, use [docs/README.md](./docs/README.md).
For the product site, use <https://ralphworkflow.com>.

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you.