# Ralph Workflow

> **Primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub mirror: <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

## What it is

Ralph Workflow is a free, open-source orchestrator for AI coding agents.
Hand it a well-specified task, let agents plan, build, verify, and fix,
and come back to reviewable, tested work. The full operator manual lives
under [`ralph-workflow/docs/sphinx/`](ralph-workflow/docs/sphinx/index.rst).

## Who it's for

Ralph Workflow fits developers and small teams with work that is too big
to babysit and too risky to trust blindly — the solo builder, the team
lead, or the tool builder wiring Claude Code, Codex, OpenCode, Nanocoder,
AGY, or Pi into a workflow. It is not for vague prompts or repos without
guardrails.

## Start your first run

```bash
pipx install ralph-workflow        # 1. install
cd /path/to/your/project           # 2. pick a real repo
ralph --init                       # 3. scaffold .agent/ + PROMPT.md
ralph --diagnose                   # 4. pre-flight
$EDITOR PROMPT.md                  # 5. write the task
ralph                              # 6. run the unattended workflow
```

See [Getting Started → Proof: what a run leaves you](ralph-workflow/docs/sphinx/getting-started.md#proof-what-a-run-leaves-you)
for the morning-after review pattern and the
[diagnostics page](ralph-workflow/docs/sphinx/diagnostics.md) for what
each pre-flight check proves.

## Trust and safety boundaries

- **Local execution.** Ralph Workflow runs on your machine.
- **Agent authentication is yours.** Ralph Workflow does not store or
  proxy agent credentials.
- **Worktree expectations.** A long run writes files and may create
  branches. Run on a clean worktree and review before merge.
- **Unattended approval.** Agents may keep writing while you sleep; have
  backups and branch protection.
- **Cost.** Agent calls are on your cloud bill; Ralph Workflow has no
  per-run fee.
- **Human responsibility.** You run the finished program against your
  real environment, exercise the feature, and judge whether the result
  matches the original intent.

## Documentation route

README → START_HERE → docs map → manual:

1. **[START_HERE.md](START_HERE.md)** — guided first run
2. **[docs/README.md](docs/README.md)** — route by reader intent
3. **[ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst)**
   — the maintained operator manual

## Runtime, license, project home

- **Runtime:** Python ≥ 3.12, local-first.
- **License:** AGPL-3.0-or-later.
- **Supported agents:** Claude Code (interactive + headless), Codex,
  OpenCode, Nanocoder, AGY, Pi. See
  [Agent Compatibility](ralph-workflow/docs/sphinx/agent-compatibility.md).
- **PyPI:** <https://pypi.org/project/ralph-workflow/>
- **Issue tracker:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Contribution route:** [CONTRIBUTING.md](CONTRIBUTING.md) →
  [ralph-workflow/CONTRIBUTING.md](ralph-workflow/CONTRIBUTING.md)

The Ralph Loop pattern is attributed to
[Geoffrey Huntley (ghuntley.com/ralph)](https://ghuntley.com/ralph);
Ralph Workflow is an independent reference implementation.
