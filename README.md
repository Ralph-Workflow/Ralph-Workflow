# Ralph Workflow

> **Codeberg is primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror:
> <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

## What it is

Ralph Workflow is a free and open-source AI agent orchestrator for coding
work. Hand it a well-specified task, let the agents plan, build, verify,
and fix, and come back to reviewable, tested work. The full operator
manual lives under
[`ralph-workflow/docs/sphinx/`](ralph-workflow/docs/sphinx/index.rst).

## Who it's for

Ralph Workflow fits developers and small teams with engineering work that
is too big to babysit and too risky to trust blindly:

- **The solo builder.** Side projects with real spec depth — you know
  what to build, but you're one person. Set `PROMPT.md` before bed,
  wake up to reviewed commits.
- **The team lead.** The work fits between PR and review — unattended
  verification that your agents shipped what you asked for.
- **The AI tool builder.** You are already wiring Claude Code (or
  Codex, OpenCode, Nanocoder, AGY, Pi) into your workflow. Ralph
  Workflow gives you the loop pattern — phase routing, recovery,
  checkpointing — as infrastructure.

**Ralph Workflow is not for** one-line fixes, vague prompts, or repos
without tests. A repo without guardrails will produce results that
reflect that.

## Start your first run

```bash
pipx install ralph-workflow        # 1. install
cd /path/to/your/project           # 2. pick a real repo
ralph --init                       # 3. scaffold .agent/ + PROMPT.md
ralph --diagnose                   # 4. pre-flight: agents, MCP, capabilities
$EDITOR PROMPT.md                  # 5. write the task
ralph                              # 6. run the unattended workflow
```

Run those commands from a human-operated shell outside any Ralph-managed
agent session. `ralph --init` provisions the default local work surface
and shipped baseline skills. `ralph --diagnose` is the pre-flight check.
See the
[diagnostics page](ralph-workflow/docs/sphinx/diagnostics.md) for what
each check proves.

When you come back, ask one question: **would I merge this?** The
morning-after review pattern is in
[Getting Started → Proof: what a run leaves you](ralph-workflow/docs/sphinx/getting-started.md#proof-what-a-run-leaves-you).

## Trust and safety boundaries

- **Local execution.** Ralph Workflow runs on your machine. It does not
  upload your code or data to a cloud service.
- **Agent authentication is yours, not Ralph's.** Ralph Workflow does
  not store, read, or proxy agent credentials.
- **Branch / worktree expectations.** A long run writes files and may
  create branches. Run on a clean worktree and review before merge.
- **Unattended approval implications.** "Unattended" means agents may
  keep writing while you sleep. Have backups and branch protection.
- **Cost.** Agent calls are on your cloud bill. Ralph Workflow itself
  has no per-run fee.
- **Human responsibility.** You handle the judgment: run the finished
  program against your real environment and data, exercise the
  feature, check the behavior against your original intent, inspect
  the receipts, then decide the next action.

## Supported agents

Ralph Workflow ships with first-class support for six user-facing agent
CLIs: Claude Code (interactive + headless), Codex, OpenCode, Nanocoder,
Google Anti Gravity (AGY), and Pi. See
[Agent Compatibility](ralph-workflow/docs/sphinx/agent-compatibility.md)
for the full matrix.

## Runtime, license, project home

- **Runtime:** Python ≥ 3.12. Local-first; no required cloud account.
- **License:** AGPL-3.0-or-later.
- **Project home (primary):** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **PyPI:** <https://pypi.org/project/ralph-workflow/>
- **Documentation site:** <https://ralphworkflow.com/docs>
- **Issue tracker:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Contribution route:** [CONTRIBUTING.md](CONTRIBUTING.md) →
  [ralph-workflow/CONTRIBUTING.md](ralph-workflow/CONTRIBUTING.md)

## Documentation route

README → START_HERE → docs map → manual:

1. **README.md** (this file)
2. **[START_HERE.md](START_HERE.md)** — guided first run
3. **[docs/README.md](docs/README.md)** — route by intent
4. **[ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst)**
   — the maintained operator manual

## Ecosystem and attribution

The Ralph Loop pattern is attributed to
[Geoffrey Huntley (ghuntley.com/ralph)](https://ghuntley.com/ralph).
Ralph Workflow is an independent reference implementation — not the
pattern's originator.
