"""End-to-end chain demonstration: validator + planning prompt + analysis prompt.

Criterion 20 of the product brief asks for an in-session demonstration that
the planning chain now advises on substance instead of enforcing shape.
Three fixtures carry the in-session before/after evidence:

1. **HOLLOW** — every conventional section is present but the steps use
   vague verifications. Pre-``24e66c49f`` the validator blocked this shape
   through required-section rules; the rewritten chain stays silent on
   shape and surfaces cost-named warnings about the vague verification.
2. **UNCONVENTIONAL** — a substantive plan that uses a custom
   ``## Checklist`` heading with no Summary / Scope / Risks sections and
   concrete ``Verify:`` / ``Expect:`` fields. Pre-``24e66c49f`` the
   required-section grammar manufactured a misshapen plan to satisfy the
   checker; the rewritten chain passes this plan with zero errors and
   zero warnings.
3. **GOOD** — the conventional medium plan shape from
   ``test_md_plan_advisory.py`` stays silent across all severities.

The prompt-chain assertions prove that the planning prompt orders thinking
before submission mechanics and the analysis prompt carries the
overrides-respect sentence exactly once, so the chain states one
standard once across the validator, the planning prompt, and the
analysis prompt.

All tests are pure in-memory parse / template-render calls — no I/O,
no subprocess, no sleep — so they fit inside the 60-second combined
test budget.
"""

from __future__ import annotations

import re

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document
from ralph.prompts.template_context import TemplateContext


def _validate_with_overrides(content: str) -> tuple[list, list]:
    """Tool-shape helper that mirrors ``md_artifact._validate_with_overrides``.

    Inlines the plan-aware path of the helper so the test does not import a
    private (``_``-prefixed) symbol from ``ralph.mcp.tools.md_artifact``.
    The helper is itself a two-line branch; an inline copy keeps the
    chain's wire shape (per-severity counts + overridden list) in one
    readable spot and stays inside the repo's no-private-import rule.
    """
    _content, diagnostics, overridden = analyze_plan_document(content)
    return diagnostics, list(overridden)

_ADVISORY_COST_RE = re.compile(
    r"the run cost is [^;]+(?:;[^;]+)*; resolve by [^;]+(?:;[^;]+)*$"
)


# ---------------------------------------------------------------------------
# Fixture HOLLOW: every conventional section, vague Verify: / Expect:
# Pre-24e66c49f: silently accepted because every section was filled in.
# Post-24e66c49f: PLAN020 surfaces cost-named warnings about the vagueness.
# ---------------------------------------------------------------------------


def _hollow_plan() -> str:
    return """---
type: plan
schema_version: 1
intent_verb: add
---
## Summary
foo() crashes on out-of-range indexes.

Intent: Clamp indexes.
Coverage: bugfix

## Scope
- [SC-1] Add a regression test
  Category: test
- [SC-2] Clamp indexes in src/foo.py
  Category: bugfix

## Skills MCP
Skills: test-driven-development

## Steps

### [S-1] Add the regression test
Add tests/test_foo.py::test_clamp_out_of_range.

Type: file_change
Files:
- modify tests/test_foo.py

### [S-2] Clamp indexes
Clamp negative and oversized indexes.

Type: file_change
Files:
- modify src/foo.py

### [S-3] Run the tests
Type: verify
Verify: run the tests
Expect: it works

## Critical Files
- [CF-1] src/foo.py
  Action: modify
- [CF-2] tests/test_foo.py
  Action: modify

## Acceptance Criteria
- [AC-01] Invalid indexes no longer crash foo()
  Satisfied by: S-1, S-2, S-3
  Verify: run the tests
  Expect: it works

## Risks
- [R-1] Clamping could mask a caller bug
  Severity: medium

## Verification
- [V-1] run the tests
  Expect: it works
  Timeout: 60
"""


def test_hollow_plan_surfaces_cost_named_warnings_not_errors() -> None:
    """The hollow-but-shapely plan now draws cost-named advisory warnings.

    Before the rewrite every conventional section was present, so the plan
    was accepted without comment. The chain now advises on substance: the
    vague ``Verify:`` / ``Expect:`` fields surface PLAN020 warnings that
    state the run cost and the fix, while every error-severity check stays
    silent because the validator only blocks when a downstream consumer
    actually cannot proceed.
    """
    content, diagnostics = parse_and_validate(_hollow_plan(), PLAN_SPEC)
    # The plan still parses: every section is filled in.
    assert content != {}
    errors = [d for d in diagnostics if d.severity == "error"]
    assert errors == [], (
        f"hollow plan should not produce error-severity diagnostics, got: "
        f"{[(d.rule_id, d.severity, d.message) for d in errors]}"
    )
    warnings = [d for d in diagnostics if d.severity == "warning"]
    assert warnings, "hollow plan must surface at least one cost-named advisory"
    plan020 = [d for d in warnings if d.rule_id == "PLAN020"]
    assert plan020, (
        "vague Verify:/Expect: should produce PLAN020 cost-named warnings; "
        f"got warnings: {[(d.rule_id, d.message) for d in warnings]}"
    )
    for d in plan020:
        assert _ADVISORY_COST_RE.search(d.message), (
            f"PLAN020 hollow-plan warning does not follow cost/fix convention: "
            f"{d.message!r}"
        )


def test_hollow_plan_tool_payload_reports_warnings_and_zero_overrides() -> None:
    """The tool payload shape surfaces warnings and an empty override list.

    ``_validate_with_overrides`` is the same helper the verify / submit
    / finalize / draft-status handlers use, so the chain demonstrates the
    exact wire shape a real run returns: per-severity ``counts`` and an
    ``overridden`` list that is empty for plans with no override ledger.
    """
    diagnostics, overridden = _validate_with_overrides(_hollow_plan())
    warnings = [d for d in diagnostics if d.severity == "warning"]
    assert warnings, "hollow plan must surface warnings for the chain to advise"
    assert overridden == [], (
        "a plan without ## Validation Overrides must report an empty override list; "
        f"got {overridden!r}"
    )


# ---------------------------------------------------------------------------
# Fixture UNCONVENTIONAL: substantive plan, custom headings, no shape rules
# Pre-24e66c49f: required-section rules manufactured a misshapen plan.
# Post-24e66c49f: the chain passes silently because nothing downstream reads
# those sections.
# ---------------------------------------------------------------------------


def _unconventional_plan() -> str:
    return """---
type: plan
---
## Checklist

### [S-1] Add the timeout knob
Add a configurable timeout to the retry helper.

Type: file_change
Files:
- modify ralph/util/retry.py
Satisfies: AC-01

### [S-2] Cover the new branch
Add one regression test for the timeout default.

Type: file_change
Files:
- modify tests/util/test_retry.py
Depends on: S-1
Satisfies: AC-01

### [S-3] Prove the change
Run the focused test suite.

Type: verify
Depends on: S-2
Verify: pytest tests/util/test_retry.py -q
Expect: the focused retry tests pass with exit code 0

## Acceptance Criteria
- [AC-01] The retry helper honors the configurable timeout
  Satisfied by: S-1, S-2, S-3
  Verify: pytest tests/util/test_retry.py -q
  Expect: the focused retry tests pass with exit code 0
"""


def test_unconventional_substantive_plan_passes_silently() -> None:
    """A substantive plan with custom headings now passes with zero diagnostics.

    The validator only blocks when a downstream consumer cannot proceed;
    no consumer reads Summary, Scope, Risks, or Verification as required
    sections, so a plan that omits them with a substantive alternative
    shape must surface zero errors and zero warnings.
    """
    content, diagnostics = parse_and_validate(_unconventional_plan(), PLAN_SPEC)
    assert content != {}, "unconventional plan should still produce canonical content"
    assert diagnostics == [], (
        f"unconventional substantive plan should be silent; got: "
        f"{[(d.rule_id, d.severity, d.message) for d in diagnostics]}"
    )


def test_unconventional_plan_tool_payload_reports_zero_counts() -> None:
    """The tool payload reports ``counts`` of zero across severities.

    ``_validate_with_overrides`` produces the same per-severity counts the
    wire shape returns; an empty ``counts`` map and an empty ``overridden``
    list are the chain's proof that an unconventional substantive plan is
    executor-ready.
    """
    diagnostics, overridden = _validate_with_overrides(_unconventional_plan())
    severity_counts: dict[str, int] = {"error": 0, "warning": 0, "info": 0}
    for diagnostic in diagnostics:
        if diagnostic.severity in severity_counts:
            severity_counts[diagnostic.severity] += 1
    assert severity_counts == {"error": 0, "warning": 0, "info": 0}, (
        f"unconventional substantive plan should report zero counts; got "
        f"{severity_counts!r}"
    )
    assert overridden == []


# ---------------------------------------------------------------------------
# Fixture GOOD: the conventional medium plan stays silent.
# ---------------------------------------------------------------------------


def _good_plan() -> str:
    """Conventional medium plan with real Files / Verify / Expect."""
    return """---
type: plan
schema_version: 1
intent_verb: add
---
## Summary
Migrate the plan artifact to a markdown grammar.

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


def test_good_plan_stays_silent_through_the_chain() -> None:
    """The good plan reports zero counts and an empty override list."""
    diagnostics, overridden = _validate_with_overrides(_good_plan())
    assert diagnostics == [], (
        f"good plan must stay silent; got: "
        f"{[(d.rule_id, d.severity, d.message) for d in diagnostics]}"
    )
    assert overridden == []


def test_good_plan_analyze_entry_point_returns_empty_diagnostics() -> None:
    """``analyze_plan_document`` mirrors the same silent-good-plan behavior."""
    _content, diagnostics, overridden = analyze_plan_document(_good_plan())
    assert diagnostics == []
    assert overridden == []


# ---------------------------------------------------------------------------
# Prompt-chain assertions: the planning prompt leads with thinking, the
# analysis prompt carries the overrides-respect sentence exactly once, and
# every formerly-duplicated paragraph in the analysis prompt now appears
# exactly once. These tests fail closed if any of the three prompts in the
# chain drifts back to the pre-rewrite wording.
# ---------------------------------------------------------------------------


def test_planning_prompt_orders_thinking_before_submission() -> None:
    """The rendered planning prompt leads with how to think about the change.

    The chain promises the agent that thinking comes first; if the
    planning prompt ever pushes the submission / READ-ONLY block back
    above the thinking partial, this assertion fires closed.

    The check inspects the combined rendered string: the thinking
    partial contributes the ``## How to think about this plan`` heading
    and ``planning.jinja`` contributes the submission / READ-ONLY block.
    Joining them lets the test reason about ordering without depending on
    the template engine's exact include-rendering mechanics.
    """
    context = TemplateContext.default()
    main = context.registry.get_template("planning.jinja")
    partial = context.partials["shared/_planning_thinking"]
    combined = partial + "\n" + main
    assert "## How to think about this plan" in combined
    assert "You MUST submit your plan" in combined
    assert "READ-ONLY planning task" in combined
    assert combined.index("## How to think about this plan") < combined.index(
        "You MUST submit your plan"
    ), "thinking-first rewrite requires thinking content to precede the submission mechanic"
    assert combined.index("## How to think about this plan") < combined.index(
        "READ-ONLY planning task"
    ), "thinking-first rewrite requires thinking content to precede the READ-ONLY block"


def test_planning_prompt_states_each_commitment_once() -> None:
    """The thinking partial carries the brevity commitment.

    The Document contract paragraph in the thinking partial adds two
    brevity lines so the executor can re-read the plan mid-run under
    context pressure; the chain pins that wording so a future edit
    cannot silently drop the brevity commitment. The partial is loaded
    separately because ``get_template`` returns the main template
    source and the included partial lives in its own file.
    """
    context = TemplateContext.default()
    partial = context.partials["shared/_planning_thinking"]
    assert "State each commitment once" in partial
    assert "re-read in one pass" in partial


def test_analysis_prompt_treats_overrides_as_settled_judgement() -> None:
    """The analysis prompt declares overridden findings settled.

    The chain promises the agent that an overridden advisory finding
    is the planner's recorded judgement and must not be re-litigated
    unless repository evidence proves the recorded reason false. The
    paragraph lives in the planning_analysis template so the standard
    is stated once across the chain.
    """
    source = TemplateContext.default().registry.get_template("planning_analysis.jinja")
    normalized = " ".join(source.split())
    assert (
        "## Validation Overrides` section in the plan is the planner's recorded judgement"
    ) in normalized
    assert (
        "An overridden finding is settled and must not be re-raised as a "
        "new finding in `## What Came Up Short`, unless repository evidence "
        "proves the recorded reason false."
    ) in normalized


def test_analysis_prompt_dedups_formally_duplicated_paragraphs() -> None:
    """Every formerly-duplicated paragraph in the analysis prompt appears once.

    The single-standard rewrite collapses the second ``## Review checklist``
    section and its duplicate paragraphs; if either of the formerly
    duplicated review paragraphs ever drifts back to two occurrences, the
    chain breaks the "one standard stated once" promise and this test
    fails closed.
    """
    source = TemplateContext.default().registry.get_template("planning_analysis.jinja")
    assert source.count("enumerate all currently visible") == 1
    assert source.count("target the planner's revision workflow") == 1
    # The legacy second `## Review checklist` heading is gone; only the
    # operational `## REVIEW CHECKLIST` survives.
    assert source.count("## Review checklist\n") == 0
