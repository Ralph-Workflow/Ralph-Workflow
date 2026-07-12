<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: agent-policy.md -->

# Agent Policy

## Purpose and scope

This policy governs every AI agent working in this project. It applies
to every change made through Claude Code, OpenCode, or any other agent
shell. It defines the project instruction surface, the gate obligations,
the truthfulness obligations, and the documentation update obligations.

## Default requirements

* Every AI agent MUST read `AGENTS.md` and the canonical policy files
  under `docs/ralph-workflow-policy/` before changing the project.
* Every AI agent MUST follow the policies applicable to the files it
  touches (testing policy for tests, type-checking policy for typed
  code, etc.).
* Every AI agent MUST run the authoritative verification gate before
  claiming completion. The agent MUST report the actual command and
  outcome; "I think it works" is not evidence.
* Every AI agent MUST report failures accurately. Weakening a check
  to obtain a passing result is forbidden.
* Every AI agent MUST update affected policies and documentation in
  the same workflow that changes the underlying behaviour.
* Every AI agent MUST avoid unsupported claims about tools, commands,
  or dependency quality.
* Every AI agent MUST preserve the canonical policy directory as the
  single source of truth for project quality policy.

## Project facts to resolve

* RALPH-FACT: supported_agents: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: agent_dispatch_command: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: agent_review_process: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: failure_reporting_contract: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: documentation_update_obligation: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT `AGENTS.md` and the canonical policy directory before any
  change. List the evidence used.
* PRESERVE stricter existing agent rules; adapt rather than weaken.
* REPLACE every starter placeholder with a verified value.
* RUN the authoritative verification gate and report the actual
  outcome.
* UPDATE affected policies and documentation in the same change.

The agent MUST NOT:

* Claim a passing gate that was not actually run.
* Weaken a check to obtain a passing result.
* Fabricate capabilities, dependency characteristics, or adoption
  claims.

## Verification

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 from the authoritative
verification gate. The agent MUST report the actual command output.

## Exceptions

A specific agent (e.g. an automation bot) MAY be exempted from
certain obligations with a documented rationale, scope, owner, and
review date.

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

* Conflicts between starter boilerplate and the project's established
  practice are resolved in favor of the existing project policy — adapt
  this file to the project, never the reverse.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: agent-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` completion comment (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).