<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: agent-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Agent Policy

## Purpose and scope

This policy governs every AI agent working in this project. It applies
to every change made through Claude Code, OpenCode, or any other agent
shell. It defines the project instruction surface, the gate obligations,
the truthfulness obligations, and the documentation update obligations.

## Default requirements

* Every AI agent MUST read `AGENTS.md`, `agent-policy.md`, and
  `verification-policy.md`, then the canonical policies mapped to the files
  and surfaces it will touch. Policy remediation MUST inventory the full set,
  then reconcile one policy at a time with the shared remediation contract.
* Every AI agent MUST follow the policies applicable to the files it
  touches (testing policy for tests, type-checking policy for typed
  code, etc.).
* Every AI agent MUST run the authoritative verification gate before
  claiming completion. The agent MUST report the actual command and
  outcome; "I think it works" is not evidence.
* Every AI agent MUST report failures accurately. Weakening a check
  to obtain a passing result is forbidden.
* Every AI agent MUST update a policy when a resolved fact, command,
  applicability decision, or stated requirement becomes inaccurate. User
  documentation MUST change when its described behavior changes.
* Every AI agent MUST avoid unsupported claims about tools, commands,
  or dependency quality.
* Every AI agent MUST preserve the canonical policy directory as the
  single source of truth for project quality policy.
* Dead code is prohibited. Agents MUST remove unused, unreachable, obsolete,
  superseded, commented-out, and speculative code in the same change that
  makes it unnecessary. Version control preserves history; rewriting later is
  preferable to retaining dead code.
* Policy ownership MUST remain distinct: architecture identifies boundaries;
  API compatibility owns consumer contracts; accessibility owns functional
  conformance; design system owns reusable visual primitives; UX owns flows;
  dependency owns third-party supply chain; release owns artifacts and
  promotion; documentation owns content accuracy; verification composes gates.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If a fact cannot be resolved yet (project too young, tool not chosen, value
not knowable), defer it with the RALPH-PENDING form "RALPH-PENDING (assumed
<date>); review trigger: <trigger>" — it reaches readiness and a dev-cycle
agent resolves it when its trigger fires. Then
delete this comment. -->

RALPH-FACT: supported_agents: PROJECT-FACT-UNRESOLVED
RALPH-FACT: agent_dispatch_command: PROJECT-FACT-UNRESOLVED
RALPH-FACT: agent_review_process: PROJECT-FACT-UNRESOLVED
RALPH-FACT: failure_reporting_contract: PROJECT-FACT-UNRESOLVED
RALPH-FACT: documentation_update_obligation: PROJECT-FACT-UNRESOLVED
RALPH-FACT: policy_applicability_index: PROJECT-FACT-UNRESOLVED

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

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; a gate that applies but is not wired yet (for example the tool is
not installed on a new project) is recorded as a RALPH-PENDING deferral —
`RALPH-PENDING: <approved-tool> (assumed <date>); review trigger: <trigger>`
— which reaches readiness and is resolved by a later dev cycle when its
trigger fires; only a gate that truly cannot EVER exist is recorded as
inapplicable with a reason and the condition that would create it. Then
delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 from the authoritative
verification gate. Report the actual command output.

## Exceptions

Truthful reporting, non-fabrication, applicable verification, and the ban on
weakening checks are never exemptible. A specific automation may omit only a
named gate that is technically irrelevant to its bounded scope, with rationale,
owner, and review date.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new agent is added or an existing agent is removed.
* The agent dispatch command changes.
* The agent review process changes.

## Research basis

* publisher: Google Engineering Practices
  title: "Code Review: How to Review Code"
  http: https://google.github.io/eng-practices/review/reviewer/
  review date: 2026-07-11

* publisher: Anthropic
  title: "Claude Code: Best Practices for Agent Workflows"
  http: https://docs.anthropic.com/en/docs/claude-code/best-practices
  review date: 2026-07-11

* publisher: OpenAI
  title: "Agent Design Patterns"
  http: https://platform.openai.com/docs/guides/agents
  review date: 2026-07-11

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
* Schema version: `<!-- ralph-policy-schema: v2 -->`
