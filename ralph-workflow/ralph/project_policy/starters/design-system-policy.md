<!-- ralph-policy-schema: v1 -->
<!-- ralph-policy-id: design-system-policy.md -->

# Design-System Policy

> REMOVE this file when the project has no GUI, visual component
> library, theme, or other meaningful visual interface. The validator
> detects the domain via deterministic signals (UI framework names in
> `stack.frameworks` OR CSS-family languages in
> `stack.secondary_languages`) and requires this file only when the
> domain is present.

## Purpose and scope

This policy governs the project's design system: the component
library, theme, tokens, styling architecture, and the rules for
introducing new visual primitives. It applies to every change that
touches the GUI, the component library, the theme, or the styling
surface.

## Default requirements

* The agent MUST reuse the project's existing components, variants,
  tokens, utilities, and theming APIs before introducing any new visual
  primitive. A new primitive MUST be added to the design system, not
  copied across screens.
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

* RALPH-FACT: design_system_name: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: component_library_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: theme_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: tokens_path: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: styling_architecture: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: raw_css_policy: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: accessibility_standard: PROJECT-FACT-UNRESOLVED
* RALPH-FACT: visual_regression_command: PROJECT-FACT-UNRESOLVED

## AI execution instructions

The agent MUST:

* INSPECT the existing design system, component library, theme, and
  tokens before any visual change.
* PRESERVE stricter existing design-system rules; adapt rather than
  weaken.
* REPLACE every starter placeholder with a verified value.
* PREFER existing components, variants, tokens, utilities, and theming
  APIs over new visual primitives.
* ADD new reusable patterns to the design system, not across screens.
* RUN every declared `RALPH-COMMAND:` and report the outcome.

The agent MUST NOT:

* Introduce a new framework, replace a functioning theme, or invent
  design tokens merely to fill this template.
* Introduce raw CSS where the project's theming or styling system can
  express the requirement.

## Verification

* RALPH-COMMAND: PROJECT-FACT-UNRESOLVED

The expected successful result is a clean visual-regression audit (or
its project equivalent). On failure, the agent MUST report the affected
component and the regression.

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

* Conflicts between starter boilerplate and the project's established
  practice are resolved in favor of the existing project policy — adapt
  this file to the project, never the reverse.
* An amendment MUST NOT subvert the INTENT of this policy. Weakening,
  disabling, or deleting a requirement so that a failing change passes is
  forbidden; evolution clarifies and extends, it does not water down.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: design-system-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v1 -->`
* Completion marker: the `ralph-policy-complete` completion comment (added ONLY when
  every requirement above is satisfied and every placeholder is
  resolved).