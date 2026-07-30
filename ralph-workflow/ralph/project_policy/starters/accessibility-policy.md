<!-- ralph-policy-schema: v2 -->
<!-- ralph-policy-id: accessibility-policy.md -->
<!-- RALPH-STARTER-TEMPLATE: starter template; replace verified facts and
commands, then delete this banner. -->

# Accessibility Policy

## Purpose and scope

This policy governs functional accessibility for user-facing interfaces.

## Applicability

Required for project-owned web, graphical, or interactive UI. Record platform,
conformance scope, and exclusions; uncertainty requires owner confirmation. If
the surface disappears, retain the file with a dated inactive decision and
reactivation trigger, or remove it through reviewed policy cleanup.

## Default requirements

* Semantic structure, names, roles, states, focus order, keyboard operation,
  contrast, zoom, motion preferences, and error identification MUST be
  addressed where applicable.
* Applicable text alternatives, captions, reflow, target size, status messages,
  timing, flashing, language, and accessible authentication criteria MUST be
  included in the declared conformance scope.
* Automated checks MUST complement, not replace, keyboard and
  assistive-technology review of material flows.
* New UI MUST meet the project's declared accessibility standard and MUST NOT
  introduce regressions.

## Project facts to resolve

The lines below are the verified project facts that agents rely on and keep
current.

<!-- REPLACE-ME: record the standard, supported input modes, review process,
and real gate, then delete this comment. -->

RALPH-FACT: accessibility_standard: PROJECT-FACT-UNRESOLVED
RALPH-FACT: conformance_level_and_platform_scope: PROJECT-FACT-UNRESOLVED
RALPH-FACT: keyboard_and_focus_pattern: PROJECT-FACT-UNRESOLVED
RALPH-FACT: assistive_technology_review: PROJECT-FACT-UNRESOLVED
RALPH-FACT: material_flow_and_platform_at_matrix: PROJECT-FACT-UNRESOLVED
RALPH-FACT: applicability_decision: PROJECT-FACT-UNRESOLVED

## AI execution instructions

Agents MUST reuse accessible components and select material flows by user
criticality, interaction novelty, and regression risk. Test representative
supported platform/input/assistive-technology combinations when the platform
supports them; otherwise inspect platform accessibility APIs and document the
limitation. Agents MUST run declared commands and reviews, identify the tested
platform and flow, and report manual capability that was not available. Agents
MUST NOT claim assistive-technology evidence that was not performed.

## Verification

<!-- REPLACE-ME: declare the real accessibility gate, then delete this comment. -->

RALPH-COMMAND: PROJECT-FACT-UNRESOLVED
RALPH-REVIEW: exercise affected keyboard and assistive-technology flows; evidence: dated accessibility review including not-performed blockers; owner: accessibility owner

## Exceptions

Exceptions require affected users, impact, mitigation, owner, and review date.

## Maintenance triggers

Review when the UI framework, component library, accessibility standard, or
primary interaction pattern changes.

## Research basis

* publisher: World Wide Web Consortium
  title: "Web Content Accessibility Guidelines (WCAG) 2.2"
  http: https://www.w3.org/TR/WCAG22/
  review date: 2026-07-12

* publisher: World Wide Web Consortium
  title: "WCAG2ICT 2.2"
  http: https://www.w3.org/TR/wcag2ict-22/
  review date: 2026-07-12

## Living document contract

This is a living document. Verified project facts determine implementation
details; mandatory outcomes remain unless narrowed by a scoped, owner-approved,
expiring exception. Stronger legal, contractual, security, or safety
obligations win.

## Ralph markers

* Policy id: `<!-- ralph-policy-id: accessibility-policy.md -->`
* Schema version: `<!-- ralph-policy-schema: v2 -->`
