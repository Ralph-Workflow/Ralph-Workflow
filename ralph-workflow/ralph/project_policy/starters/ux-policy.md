<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: ux-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, deletes this banner, and adds the completion
marker. Readiness stays blocked while this banner or any placeholder token
remains. -->

# UX Policy

This policy applies while the project ships substantial UI (an app
framework or client-side routing). UX obligations imply the
design-system policy; a single incidental control or a minimal
admin page is out of scope. If the project permanently stops
shipping substantial UI, remove this policy file in the same
workflow.

## Purpose and scope

This policy governs user experience: target users, primary tasks,
established interaction principles, navigation and information
architecture, expected states (loading, empty, error, validation,
success, disabled, permission), form behaviour, focus and keyboard
interaction, responsive behaviour, destructive-action consistency, and
UX review evidence. It applies to every change that affects user flows,
interaction behaviour, or usability.

## Default requirements

* The agent MUST identify target users, primary tasks, and established
  interaction principles before any UX change.
* Navigation and information-architecture conventions MUST be
  consistent across the application.
* Loading, empty, error, validation, success, disabled, and permission
  states MUST follow the established pattern.
* Forms MUST follow the established pattern: labels, validation
  feedback, focus management, keyboard interaction, and accessibility.
* Destructive operations MUST require explicit confirmation and MUST
  follow the established destructive-action pattern.
* Terminology, actions, and confirmations MUST be consistent across the
  application.
* UX changes MUST be evaluated against existing patterns and user
  needs. UX review evidence MUST be proportionate to the change.

## Project facts to resolve

The `RALPH-FACT:` lines below record verified project facts. Agents rely
on them when enforcing this policy and MUST keep them current as the
project evolves.

<!-- REPLACE-ME: record one verified, machine-checkable value per fact
below (commands, paths, names, versions — not adjectives or aspirations).
If the project is too young for a fact to be settled, record the best
current answer plus the condition that will settle it, e.g.
"none yet (assumed <date>; revisit when <trigger>)" — a future agent must
be able to tell a settled fact from a provisional one at a glance. Then
delete this comment. -->

RALPH-FACT: target_users: PROJECT-FACT-UNRESOLVED
RALPH-FACT: primary_tasks: PROJECT-FACT-UNRESOLVED
RALPH-FACT: interaction_principles: PROJECT-FACT-UNRESOLVED
RALPH-FACT: navigation_pattern: PROJECT-FACT-UNRESOLVED
RALPH-FACT: state_pattern_reference: PROJECT-FACT-UNRESOLVED
RALPH-FACT: form_pattern_reference: PROJECT-FACT-UNRESOLVED
RALPH-FACT: destructive_action_pattern: PROJECT-FACT-UNRESOLVED
RALPH-FACT: ux_review_process: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* PREFER existing UX patterns over new patterns.
* RECORD the UX review evidence appropriate to the change.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the navigation, form, state, or
  destructive-action pattern.

An agent MUST NOT:

* Introduce inconsistent terminology or destructive-action patterns.
* Skip the empty / error / loading state implementation.

## Verification

Run every gate below before claiming a change complies with this policy.

<!-- REPLACE-ME: set the project's real gate command. The first token must
be an approved gate tool (wrap anything else in `make`, `uv run`, or
`npx`). If the project has no such gate yet, create the smallest real one
(a make target running the actual check) rather than declaring a hollow
command; only a gate that truly cannot exist may be recorded as
inapplicable with a reason and the condition that would create it. Then
delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 from the UX review / audit.
On failure, report the affected screen and the gap.

## Exceptions

A documented UX exception (e.g. a wizard flow that diverges from the
default) requires a documented rationale, scope, owner, and review
date.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new screen, flow, or navigation pattern is added.
* The form or state pattern changes.
* The destructive-action pattern changes.

## Research basis

* publisher: Nielsen Norman Group
  title: "10 Usability Heuristics for User Interface Design"
  http: https://www.nngroup.com/articles/ten-usability-heuristics/
  review date: 2026-07-11

* publisher: W3C
  title: "Web Content Accessibility Guidelines (WCAG) 2.2"
  http: https://www.w3.org/TR/WCAG22/
  review date: 2026-07-11

* publisher: Don Norman
  title: "The Design of Everyday Things"
  http: https://www.basicbooks.com/titles/don-norman/the-design-of-everyday-things/9780465050659/
  review date: 2026-07-11

* publisher: Nielsen Norman Group (NN/g)
  title: "Usability 101: Introduction to Usability"
  http: https://www.nngroup.com/articles/usability-101-introduction-to-usability/
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

* Policy id: `<!-- ralph-policy-id: ux-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` comment; its presence
  certifies this file passed validation when it was last amended.