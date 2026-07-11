<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: ux-policy.md -->

# UX Policy

> REMOVE this file when the project does not have substantial UI (the
> validator detects the domain via stricter signals: app framework names
> in `stack.frameworks` OR router-dep substrings in
> `package.json` / `pyproject.toml`). Design-system alone does NOT
> trigger UX; UX implies design-system. A single incidental control or
> a minimal admin page does NOT require this file.

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

* RALPH-FACT: target_users: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: primary_tasks: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: interaction_principles: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: navigation_pattern: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: state_pattern_reference: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: form_pattern_reference: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: destructive_action_pattern: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: ux_review_process: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT the existing UX patterns, navigation, and state conventions
  before any UX change.
* PRESERVE stricter existing UX rules; adapt rather than weaken.
* REPLACE every starter placeholder with a verified value.
* PREFER existing UX patterns over new patterns.
* RECORD the UX review evidence appropriate to the change.

The agent MUST NOT:

* Introduce inconsistent terminology or destructive-action patterns.
* Skip the empty / error / loading state implementation.

## Verification

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is exit 0 from the UX review / audit.
On failure, the agent MUST report the affected screen and the gap.

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

* publisher: NN/g
  title: "User Experience Design"
  http: https://www.nngroup.com/ux-design/
  review date: 2026-07-11

## Ralph markers

* Policy id: `<!-- ralph-policy-id: ux-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: `the the project-policy-complete comment identifier comment` (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).