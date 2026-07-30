<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: design-system-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: this file is a starter template, not yet this
project's policy. A remediation agent rewrites it with verified project
facts (every RALPH-FACT and RALPH-COMMAND below), adapts the defaults to the
project's established practice, and deletes this banner. Readiness stays
blocked while this banner or any placeholder token remains. -->

# Design-System Policy

This policy applies while the project ships a visual interface
(UI framework, component library, theme, or CSS-family styling).
If the project permanently stops shipping a visual interface,
remove this policy file in the same workflow.

## Purpose and scope

This policy governs the project's design system: the component
library, theme, tokens, styling architecture, and the rules for
introducing new visual primitives. It applies to every change that
touches the GUI, the component library, the theme, or the styling
surface.

## Default requirements

* The agent MUST reuse the project's existing components, variants,
  tokens, utilities, and theming APIs before introducing any new visual
  primitive. A reusable or system-level primitive MUST be added to the
  design system; a domain-local component MAY remain local when its scope
  does not justify promotion.
* The agent MUST prefer the project's established theming or styling
  system over raw CSS when the system can express the requirement.
* When raw CSS is permitted, it MUST consume existing tokens or
  variables (color, spacing, typography) rather than introduce
  unexplained values.
* Typography, color, spacing, layout, responsive behaviour, states,
  iconography, and motion MUST follow the project's design system
  conventions.
* Accessibility and contrast requirements applicable to visual changes
  MUST be verified before merge.

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

RALPH-FACT: design_system_name: PROJECT-FACT-UNRESOLVED
RALPH-FACT: component_library_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: theme_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: tokens_path: PROJECT-FACT-UNRESOLVED
RALPH-FACT: styling_architecture: PROJECT-FACT-UNRESOLVED
RALPH-FACT: raw_css_policy: PROJECT-FACT-UNRESOLVED
RALPH-FACT: accessibility_standard: PROJECT-FACT-UNRESOLVED
RALPH-FACT: visual_regression_command: PROJECT-FACT-UNRESOLVED

## AI execution instructions

To follow this policy, an agent making any change MUST:

* PREFER existing components, variants, tokens, utilities, and theming
  APIs over new visual primitives.
* ADD new reusable patterns to the design system, not across screens.
* RUN every `RALPH-COMMAND:` gate declared under Verification before
  claiming the change complies, and report the actual outcome. Never
  report a command that was not run.
* UPDATE this policy (facts, commands, requirements) in the same
  workflow that changes the design system, theme, tokens, or styling
  architecture.

An agent MUST NOT:

* Introduce a new framework, replace a functioning theme, or invent
  design tokens the project does not need.
* Introduce raw CSS where the project's theming or styling system can
  express the requirement.

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
RALPH-REVIEW: proportionately review material visual changes across affected states, breakpoints, themes, semantics, and contrast; evidence: dated visual review, with automation as supporting evidence; owner: design-system owner

The expected successful result is a clean visual-regression audit (or
its project equivalent). On failure, report the affected component and
the regression.

## Exceptions

A new visual primitive introduced outside the design system requires a
documented rationale, scope, owner, and review date.

## Maintenance triggers

This policy MUST be reviewed in the same workflow as any of:

* A new component or variant is added to the design system.
* A new token or theme is introduced.
* The raw CSS policy changes.
* The accessibility standard changes.

## Research basis

* publisher: Nielsen Norman Group
  title: "10 Usability Heuristics for User Interface Design"
  http: https://www.nngroup.com/articles/ten-usability-heuristics/
  review date: 2026-07-11

* publisher: W3C
  title: "Web Content Accessibility Guidelines (WCAG) 2.2"
  http: https://www.w3.org/TR/WCAG22/
  review date: 2026-07-11

* publisher: Brad Frost
  title: "Atomic Design"
  http: https://bradfrost.com/blog/post/atomic-web-design/
  review date: 2026-07-11

* publisher: Google Material Design
  title: "Design Tokens Specification"
  http: https://m3.material.io/foundations/design-tokens/overview
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

* Policy id: `<!-- ralph-policy-id: design-system-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
