<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: agent-policy.md -->

# Agent Policy

## Purpose and scope

This policy governs every AI agent working in this project. It applies
to every change made through Claude Code, OpenCode, Codex, Gemini,
Cursor, Nanocoder, AGY (Google Anti Gravity), Pi, or any other agent
shell that delegates to the Ralph Workflow runtime. It defines the
project instruction surface, the gate obligations, the truthfulness
obligations, and the documentation update obligations.

## Default requirements

* Every AI agent MUST read `AGENTS.md` and the canonical policy files
  under `docs/ralph-workflow-policy/` before changing the project.
* Every AI agent MUST follow the policies applicable to the files it
  touches (testing policy for tests, type-checking policy for typed
  code, etc.).
* Every AI agent MUST run the authoritative verification gate
  (`make -C ralph-workflow verify`) before claiming completion. The
  agent MUST report the actual command and outcome; "I think it
  works" is not evidence.
* Every AI agent MUST report failures accurately. Weakening a check
  to obtain a passing result is forbidden.
* Every AI agent MUST update affected policies and documentation in
  the same workflow that changes the underlying behaviour.
* Every AI agent MUST avoid unsupported claims about tools, commands,
  or dependency quality. The fabrication guard
  (`scripts/fabrication_guard.py` — level 1 pre-commit, level 2
  opt-in, level 3 with GITHUB_TOKEN) is the authoritative detector
  for unverified third-party claims in public-facing markdown.
* Every AI agent MUST preserve the canonical policy directory
  (`docs/ralph-workflow-policy/`) as the single source of truth for
  project quality policy.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents
rely on them when enforcing this policy and MUST keep them current as
the project evolves.

RALPH-FACT: supported_agents: Claude Code (interactive + headless), Codex, OpenCode, Nanocoder, AGY (Google Anti Gravity), Pi, Cursor, Gemini — 8 built-in agents wired through `ralph/agents/` and surfaced in `ralph-workflow/docs/sphinx/agents.md` and `agent-compatibility.md`. New agents register via `register_agent_support` (advanced) or `register_my_agent` (the 90% recipe) per `ralph-workflow/docs/agents/adding-a-new-agent.md`.
RALPH-FACT: agent_dispatch_command: `python -m ralph` (the runtime CLI; the `rdev` launcher for the dev build, the `ralph` launcher for the pinned stable build per `ralph-workflow/CONTRIBUTING.md`). All agent shells resolve to this CLI; there is no per-agent bespoke dispatcher.
RALPH-FACT: agent_review_process: development and fix phases MUST submit `development_result` with proof entries covering every plan step (and every prior `how_to_fix` item when `development_analysis` feedback exists). Review depends on a fresh per-phase artifact created during the current invocation; a clean subprocess exit is not enough evidence of useful work for `review`. The proof policy is enforced by `[phases.development.artifact_proof_policy]` in `ralph/policy/defaults/pipeline.toml`. The watchdog contract (`ralph/agents/idle_watchdog/` — `IdleWatchdog` + `PostExitWatchdog`, both via `Clock` injection for deterministic testing) owns in-stream and post-exit wall-clock ceilings; ad-hoc `time.sleep()` loops in `ralph/agents/invoke.py` are forbidden.
RALPH-FACT: failure_reporting_contract: failures are classified by `ralph/recovery/classifier.py` (single owner); technical retries share one cap (`general.max_same_agent_retries`), one formatter (`ralph/recovery/retry_prompt.py`), and one error-format contract (failure/error block first, prompt/context references secondary). Loopbacks (analysis / validation) are not technical retries and do not reuse the technical retry counter. The agent MUST surface the classified `FailureCategory` and the cap state in every reported failure, never collapse distinct recoverable categories into "agent error".
RALPH-FACT: documentation_update_obligation: every behaviour change updates affected markdown in the same workflow. The canonical fabric is `ralph-workflow/docs/sphinx/` (Sphinx operator manual) and `docs/ralph-workflow-policy/` (canonical policy). User-facing surfaces (`README.md`, `START_HERE.md`, `docs/README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`) route readers to those canonical homes; a behaviour change MUST not leave those surfaces pointing at stale prose. The fabrication guard governs every edit to public-facing markdown, not just claims files.
RALPH-FACT: policy_applicability_index: maps every required policy file to the agent surface that owns it. `testing-policy.md` -> Claude Code / OpenCode / Codex / Pi (development and fix phases). `typechecking-policy.md` and `linting-policy.md` -> any agent that edits Python. `dependency-policy.md` -> any agent that touches pyproject.toml / uv.lock. `verification-policy.md` -> the CI surface (Codeberg / Woodpecker is the surface that runs `make verify`; GitHub Actions runs the CLA check and PyPI publishing only); called by every agent before completion. `agent-policy.md` -> every AI agent; self-referential. `clean-code-policy.md` -> every AI agent on first-party code. `documentation-policy.md` -> any agent that writes user-facing markdown. `security-policy.md` -> any agent that touches subprocess, filesystem, network, or credential code under ralph-workflow/ralph/. `architecture-policy.md` -> any agent that crosses component boundaries. The index is the single source of truth for "which policy applies to this change" and is re-evaluated on every new component added under ralph-workflow/ralph/.

## AI execution instructions

To follow this policy, an agent making any change MUST:

* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) and affected
  documentation in the same workflow that changes the supported agents,
  dispatch command, or review process.

An agent MUST NOT:

* Claim a passing gate that was not actually run.
* Weaken a check to obtain a passing result.
* Fabricate capabilities, dependency characteristics, or adoption
  claims.
* Bypass the fabrication guard with `--no-verify`; doing so is
  itself fabrication (per AGENTS.md § Non-negotiables).

## Verification

Run every gate below before claiming a change complies with this policy.

RALPH-COMMAND: make -C ralph-workflow verify

The expected successful result is exit 0 from the authoritative
verification gate. Report the actual command output. The verify gate
exercises ruff, mypy --strict, `make test` (60 s combined budget), the
17 non-test audits, and the social-proof gate; a green run is the
minimum evidence of compliance with this policy.

The dogfooded commit rule (commit generation) is also gated here:

RALPH-COMMAND: python -m ralph --generate-commit

This is the only permitted commit command per `AGENTS.md` § Commit
rule; hand-rolled `git commit` is forbidden.

## Exceptions

A specific agent (e.g. an automation bot) MAY be exempted from
certain obligations with a documented rationale, scope, owner, and
review date. The exemption lives in this section and MUST reference
the relevant Ralph Workflow pipeline phase and the operator contract
the bot obeys.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new agent is added or an existing agent is removed.
* The agent dispatch command changes.
* The agent review process changes.

## Research basis

* publisher: Google Engineering Practices
  title: "Code Review: How to Review Code"
  http: https://google.github.io/eng-practices/review/reviewer/
  review date: 2026-07-12

* publisher: Anthropic
  title: "Claude Code: Best Practices for Agent Workflows"
  http: https://docs.anthropic.com/en/docs/claude-code/best-practices
  review date: 2026-07-12

* publisher: OpenAI
  title: "Agent Design Patterns"
  http: https://platform.openai.com/docs/guides/agents
  review date: 2026-07-12

## Living document contract

This policy is a living document. It MUST evolve as the project grows:
update the resolved facts, commands, and requirements whenever verified
project reality changes (new frameworks, new commands, new structure).
Two guardrails bound every amendment:

* Conflicts between this policy's generic defaults and the project's
  established practice are resolved in
  favor of the existing project policy — adapt this file to verified
  project reality, never the reverse. A looser project practice is
  NOT such a conflict: keep the stronger requirement unless a
  documented exception narrows it.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: agent-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
