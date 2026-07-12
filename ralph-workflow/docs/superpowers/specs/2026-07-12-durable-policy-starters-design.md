# Durable policy starters — design

Date: 2026-07-12
Status: approved (verbal, in-session)

## Problem

Starter policy files mixed three kinds of content, and all of it survived
remediation into the project's permanent policy files:

1. Durable policy (purpose, requirements, gates, exceptions, maintenance).
2. Remediation-era scaffolding ("REPLACE every starter placeholder",
   "REFUSE to add the completion marker", "INSPECT the project first") —
   one-time instructions that are already duplicated in the remediation
   prompt and become dead weight after READY.
3. Validator-mechanics prose ("the validator will reject…").

The end state we want: every policy file at READY reads as detailed,
easy-to-follow instructions for an AI agent to FOLLOW and ENFORCE that
policy — nothing else. The starter must remain detailed enough to produce
a very good policy, and obviously a template meant to be overwritten.

## Decision

Content-level fix, no schema break:

* **Headings unchanged.** Renaming any REQUIRED_HEADINGS entry is a v1
  schema break that would invalidate every existing READY project.
* **Template banner.** Every starter opens with one
  `RALPH-STARTER-TEMPLATE` HTML-comment banner explaining it is a
  template a remediation agent must rewrite and delete. The token is
  added to `markers.PLACEHOLDER_TOKENS`, so the deterministic validator
  blocks readiness until the banner is deleted — template-ness is
  obvious AND its removal is machine-enforced.
* **Section rewrites (all 12 starters).**
  * "Project facts to resolve" intro reframed as a durable record:
    RALPH-FACT lines record verified facts agents rely on and must keep
    current. Fact lines themselves unchanged.
  * "AI execution instructions" keeps only how-to-follow/enforce
    bullets; remediation-time bullets deleted (they live in the
    remediation prompt).
  * "Verification" intro reframed: run every gate before claiming
    compliance. RALPH-COMMAND/LANG/INAPPLICABLE lines unchanged.
  * "Ralph markers" completion bullet reworded to a durable
    certification statement.
  * Purpose/scope, Default requirements, Exceptions, Maintenance
    triggers, Research basis, Living document contract unchanged.
* **Remediation prompt additions** (`remediation._render_prompt`): remove
  inapplicable conditional sections; preserve the stricter rule when the
  project's existing practice is stricter; delete the starter banner;
  finished files must read as durable policy with no fill-in
  instructions, starter references, or validator mechanics.
* **Guard tests** (`tests/project_policy/test_starter_enforcement_prose.py`):
  ban remediation-era phrases in starters; require the durable facts
  framing; require exactly one banner per starter; require the banner
  token in PLACEHOLDER_TOKENS; require the prompt to own the fill-in
  instructions.

## Corrections forced by end-to-end verification

* `PLACEHOLDER_TOKENS` are only checked inside RALPH-FACT / RALPH-COMMAND /
  RALPH-INAPPLICABLE values (deliberately, so prose like "no TODO
  comments" cannot false-positive), so the banner token got its own
  whole-file validator check (`_check_template_banner`,
  `markers.STARTER_TEMPLATE_TOKEN`) instead of a PLACEHOLDER_TOKENS entry.
* The validator only counts machine lines anchored at line start, but the
  starters shipped them as `* RALPH-…` bullets — an in-place fill could
  never satisfy the validator. All machine lines are now line-start, with
  a guard test.
* The citation parser treats every paragraph under "## Research basis"
  as a citation block, so the starters' intro sentence there could never
  validate. Removed, with the fill-in guard test covering it.
* `test_filled_in_starter_validates_clean` now proves the full contract
  for every starter: resolve placeholders in place + delete banner + add
  completion marker == zero per-file findings.
* The bundle grew a 13th starter (`security-policy.md`, core, with a
  required "Threat surfaces" heading) in a parallel work stream; it
  follows this spec's template shape and is covered by all guards.

## Compatibility

Existing READY projects keep validating: their policy files contain
neither the banner token nor the banned prose, and heading requirements
are unchanged. Freshly seeded starters now fail validation on the banner
token in addition to PROJECT-FACT-UNRESOLVED — same remediation flow,
one more deterministic reason the file cannot be marked complete while
still template-shaped.

## Out of scope

* Renaming headings (schema v2 territory).
* Post-READY automated stripping of agent-authored prose.
