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


def test_consumed_anchors_demote_to_warning_with_cost_named() -> None:
    """Pipeline-consumed anchors demote to warnings with cost-named messages.

    Content-shape findings (malformed step IDs, work-unit dependency
    cycles, malformed work-unit markers, shell invocations on verify
    items) are advisory under the plan-scoped severity policy. They
    state the run cost and the fix instead of blocking.
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

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    errors = [d for d in diagnostics if d.severity == "error"]
    warnings = [d for d in diagnostics if d.severity == "warning"]

    # Anchor findings are warnings; canonical content still maps despite
    # the malformed step ID / cycle / shell invocation / stray prose.
    assert errors == [], (
        f"advisory-by-default plan should produce zero errors, got: "
        f"{[(d.rule_id, d.severity, d.message) for d in errors]}"
    )
    assert content != {}, (
        "a plan that draws only advisory findings must still produce "
        "canonical content"
    )

    rule_ids = {d.rule_id for d in warnings}
    assert "PLAN022" in rule_ids  # malformed step ID (STEP-1)
    assert "REF004" in rule_ids   # dependency cycle in Work Units
    assert "PLAN024" in rule_ids  # malformed Work Units line
    assert "PLAN020" in rule_ids  # shell invocation guard on V-1

    # Every warning follows the advisory cost \u2192 resolve convention.
    for d in warnings:
        assert _ADVISORY_COST_RE.search(d.message), (
            f"advisory diagnostic does not follow the cost \u2192 resolve "
            f"convention: rule_id={d.rule_id} message={d.message!r}"
        )


def test_dangling_step_reference_advisories_with_cost_named() -> None:
    """A ``Depends on:`` value that does not match any step is advisory.

    Under the advisory-by-default policy, a dangling step reference
    surfaces as a cost-named warning; the canonical content still maps
    so downstream consumers (development_result proof, fan-out) see the
    plan they were handed.
    """
    document = """---
type: plan
---
## Steps

### [S-1] Step one
Depends on: S-99
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    errors = [d for d in diagnostics if d.severity == "error"]
    warnings = [d for d in diagnostics if d.severity == "warning"]

    assert errors == [], (
        f"dangling step reference is advisory; got errors: "
        f"{[(d.rule_id, d.message) for d in errors]}"
    )
    assert content != {}, "warnings-only plan must still map to canonical content"
    assert any(
        d.rule_id in {"PLAN021", "REF003"} and d.severity == "warning" for d in warnings
    ), f"expected a dangling-reference warning, got: {[(d.rule_id, d.severity, d.message) for d in warnings]}"
    for d in warnings:
        assert _ADVISORY_COST_RE.search(d.message), (
            f"advisory diagnostic does not follow cost/resolve convention: "
            f"rule_id={d.rule_id} message={d.message!r}"
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
    """An override targeting a routing error emits PLAN026 and the error still blocks.

    Under the plan-scoped severity policy, content-shape findings
    (PLAN022 malformed step ID, PLAN021 dangling reference, etc.) are
    demoted to warning. The only rule_ids that stay blocking for plans
    are the four routing/transport classes: PLAN001 not-a-plan,
    SPEC002 missing type, MD005/MD006/MD007 unparseable frontmatter,
    SPEC001 size transport, and PLAN023 noop grammar. We exercise the
    override-on-error contract with SPEC002, the missing-type routing
    check, so the test verifies the new contract rather than relying
    on a rule that has moved to advisory.
    """
    document = """---
title: just a title
---
## Steps

### [S-1] First step
This is a step block with content. Adding more text to make it over 100
characters of actual content so the not-a-plan detector does not fire on
length grounds and we can exercise the override-on-error contract.

## Validation Overrides
- [SPEC002] Trying to silence an error
"""

    content, diagnostics, overridden = analyze_plan_document(document)
    spec002_errors = [d for d in diagnostics if d.rule_id == "SPEC002"]
    plan026_warnings = [d for d in diagnostics if d.rule_id == "PLAN026"]

    assert len(spec002_errors) == 1
    assert spec002_errors[0].severity == "error"
    assert len(plan026_warnings) == 1
    assert plan026_warnings[0].severity == "warning"
    assert content == {}  # the routing error still blocks content mapping
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


# ---------------------------------------------------------------------------
# (e) Convention-corpus battery — every reachable diagnostic follows the
# message contract so the agent can read what, the consumer / cost, and the
# fix in linear order. These are the residue gaps closed by the consumer-
# naming iteration; if a future edit rewrites any of these messages back
# to a non-convention form, the test fails closed.
# ---------------------------------------------------------------------------


def test_spec002_missing_type_frontmatter_names_consumer() -> None:
    """A plan with no ``type`` frontmatter surfaces SPEC002 with consumer phrase."""
    document = """---
schema_version: 1
---
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    assert content == {}
    spec002_errors = [d for d in diagnostics if d.rule_id == "SPEC002"]
    assert spec002_errors, "expected SPEC002 for missing type frontmatter"
    for d in spec002_errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"SPEC002 missing required frontmatter does not name its consumer: "
            f"{d.message!r}"
        )


def test_md006_duplicate_type_frontmatter_names_consumer() -> None:
    """A plan with duplicate ``type`` frontmatter lines surfaces MD006 with consumer phrase."""
    document = """---
type: plan
type: plan
---
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    assert content == {}
    md006_errors = [
        d for d in diagnostics if d.rule_id == "MD006" and d.severity == "error"
    ]
    assert md006_errors, "expected MD006 for duplicate type frontmatter"
    for d in md006_errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"MD006 duplicate frontmatter does not name its consumer: {d.message!r}"
        )


def test_spec010_pydantic_failure_advisories_with_cost_named() -> None:
    """A pydantic-schema rejection surfaces SPEC010 as advisory.

    The pydantic branch of SPEC010 is content-shape (the canonical
    normalizer rejected a field the markdown side accepted), so the
    plan-scoped severity policy demotes it from error to warning. The
    best-effort canonical content is persisted so downstream consumers
    still see the plan the agent authored.
    """
    document = """---
type: plan
---
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py
Satisfies: AC-99

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    assert content != {}, "warnings-only plan must still map to canonical content"
    spec010_warnings = [d for d in diagnostics if d.rule_id == "SPEC010" and d.severity == "warning"]
    assert spec010_warnings, "expected SPEC010 warning for pydantic schema rejection"
    for d in spec010_warnings:
        assert _ADVISORY_COST_RE.search(d.message), (
            f"SPEC010 pydantic failure does not follow the cost \u2192 resolve "
            f"convention: {d.message!r}"
        )


def test_plan009_unknown_field_label_names_cost() -> None:
    """An unknown field label surfaces PLAN009 with the cost/fix convention."""
    document = """---
type: plan
---
## Steps

### [S-1] Step
Type: file_change
Filse:
- modify foo.py

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0
"""

    _content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    plan009_warnings = [
        d for d in diagnostics if d.rule_id == "PLAN009" and d.severity == "warning"
    ]
    assert plan009_warnings, "expected PLAN009 warning for unknown field label"
    for d in plan009_warnings:
        assert _ADVISORY_COST_RE.search(d.message), (
            f"PLAN009 unknown label warning does not follow cost/fix convention: "
            f"{d.message!r}"
        )


def test_plan020_prose_drop_advisory_names_cost() -> None:
    """A known field with a missing value surfaces PLAN020 with the cost/fix convention."""
    document = """---
type: plan
---
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py
Verify:
Expect: pytest will pass

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0
"""

    _content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    plan020_warnings = [
        d for d in diagnostics if d.rule_id == "PLAN020" and d.severity == "warning"
    ]
    assert plan020_warnings, "expected PLAN020 warning for missing-value prose drop"
    # The warning is the prose-drop fallback (the field falls back to prose
    # because the recognized label lacks a value); pin that the diagnostic
    # follows the advisory cost/fix convention.
    prose_drop = [
        d
        for d in plan020_warnings
        if "requires a value" in d.message or "drops to prose" in d.message
    ]
    assert prose_drop, (
        "expected at least one PLAN020 prose-drop warning; got "
        f"{[d.message for d in plan020_warnings]}"
    )
    for d in prose_drop:
        assert _ADVISORY_COST_RE.search(d.message), (
            f"PLAN020 prose-drop warning does not follow cost/fix convention: "
            f"{d.message!r}"
        )


# ---------------------------------------------------------------------------
# (f) Parser-originated errors: MD002 / MD005 / MD006 / MD007 are produced
# by the shared markdown parser; every one of them must follow the same
# ``what; blocking because <consumer>; resolve by <fix>`` convention the rest
# of the blocking surface uses so the agent can read what the error is,
# who cannot proceed past it, and how to fix it in linear order. A parser
# error reaching the plan validator must also fail ``content`` to ``{}`` so
# the tool handler's ``is_error`` check fires — the consumer (spec-registry
# routing) is upstream of the plan validator and an unparseable document
# cannot be routed at all.
# ---------------------------------------------------------------------------


def test_md002_top_level_prose_advisories_with_cost_named() -> None:
    """A body line before the first ``## Heading`` is advisory.

    MD002 is content-shape (the parser found a body line outside any
    section), so the plan-scoped severity policy demotes it from error
    to warning. The message still names the run cost so the agent can
    decide whether to repair it.
    """
    document = """---
type: plan
---
Some prose before any heading.
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    assert content != {}, "warnings-only plan must still map to canonical content"
    md002_warnings = [d for d in diagnostics if d.rule_id == "MD002" and d.severity == "warning"]
    assert md002_warnings, "expected MD002 warning for top-level prose"
    for d in md002_warnings:
        assert _ADVISORY_COST_RE.search(d.message), (
            f"MD002 top-level prose does not follow the cost \u2192 resolve convention: "
            f"{d.message!r}"
        )


def test_md005_malformed_frontmatter_field_names_consumer() -> None:
    """A frontmatter line that does not match ``key: value`` follows the convention.

    The parser is the one that reports MD005; the message must still
    state the routing consumer and the grammar fix the agent should make.
    """
    document = """---
type: plan
not a field
---
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    assert content == {}
    md005_errors = [d for d in diagnostics if d.rule_id == "MD005" and d.severity == "error"]
    assert md005_errors, "expected MD005 error for malformed frontmatter"
    for d in md005_errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"MD005 malformed frontmatter does not name its consumer: {d.message!r}"
        )


def test_md007_unterminated_frontmatter_block_names_consumer() -> None:
    """A frontmatter block that does not close with ``---`` follows the convention.

    The parser reports MD007 when the document ends inside the
    frontmatter; the message still carries the routing consumer and the
    closing-line fix.
    """
    document = """---
type: plan
this never closes
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    assert content == {}
    md007_errors = [d for d in diagnostics if d.rule_id == "MD007" and d.severity == "error"]
    assert md007_errors, "expected MD007 error for unterminated frontmatter"
    for d in md007_errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"MD007 unterminated frontmatter does not name its consumer: {d.message!r}"
        )


def test_parser_errors_block_content_mapping() -> None:
    """Parser-originated errors must map to an empty content payload.

    A document with a parser-level error cannot be routed to the plan
    validator because the grammar is broken upstream; the canonical
    content must be ``{}`` so the tool handler's ``valid=False`` /
    ``is_error=True`` checks fire. This is the same shape
    :func:`parse_and_validate` already guarantees for parser errors.
    """
    document = """---
type: plan
type: plan
not a field
---
Some prose before any heading.
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py
"""

    content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    error_severity = {d.rule_id for d in diagnostics if d.severity == "error"}
    assert error_severity >= {"MD005", "MD006"}
    assert content == {}


# ---------------------------------------------------------------------------
# (g) PLAN006 coercion: unknown evidence kind is coerced to 'file' with a
# warning. The other warning rules follow the what/cost/fix convention; this
# pin catches PLAN006 if it ever regresses to a flat 'unknown evidence kind'
# message without the cost-named rationale the run pays when the executor
# reads the evidence as a file the planner did not mean.
# ---------------------------------------------------------------------------


def test_plan006_unknown_evidence_kind_follows_advisory_convention() -> None:
    """PLAN006 surfaces as a warning that names the run cost and the fix.

    A bullet of the form ``- prefix: value`` that uses an evidence kind
    outside the canonical {file, command_output, test_name} set is coerced
    to ``file`` with PLAN006. The diagnostic must follow the advisory
    ``what; the run cost is <cost>; resolve by <fix>`` convention so the
    agent can read why the coercion matters and what to do about it.
    """
    document = """---
type: plan
---
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py
Evidence:
- link: https://example.com/spec

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0
"""

    _content, diagnostics = parse_and_validate(document, PLAN_SPEC)
    plan006_warnings = [
        d for d in diagnostics if d.rule_id == "PLAN006" and d.severity == "warning"
    ]
    assert plan006_warnings, "expected PLAN006 warning for unknown evidence kind"
    for d in plan006_warnings:
        assert _ADVISORY_COST_RE.search(d.message), (
            "PLAN006 unknown evidence kind does not follow the cost/fix convention: "
            f"{d.message!r}"
        )

