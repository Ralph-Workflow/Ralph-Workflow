"""Three-level severity and ``## Validation Overrides`` ledger coverage.

These tests pin the advisory-by-default behavior described in
``.agent/PRODUCT_CRITERIA.md``: shape findings are warnings, only
pipeline-consumed anchors stay blocking, and the override ledger lets an
agent proceed past any advisory finding with a recorded reason. Tests
are pure in-memory parse/validate calls \u2014 no I/O, no subprocess, no sleep \u2014
so they fit inside the 60-second combined test budget.

Coverage areas:

(a) A good plan (the conventional medium plan) submits with zero errors AND
    zero warnings \u2014 the validator stays quiet on plans that already do the
    right thing.
(b) A shape-violating battery yields warnings, not errors, and still
    submits valid \u2014 PLAN010 (file_change without Files:), PLAN011 (verify
    without Verify:/Location:), and the PLAN020 concreteness heuristics.
(c) A consumed-anchor battery still errors, and every error message names
    its downstream consumer in the convention (what \u2192 consumer \u2192 resolve-by).
(d) Override flow: a recorded override of a warning moves that diagnostic
    into ``overridden`` with its reason; a stale override (no matching
    diagnostic) draws PLAN025 info; an override on an error draws PLAN026
    warning and the error still blocks.
"""

from __future__ import annotations

import re

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document

# Regex fragments for the message convention. Every blocking diagnostic must
# state what was observed, which downstream consumer it blocks, and how to
# resolve it. Advisory diagnostics must state what, the run cost, and how.
# The end-of-string may or may not include a trailing period; the convention
# is content-based, not punctuation-based. Periods are allowed inside the
# "resolve by" reason because examples like 'pytest tests/x.py -q' contain them.
_BLOCKING_CONSUMER_RE = re.compile(
    r"blocking because [^;]+(?:;[^;]+)*; resolve by [^;]+(?:;[^;]+)*$"
)
_ADVISORY_COST_RE = re.compile(
    r"the run cost is [^;]+(?:;[^;]+)*; resolve by [^;]+(?:;[^;]+)*$"
)


def _good_plan() -> str:
    """Conventional medium plan: real Files:, real Verify:, real Expect:.

    This is the regression baseline: zero diagnostics, zero overrides.
    """
    return """---
type: plan
schema_version: 1
intent_verb: add
---
## Summary
Migrate the plan artifact to a JSON-free markdown grammar.

Intent: Plan documents are authored as plain markdown.
Coverage: feature, test

## Scope
- [SC-1] Redesign the plan grammar
  Category: feature

## Steps

### [S-1] Implement the markdown plan spec
Rewrite the mapping so labeled fields replace embedded JSON.

Type: file_change
Priority: high
Files:
- modify ralph/mcp/artifacts/markdown/specs/plan.py
- create tests/mcp/test_md_plan_spec.py
Satisfies: AC-01

### [S-2] Verify the focused suites
Run the markdown artifact suites.

Type: verify
Depends on: S-1
Verify: pytest tests/mcp/test_md_plan_spec.py -q
Expect: the focused markdown-plan tests pass with exit code 0

## Acceptance Criteria
- [AC-01] The plan grammar contains no JSON anywhere
  Satisfied by: S-1
  Verify: pytest tests/mcp/test_md_plan_spec.py -q
  Expect: the focused markdown-plan tests pass with exit code 0

## Verification
- [V-1] pytest tests/mcp/test_md_plan_spec.py -q
  Expect: the focused markdown-plan tests pass with exit code 0
  Timeout: 60
"""


# ---------------------------------------------------------------------------
# (a) Good-plan battery
# ---------------------------------------------------------------------------


def test_good_plan_emits_no_errors_and_no_warnings() -> None:
    """A plan with real files / verify / expect / concrete evidence stays quiet."""
    content, diagnostics = parse_and_validate(_good_plan(), PLAN_SPEC)

    assert content != {}
    assert diagnostics == []


# ---------------------------------------------------------------------------
# (b) Shape-violating battery \u2014 advisory, not blocking
# ---------------------------------------------------------------------------


def test_shape_violations_are_advisory_not_blocking() -> None:
    """PLAN010 / PLAN011 / PLAN020 shape findings emit as warnings only."""
    document = """---
type: plan
schema_version: 1
intent_verb: add
---
## Steps

### [S-1] File step without Files
This step claims to be a file_change but lists no target.

Type: file_change

### [S-2] Verify step without Verify
This step claims to be a verify step but lists no command.

Type: verify

### [S-3] Vague verification
Type: verify
Verify: run the tests
Expect: it works

## Verification
- [V-1] run the tests
  Expect: it works
"""

    _content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    warnings = [d for d in diagnostics if d.severity == "warning"]
    _errors = [d for d in diagnostics if d.severity == "error"]

    # PLAN010 / PLAN011 demoted; PLAN020 concreteness findings are warnings.
    advisory_rule_ids = {d.rule_id for d in warnings}
    assert "PLAN010" in advisory_rule_ids
    assert "PLAN011" in advisory_rule_ids
    assert "PLAN020" in advisory_rule_ids

    # No error-level advisory finding; pydantic may still surface SPEC010
    # on the vague verify step, but the markdown-side findings are warnings.
    assert all(d.rule_id != "PLAN010" or d.severity != "error" for d in diagnostics)
    assert all(d.rule_id != "PLAN011" or d.severity != "error" for d in diagnostics)

    # Every warning uses the advisory cost \u2192 resolve convention.
    for d in warnings:
        assert _ADVISORY_COST_RE.search(d.message), (
            f"advisory diagnostic does not follow the cost \u2192 resolve convention: "
            f"{d.message!r}"
        )


# ---------------------------------------------------------------------------
# (c) Consumed-anchor battery \u2014 still blocking, message names consumer
# ---------------------------------------------------------------------------


def test_consumed_anchors_remain_blocking_with_consumer_named() -> None:
    """Pipeline-consumed anchors error with a message that names the reader.

    Every error diagnostic must follow the convention
    ``what; blocking because <consumer>; resolve by <fix>``.
    """
    document = """---
type: plan
---
## Steps

### [STEP-1] Malformed step ID
This block uses the wrong heading form.

Type: action

## Work Units
- [WU-A] First unit
  Directories:
  - foo
  Depends on: WU-B

- [WU-B] Second unit
  Directories:
  - bar
  Depends on: WU-A

free text line that is not a [unit-id] item

## Verification
- [V-1] bash -c 'pytest tests'
  Expect: tests pass
"""

    _content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    errors = [d for d in diagnostics if d.severity == "error"]

    rule_ids = {d.rule_id for d in errors}
    assert "PLAN022" in rule_ids  # malformed step ID (STEP-1)
    assert "REF004" in rule_ids   # dependency cycle in Work Units
    assert "PLAN024" in rule_ids  # malformed Work Units line
    assert "PLAN020" in rule_ids  # shell invocation guard on V-1

    # Every error message follows the blocking consumer convention.
    for d in errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"blocking diagnostic does not name its consumer: rule_id={d.rule_id} "
            f"message={d.message!r}"
        )


def test_dangling_step_reference_blocks_with_consumer_named() -> None:
    """A ``Depends on:`` value that does not match any step blocks submission."""
    document = """---
type: plan
---
## Steps

### [S-1] Step one
Depends on: S-99
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    errors = [d for d in diagnostics if d.severity == "error"]

    assert content == {}
    assert any(
        d.rule_id in {"PLAN021", "REF003"} and d.severity == "error" for d in errors
    ), f"expected a dangling-reference error, got: {[(d.rule_id, d.severity, d.message) for d in errors]}"
    for d in errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"blocking diagnostic does not name its consumer: rule_id={d.rule_id} "
            f"message={d.message!r}"
        )


def test_wrong_frontmatter_type_blocks_with_consumer_named() -> None:
    """An unknown ``type`` value blocks because the spec registry routes on it."""
    document = """---
type: nonsense
---
## Steps

### [S-1] Step
Body.
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    errors = [d for d in diagnostics if d.severity == "error"]

    assert content == {}
    type_errors = [d for d in errors if "type" in d.message.lower()]
    assert type_errors
    for d in type_errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"frontmatter 'type' error does not name its consumer: {d.message!r}"
        )


# ---------------------------------------------------------------------------
# (d) Override ledger
# ---------------------------------------------------------------------------


def test_override_suppresses_matching_advisory_with_recorded_reason() -> None:
    """A recorded override of a warning moves that diagnostic into ``overridden``.

    The override and its reason are returned to tool handlers; the warning
    is no longer in the diagnostic list.
    """
    document = """---
type: plan
---
## Steps

### [S-1] File step without Files
Type: file_change

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0

## Validation Overrides
- [PLAN010] This is a coordinating step, not a file_change
"""

    _content, diagnostics, overridden = analyze_plan_document(document)

    # The matching warning has been moved into ``overridden`` with its reason.
    plan010_in_diagnostics = [
        d for d in diagnostics if d.rule_id == "PLAN010" and d.severity == "warning"
    ]
    assert plan010_in_diagnostics == []

    plan010_overrides = [o for o in overridden if o.rule_id == "PLAN010"]
    assert len(plan010_overrides) == 1
    override = plan010_overrides[0]
    # The override has no section narrowing (no 'Where:'), so the matcher
    # attribute is None; the diagnostic itself still carries the Steps
    # section it was emitted in.
    assert override.section is None
    assert override.diagnostic.section == "Steps"
    assert "coordinating step" in override.reason
    assert override.diagnostic.severity == "warning"
    assert "S-1" in override.diagnostic.message


def test_stale_override_draws_plan025_info() -> None:
    """An override whose rule_id never fired surfaces as PLAN025 info."""
    document = """---
type: plan
---
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0

## Validation Overrides
- [PLAN999] This rule does not exist in the validator
"""

    _content, diagnostics, overridden = analyze_plan_document(document)
    stale = [d for d in diagnostics if d.rule_id == "PLAN025"]
    assert len(stale) == 1
    assert stale[0].severity == "info"
    assert overridden == []


def test_override_targeting_an_error_draws_plan026_warning_and_still_blocks() -> None:
    """An override targeting an error emits PLAN026 and the error still blocks."""
    document = """---
type: plan
---
## Steps

## Validation Overrides
- [PLAN022] Trying to silence an error
"""

    _content, diagnostics, overridden = analyze_plan_document(document)
    plan022_errors = [d for d in diagnostics if d.rule_id == "PLAN022"]
    plan026_warnings = [d for d in diagnostics if d.rule_id == "PLAN026"]

    assert len(plan022_errors) == 1
    assert plan022_errors[0].severity == "error"
    assert len(plan026_warnings) == 1
    assert plan026_warnings[0].severity == "warning"
    assert _content == {}  # the error still blocks content mapping
    assert overridden == []  # errors are never overridable


def test_override_with_section_label_narrows_match() -> None:
    """``Where: <section>`` only matches diagnostics in that section.

    An override entry matches every diagnostic with the same rule_id
    whose section (when narrowed) matches. Diagnostics outside the
    narrowed section remain as advisory findings.
    """
    document = """---
type: plan
---
## Steps

### [S-1] File step without Files
Type: file_change

### [S-2] Other file step without Files
Type: file_change

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0

## Validation Overrides
- [PLAN010] Where: Steps both file_change warnings were deliberate
"""

    _content, diagnostics, overridden = analyze_plan_document(document)
    plan010_in_diagnostics = [
        d for d in diagnostics if d.rule_id == "PLAN010" and d.severity == "warning"
    ]

    # Both PLAN010 diagnostics were in the Steps section and matched the
    # override; both are removed from the diagnostic list.
    assert plan010_in_diagnostics == []
    plan010_overrides = [o for o in overridden if o.rule_id == "PLAN010"]
    assert len(plan010_overrides) == 2
    assert all(o.section == "Steps" for o in plan010_overrides)


def test_override_section_label_filters_out_other_sections() -> None:
    """A ``Where:`` label matches only diagnostics in that named section."""
    document = """---
type: plan
---
## Steps

### [S-1] File step without Files
Type: file_change

## Verification
- [V-1] run the tests
  Expect: it works

## Validation Overrides
- [PLAN020] Where: Verification the verification item was deliberate
"""

    _content, diagnostics, _overridden = analyze_plan_document(document)

    # The Steps-section PLAN010 was NOT matched (the override narrowed to
    # Verification); it remains as advisory.
    plan010_in_diagnostics = [
        d for d in diagnostics if d.rule_id == "PLAN010" and d.severity == "warning"
    ]
    assert len(plan010_in_diagnostics) == 1
    # The Verification-section PLAN020 was matched and removed.
    plan020_in_diagnostics = [
        d for d in diagnostics if d.rule_id == "PLAN020" and d.severity == "warning"
    ]
    assert plan020_in_diagnostics == []


def test_malformed_override_entry_draws_plan025_error() -> None:
    """A malformed override entry is itself an error (fail closed)."""
    document = """---
type: plan
---
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0

## Validation Overrides
This is prose, not an override item.
"""

    _content, diagnostics, _overridden = analyze_plan_document(document)
    malformed = [d for d in diagnostics if d.rule_id == "PLAN025" and d.severity == "error"]
    assert len(malformed) == 1
    assert "list items" in malformed[0].message
