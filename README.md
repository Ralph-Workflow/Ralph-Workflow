# Ralph Workflow — the autopilot for coding agents

> **Codeberg is primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror:
> <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

## What it is

Ralph Workflow is **the operating system for autonomous coding** — a
free and open-source **AI agent orchestrator** built on a **simple
Ralph-loop core** that becomes **powerful in composition**. It ships
with a **strong default workflow for writing software** that you can
adopt **as-is first** and extend later.

Hand it a well-specified coding task, let the agents plan, build,
verify, and fix, and come back to reviewable, tested work. The full
operator manual lives under
[`ralph-workflow/docs/sphinx/`](ralph-workflow/docs/sphinx/index.rst).

## Who it's for

Ralph Workflow fits developers and small teams with engineering work that
is too big to babysit and too risky to trust blindly:

- **The solo builder.** Side projects with real spec depth — you know
  what to build, but you're one person. Set `PROMPT.md` before bed,
  wake up to reviewed commits.
- **The team lead.** The work fits between PR and review — unattended
  verification that your agents shipped what you asked for, not what
  they guessed.
- **The AI tool builder.** You are already wiring Claude Code (or
  Codex, OpenCode, Nanocoder, AGY, Pi) into your workflow. Ralph
  Workflow gives you the loop pattern — phase routing, recovery,
  checkpointing — as infrastructure instead of something you'd build
  yourself.

**Ralph Workflow is not for** one-line fixes, vague prompts, or repos
without tests. It is for ambitious, well-specified work you would
trust a capable colleague to do unattended. A repo without guardrails
will produce results that reflect that.

## Start your first run

```bash
pipx install ralph-workflow        # 1. install
cd /path/to/your/project           # 2. pick a real repo
ralph --init                       # 3. scaffold .agent/ + PROMPT.md
ralph --diagnose                   # 4. pre-flight: verify agents, MCP, capabilities
$EDITOR PROMPT.md                  # 5. write the task — see PROMPT.md template
ralph                              # 6. run the unattended workflow
```

Run those commands from a human-operated shell outside any Ralph-managed
agent session.

- `ralph --init` provisions the default local work surface and shipped
  baseline skills.
- `ralph --diagnose` is the pre-flight check: it verifies your agent
  CLIs, MCP servers, and capability bundles are healthy. See the
  [diagnostics page](ralph-workflow/docs/sphinx/diagnostics.md) for
  what each check proves.

When you come back, ask one question: **would I merge this?** If yes,
give it a harder task next. If no, tighten the spec, the checks, or
the task choice and run again.

The shortest path is [START_HERE.md](START_HERE.md). The full
walkthrough is in the [maintained operator manual](ralph-workflow/docs/sphinx/index.rst).

## Trust and safety boundaries

These are not negotiable. They are how the tool is designed:

- **Local execution.** Ralph Workflow runs on your machine. It does not
  upload your code or data to a cloud service. Crash reports are
  anonymous and opt-out-able — see *Privacy* in the package README.
- **Agent authentication is yours, not Ralph's.** Ralph Workflow does
  not store, read, or proxy agent credentials. Each agent CLI uses
  its own native authentication (vendor login or API key). You
  authenticate each agent first, and Ralph Workflow then invokes those
  CLIs as-is.
- **Branch / worktree expectations.** A long run writes files and may
  create branches. Run on a clean worktree and review with your normal
  git workflow before merge.
- **Unattended approval implications.** "Unattended" means agents may
  keep writing while you sleep. Have backups and branch protection.
- **Cost.** Agent calls are on your cloud bill. Ralph Workflow itself
  has no per-run fee.
- **Human responsibility.** Agents handle the long middle. You handle
  the judgment: run the finished program against your real environment
  and data, exercise the feature, check the behavior against your
  original intent, inspect the receipts, then decide the next action —
  push a solo-dev branch, merge a pull request, ask for changes, revert,
  rerun, or throw the result away. Code review is supporting evidence,
  not the only acceptance mechanism.

## Supported agents

Ralph Workflow ships with first-class support for six user-facing agent
CLIs: Claude Code (interactive + headless), Codex, OpenCode, Nanocoder,
Google Anti Gravity (AGY), and Pi. See
[Agent Compatibility](ralph-workflow/docs/sphinx/agent-compatibility.md)
for the full matrix and known caveats.

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

The shortest read is README → START_HERE → docs-map → manual:

1. **README.md** (this file) — what it is, who it's for, fastest install
2. **[START_HERE.md](START_HERE.md)** — guided first run
3. **[docs/README.md](docs/README.md)** — route by intent
4. **[ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst)**
   — the maintained operator manual

For contributor and architecture detail, see
[`docs/architecture/`](docs/architecture/README.md) and
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## Ecosystem and attribution

The Ralph Loop pattern is attributed to
[Geoffrey Huntley (ghuntley.com/ralph)](https://ghuntley.com/ralph).
Ralph Workflow is an independent reference implementation — not the
pattern's originator.

## What a run leaves you

This is the actual finish-receipt from a real bundled example — a real,
unedited handoff you read in the morning instead of a transcript:

```text
# Development Result

## Outcome
Implemented empty-name validation in the CLI create flow and added
test coverage for empty and whitespace-only input.

## Changed files
- cli/create.py
- tests/test_create.py

## Checks run
- pytest tests/test_create.py        ✓ passed
- project formatting / lint checks    ✓ passed

## Reviewer focus
- confirm validation happens before any file creation side effect
- confirm the error message is clear enough for CLI users
- confirm no unrelated flow changed
```

For the meaning of each finish-receipt block, see the
[operator manual](ralph-workflow/docs/sphinx/index.rst).