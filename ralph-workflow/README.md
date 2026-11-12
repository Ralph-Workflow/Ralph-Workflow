# ralph-workflow

Ralph Workflow is a free, open-source AI agent orchestrator for coding
work. You give it one well-specified task; it runs a **Ralph loop**
(plan → build → verify → fix) with your chosen coding agent, then you
come back to inspect the result. Adopt the default workflow as-is
first; extend it later.

It fits developers and small teams with work that is too big to babysit
and too risky to trust blindly. It is not for vague prompts or repos
without tests or other guardrails.

## Install

From a checkout on Linux, macOS, or WSL, with `uv` 0.7.0 or newer, choose
the build you need:

```bash
cd ralph-workflow
make install       # self-contained manual snapshot; rdev --version ends in -build
make dev           # self-contained development snapshot; rdev --version ends in -dev
make stable        # published release; ralph --version has no local suffix
```

`make install` and `make dev` copy the checkout and its bundled templates to
their own snapshot, so `rdev` does not depend on the cloned repository. They
install `rdev` only: an existing global `ralph` is reported and left untouched,
and `rdev` is used instead. Any `rdev` from an earlier dev build is replaced.
`make stable` owns the global `ralph`, so before it changes files you choose in
a terminal to continue, remove a pipx/uv-tool install, or abort; non-interactive
conflicts abort safely.

On native Windows, use the published package: `pipx install ralph-workflow` or
`pip install ralph-workflow`. The checkout installer is a POSIX/WSL workflow.
For its locked, serialized, rollback-capable refresh contract and failure modes,
see [CONTRIBUTING.md](CONTRIBUTING.md#dev-build-vs-stable-build).

## First run

1. Install Ralph Workflow with the build that fits your workflow (above).
2. In your project: `ralph --init` — creates user-global config and a
   starter `PROMPT.md`; it does not create `.agent/`. Advanced project-local
   overrides are explicit opt-in via `ralph --init-local-config` (alias:
   `ralph --generate-local-config`).
3. Confirm a supported agent CLI is on your `PATH` and authenticated
   (`ralph --list-agents`).
4. Run `ralph --diagnose` and fix any reported problem until every line
   is green.
5. Edit `PROMPT.md` with the outcome and checks you expect. Remove the
   `<!-- ralph:starter-prompt ... -->` sentinel at the top — Ralph Workflow
   refuses to run while it remains. Or, before `PROMPT.md` exists, seed a
   task-shaped starter with `ralph --init feature-spec`, `guardrail`,
   `refactor`, `test-coverage`, or `docs`.
6. Run `ralph`. When it finishes, read the summary of what changed and
   which checks passed, then verify the feature yourself.

The canonical first-run walkthrough is
[Getting started](docs/sphinx/getting-started.md). For agent-specific
model-string formats, see
[Agent compatibility](docs/sphinx/agent-compatibility.md). For
configuration after the first run, open the
[operator manual](docs/sphinx/index.rst).

### Auto-integration ownership

Ralph Workflow refreshes the configured mainline before integration seams. The worktree
that owns that branch is infrastructure: with the default configuration Ralph Workflow
may snapshot and reset its uncommitted content to keep the mainline current.
Recover discarded contents with
`git -C <target-owner-path> restore --source=<snapshot-ref> --staged --worktree .`.
Snapshots remain under `refs/ralph-reclaim/<target>/...`; feature worktrees are never reclaimed. Set
`auto_integrate_reclaim_target_worktree = false` in `ralph-workflow.toml` to
keep the older refuse-and-retry behavior. See the
[configuration reference](docs/sphinx/configuration.md#auto-integration) for
all six settings.

When merge or rebase conflict resolution retains progress for an operator or
another agent, rerun `ralph --retry` (or `ralph --retry-integration`) to force
re-entry into the retained resolution flow. The command bypasses the durable
continuation retry gate for one attempt without spinning in repeated critical
alarms.

### Accidental damage protection: two-stage commit review

Before any code is committed, Ralph Workflow evaluates the pending diff through a two-stage commit review gate. It is a **damage gate, not a code review**: it asks only whether the diff looks like accidental damage. Code quality and plan adherence belong to the analysis phase.

1. **Deterministic tripwires:** The engine seals pending changes into an isolated proposal ref (`refs/ralph/proposals/<run>/<iteration>`) and checks deterministic damage tripwires against `HEAD`. Hard tripwires — mass deletion beyond 50 deleted files or 25% of tracked files, or removal of protected paths like `.git`, manifests, lockfiles, or `AGENTS.md` — reject automatically without an agent. Out-of-scope paths, staged secrets, and binaries over 1 MB are softer findings: they reach the agent as review evidence, where a flagged binary can be offered for cleanup. A secret or oversized binary that the edit step could not remove is rejected on the engine's own evidence.
2. **Agent review:** An agent inspects the sealed proposal diff and Stage 1 evidence, executes permitted cleanup actions (`delete_file`, `add_to_gitignore`, `add_to_git_exclude`), and submits an approved or rejected verdict bound to the sealed tree SHA.

On rejection, the engine performs a crash-safe rollback restoring the tree to `HEAD` (or dirty run-start baseline) while preserving the proposal ref for inspection. The run returns to development, and the consecutive-rejection ceiling (default 2) makes the second consecutive rejection terminal, without another rollback. Recover discarded changes at any time via:

```bash
git checkout refs/ralph/proposals/<run>/<iteration> -- .
```

Four operating assumptions are worth knowing before you rely on the gate:

- The rollback uses **neither `git stash` nor a saved patch** — it restores tracked files to `HEAD` (or the run-start baseline seal when the run started on a dirty tree) and removes only the files the proposal added; the sealed proposal ref is the recovery handle, not the restore source.
- A rejection that arrives after the cycle timebox expires routes to `failed_terminal`, not back to development.
- An intentional large deletion gets **no plan-based bypass**: raise `max_deleted_files` / `max_deleted_fraction` in the pipeline policy TOML when you expect one.
- The pre-rename phase names are not migrated. An in-flight run still holding pre-rename cleanup phases is unsupported — start a fresh run after upgrading.

Per-setting details live in the
[configuration reference](docs/sphinx/configuration.md#commit-review-configuration).

### Architecture: one source of truth per domain fact

Every lifecycle fact in Ralph Workflow has exactly one authoritative
owner and one transition authority. Pipeline progression is owned
by `ralph/pipeline/_pipeline_state_machine.py`; integration
progression by `ralph/pipeline/_integration_action.py`; terminal
eligibility by `ralph/pipeline/_terminal_authority.py`; operation-
scoped evidence by `ralph/pipeline/operation_evidence.py`. The
post-fix closure (residual prose-comparison removal, defensive
collector guard, public-boundary matrix) is recorded in
[ADR-0010](docs/architecture/adr-0010-post-fix-source-of-truth.md),
building on
[ADR-0009](docs/architecture/adr-0009-pipeline-source-of-truth-ownership.md).

## Supported agents

Nine built-in agents ship with Ralph Workflow:

| Agent | Notes |
|---|---|
| **Claude Code** | Anthropic's CLI for Claude (interactive, PTY transport). |
| **Claude Code (Headless)** | Same `claude` binary in headless subprocess mode (`claude-headless`). |
| **Codex** | OpenAI's Codex CLI. |
| **OpenCode** | Open-source terminal coding agent. |
| **Nanocoder** | Local-only TUI coding agent. |
| **Google Anti Gravity (AGY)** | Google's Antigravity CLI (`agy`). Re-check after AGY updates. |
| **Pi** | Minimal coding agent. Headless mode is `pi --mode json <prompt>`. |
| **Cursor** | Cursor Agent CLI (`agent`), headless `--print` mode. |
| **Kimi** | Kimi Code CLI (`kimi`), headless `-p <prompt>` + `--output-format stream-json`; `kimi/<model>` aliases use full configured IDs. |

Pick one, authenticate it on your machine once, and Ralph Workflow uses
it. Selection and trust-boundary details are in
[agents](docs/sphinx/agents.md) and
[agent-compatibility](docs/sphinx/agent-compatibility.md).

## Requirements

- Python ≥ 3.12
- `uv` ≥ 0.7.0 for checkout and stable Makefile installs
- Local execution; no daemon, no cloud dependency
- One supported agent CLI installed and authenticated

## License

AGPL-3.0-or-later.

## Documentation

The maintained operator manual is at
[`docs/sphinx/index.rst`](docs/sphinx/index.rst) — tutorial,
configuration reference, MCP / artifact / pipeline configuration,
concepts, troubleshooting, diagnostics, and developer internals.

## Engine-owned writes are auto-committed

Ralph Workflow's own code writes a small set of tracked files in
the background: the project-scope artifact skills (across all three
installer routes — run start, `ralph --init`, and
`ralph --force-init-skills`), the project's `.gitignore` (seeded
with the batteries-included defaults on first run), and the
project-policy surfaces (`docs/ralph-workflow-policy/`, `AGENTS.md`,
`CLAUDE.md`). Every one of those writers commits its own diff
immediately, with a fixed conventional-commit subject, so the
working tree is never left dirty for the next agent to inherit.

The deterministic chore commit is restricted to the byte-exact
set of paths the writer touched; agent or user edits to those
paths (or to any other file) are never swept in. The full contract
lives in
[`../docs/agents/deterministic-auto-commit.md`](../docs/agents/deterministic-auto-commit.md)
and is pinned by the `audit_skill_auto_commit` gate step.

## Skills

Ralph Workflow installs two kinds of skills.

**Artifact skills** ship with Ralph Workflow and are installed into the
project scope. The set follows the pipeline's artifact types automatically:
adding an artifact type adds its skill, so adding a skill now means adding
an artifact type.

**Universal skills** are exactly three — `test-driven-development`,
`systematic-debugging`, and `verification-before-completion` — fetched from
the single upstream `obra/superpowers` at release versions and installed
user-global, never committed into your project, so upstream changes never
dirty your git tree. Ralph Workflow checks for a newer release at the start
of a run, at most once per day, as a background attempt: the check never
fails a run or noticeably slows one, and an offline, rate-limited, slow, or
malformed upstream leaves the installed skills exactly as they are and is
retried later. No upstream copy is bundled, so there is nothing that can go
stale; a machine that is never online simply has no universal skills, while
the artifact skills still work.

Two independent switches turn every skill-update network request off:
`universal_updates_enabled = false` under `[skills]` in
`~/.config/ralph-workflow.toml`, and the `RALPH_DISABLE_SKILL_UPDATES`
environment variable. See the
[configuration reference](docs/sphinx/configuration.md#skills) for both, the
update safety rules, the trust statement, and the migration behavior after
upgrading.

## Shipped policy pack and readiness (v6)

The shipped policy pack is the starter templates and readiness
machinery in `ralph/project_policy/`. Those files live inside the
ralph-workflow package. Ralph Workflow copies them into the user project as
`docs/ralph-workflow-policy/`, `AGENTS.md`, and `CLAUDE.md`; those
three surfaces are auto-updated by the engine and are never hand-edited.

The v6 model is two layers. The first is a small outcome-oriented
execution kernel: five generic rules (Truthful evidence, Run every
gate, No weakening, Policy follows reality, Report failures honestly)
that ship once in `execution-kernel.md`
and are never rephrased in domain starters. The second is evidence-backed,
selectively applicable domain controls — each domain policy is loaded
only when its declared trigger fires. The default for a conditional
domain is "do not load it". Every applicable starter records its guards
in a `## Guard inventory` table: one row per guard with seven cells
(Guard, Owner, Protected outcome, Detected condition, Claim scope,
Enforcement, Retirement trigger). A rule that appears only in prose, without an
inventory row, is not enforceable.

The `Enforcement` cell names how a guard is enforced. Its value is
`deterministic: <id>` when a deterministic gate owns the rule, or
`advisory: <reason>; review: <trigger>` when no gate can own it yet. A
`deterministic` id resolves by exact string match against the ids the
project declares; in Ralph Workflow's own repository the `ralph/verify.py` step
labels resolve as well. Every new rule carries an evidence-strength tag
(`strong`, `moderate`, `anecdotal`, `none`); only `strong` and
`moderate` rules may be `deterministic` — the rest ship as `advisory`
with a review trigger.

Readiness is reported in three dimensions. `structure_complete`
means the validator is clean — it is the caching gate; a NOT-READY
project is not cached. `implementation_verified` means every recorded
gate ran to exit 0 AND the recorded `policy_content_hash` matches the
current readiness-evidence signature (hash-bound; recorded-outcome
not declared-gate bounded). `outcome_evidence_reviewed` means
`implementation_verified` AND every declared `RALPH-COMMAND` appears in
the ledger (command-completeness); the analysis/approval gate is a
separate pipeline gate and is not a dimension of this assessment.
Structural READY gates the cache; semantic correctness is reported
separately on all four surfaces: CLI emit, status bar, AGENTS.md
block, and the remediation prompt.
See `ralph/project_policy/pipeline_driver.py:244-285` for the
exact definitions.
The surfaces and the v6 model are documented in
`docs/architecture/project-policy-readiness-traceability.md`.

The v6 revision reframes the shipped policy as a small outcome-oriented
execution kernel plus evidence-backed, selectively applicable controls,
and adds the `Enforcement` column so every guard names the gate that owns
it; see the v6 history entry in `ralph/project_policy/markers.py` for the
reason and the v5-to-v6 consequence. The revision's outcomes are
measured against v5 in a paired replay; see
`docs/plans/policy-v5-paired-replay-plan.md` for the plan.

## Project home

- **Repository:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
- **PyPI:** <https://pypi.org/project/ralph-workflow/>
- **Issue tracker:** <https://github.com/Ralph-Workflow/Ralph-Workflow/issues/new>
- **Contribution route:**
  [`CONTRIBUTING.md`](CONTRIBUTING.md)
