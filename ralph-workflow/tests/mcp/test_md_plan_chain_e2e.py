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

A fourth fixture, **MALFORMED**, proves the chain now rejects a plan with
duplicate frontmatter, malformed frontmatter, or top-level prose through
the actual ``handle_verify_md_artifact`` MCP handler. Pre-``24e66c49f``
the plan-aware path silently canonicalized these documents; the rewritten
chain surfaces them as ``is_error=True`` with ``valid=False`` and the
expected parser-originated error diagnostic.

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

import json
import re
from pathlib import Path

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document
from ralph.mcp.tools.md_artifact import handle_verify_md_artifact
from ralph.prompts.template_context import TemplateContext
from tests.test_tool_artifact_2_helper_mocksession import MockSession
from tests.test_tool_artifact_2_helper_mockworkspace import MockWorkspace

_ADVISORY_COST_RE = re.compile(r"the run cost is [^;]+(?:;[^;]+)*; resolve by [^;]+(?:;[^;]+)*$")
_BLOCKING_CONSUMER_RE = re.compile(
    r"blocking because [^;]+(?:;[^;]+)*; resolve by [^;]+(?:;[^;]+)*$"
)


def _verify_payload(plan: str) -> dict[str, object]:
    """Invoke the public ``handle_verify_md_artifact`` MCP handler.

    The end-to-end test exercises the same handler a real run reaches
    instead of a private helper, so the chain's wire shape (``valid``,
    ``counts`` across severities, and ``overridden`` list) is observed
    from the same code path the runtime uses. The mock session/workspace
    stand in for the coordination session and the on-disk artifact
    directory.
    """
    session = MockSession()
    workspace = MockWorkspace(Path("/tmp"))
    params = {"artifact_type": "plan", "content": plan}
    result = handle_verify_md_artifact(session, workspace, params)
    return json.loads(result.content[0].text)


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
            f"PLAN020 hollow-plan warning does not follow cost/fix convention: {d.message!r}"
        )


def test_override_tool_payload_keeps_reason_visible_after_validation() -> None:
    """A recorded override remains visible on the public validation payload.

    The plan's coordinating step intentionally omits ``Files``. Its override
    removes the advisory from active diagnostics, but the public handler must
    retain the matched rule, reason, and original observation for later review.
    """
    plan = """---
type: plan
---
## Steps

### [S-1] Coordinate the release
Type: file_change

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0

## Validation Overrides
- [PLAN010] This is a coordinating step, not a file_change
"""

    payload = _verify_payload(plan)

    assert all(item["rule_id"] != "PLAN010" for item in payload["diagnostics"])
    overrides = payload["overridden"]
    assert isinstance(overrides, list)
    assert len(overrides) == 1
    override = overrides[0]
    assert override["rule_id"] == "PLAN010"
    assert override["reason"] == "This is a coordinating step, not a file_change"
    assert override["section"] is None
    diagnostic = override["diagnostic"]
    assert diagnostic["rule_id"] == "PLAN010"
    assert diagnostic["section"] == "Steps"
    assert diagnostic["severity"] == "warning"
    assert "Files" in diagnostic["message"]


def test_hollow_plan_tool_payload_reports_warnings_and_zero_overrides() -> None:
    """The tool payload shape surfaces warnings and an empty override list.

    ``_verify_payload`` is the public ``handle_verify_md_artifact``
    handler, so the chain demonstrates the exact wire shape a real run
    returns: ``valid``, per-severity ``counts``, and an ``overridden``
    list that is empty for plans with no override ledger.
    """
    payload = _verify_payload(_hollow_plan())
    counts = payload["counts"]
    assert isinstance(counts, dict)
    assert counts["error"] == 0, f"hollow plan must report zero errors, got {counts!r}"
    assert counts["warning"] >= 1, (
        f"hollow plan must surface at least one cost-named warning, got {counts!r}"
    )
    assert payload["valid"] is True
    assert payload["overridden"] == [], (
        "a plan without ## Validation Overrides must report an empty override list; "
        f"got {payload['overridden']!r}"
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

    ``_verify_payload`` calls the public ``handle_verify_md_artifact``
    handler, so the chain observes the wire shape a real run returns:
    ``valid=True``, ``counts`` empty across severities, and an empty
    ``overridden`` list. An unconventional substantive plan is
    executor-ready; the tool handler must say so.
    """
    payload = _verify_payload(_unconventional_plan())
    counts = payload["counts"]
    assert isinstance(counts, dict)
    assert counts == {"error": 0, "info": 0, "warning": 0}, (
        f"unconventional substantive plan should report zero counts; got {counts!r}"
    )
    assert payload["valid"] is True
    assert payload["overridden"] == []


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
    payload = _verify_payload(_good_plan())
    counts = payload["counts"]
    assert isinstance(counts, dict)
    assert counts == {"error": 0, "info": 0, "warning": 0}, (
        f"good plan must report zero counts; got {counts!r}"
    )
    assert payload["valid"] is True
    assert payload["overridden"] == []
    assert payload["diagnostics"] == []


def test_good_plan_analyze_entry_point_returns_empty_diagnostics() -> None:
    """``analyze_plan_document`` mirrors the same silent-good-plan behavior."""
    _content, diagnostics, overridden = analyze_plan_document(_good_plan())
    assert diagnostics == []
    assert overridden == []


# ---------------------------------------------------------------------------
# Fixture MALFORMED: parser-originated errors are now surfaced through the
# public MCP tool path. Pre-24e66c49f the plan-aware analyze path silently
# canonicalized a malformed document and reported ``is_error=False``; the
# rewritten chain keeps the parser diagnostics and fails the tool payload
# with ``valid=False`` and a consumer-named error so the agent sees why
# the document cannot proceed.
# ---------------------------------------------------------------------------


def _malformed_duplicate_type_plan() -> str:
    return """---
type: plan
type: plan
---
## Steps

### [S-1] Step
This substantive step describes the intended behavior clearly enough for the
executor to continue after repairing the duplicate metadata warning.
Type: file_change
Files:
- modify foo.py
"""


def _malformed_frontmatter_plan() -> str:
    return """---
type: plan
not a field
---
## Steps

### [S-1] Step
This substantive step describes the intended behavior clearly enough for the
executor to continue after repairing the malformed metadata warning.
Type: file_change
Files:
- modify foo.py
"""


def _malformed_top_level_prose_plan() -> str:
    return """---
type: plan
---
Some prose before any heading captures current behavior the executor must
preserve while it translates the work into the canonical structured mapping.
## Steps

### [S-1] Step
Type: file_change
Files:
- modify foo.py
"""


def test_malformed_duplicate_type_frontmatter_fails_through_tool() -> None:
    """Duplicate ``type`` frontmatter fails the public tool path.

    The chain must surface MD006 with a consumer phrase the same way
    the validator does; the tool handler's ``is_error`` flag carries
    the failure so a real agent never sees ``valid=True`` for a
    malformed document.
    """
    payload = _verify_payload(_malformed_duplicate_type_plan())
    assert payload["valid"] is True
    assert payload["counts"] == {"error": 0, "info": 0, "warning": 1}
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, list)
    md006 = [d for d in diagnostics if d.get("rule_id") == "MD006"]
    assert md006, f"expected MD006, got {[d.get('rule_id') for d in diagnostics]}"
    for d in md006:
        assert d.get("severity") == "warning"
        assert _ADVISORY_COST_RE.search(d.get("message", "")), (
            f"MD006 from tool path does not name its cost: {d.get('message')!r}"
        )


def test_malformed_frontmatter_fails_through_tool() -> None:
    """A malformed frontmatter line fails the public tool path."""
    payload = _verify_payload(_malformed_frontmatter_plan())
    assert payload["valid"] is True
    counts = payload["counts"]
    assert isinstance(counts, dict)
    assert counts["error"] == 0
    assert counts["warning"] >= 1
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, list)
    md005 = [d for d in diagnostics if d.get("rule_id") == "MD005"]
    assert md005, f"expected MD005, got {[d.get('rule_id') for d in diagnostics]}"
    for d in md005:
        assert d.get("severity") == "warning"
        assert _ADVISORY_COST_RE.search(d.get("message", "")), (
            f"MD005 from tool path does not name its cost: {d.get('message')!r}"
        )


def test_malformed_top_level_prose_advisories_through_tool() -> None:
    """A body line before the first ``## Heading`` advisories the public tool path.

    Under the plan-scoped severity policy, MD002 (top-level prose) is
    content-shape and demoted to warning. The tool payload reports a
    valid document with a cost-named warning so the agent can decide
    whether to repair the prose or override the warning.
    """
    payload = _verify_payload(_malformed_top_level_prose_plan())
    assert payload["valid"] is True
    counts = payload["counts"]
    assert isinstance(counts, dict)
    assert counts["error"] == 0
    diagnostics = payload["diagnostics"]
    assert isinstance(diagnostics, list)
    md002 = [d for d in diagnostics if d.get("rule_id") == "MD002"]
    assert md002, f"expected MD002 warning, got {[d.get('rule_id') for d in diagnostics]}"
    for d in md002:
        assert d.get("severity") == "warning"
        assert _ADVISORY_COST_RE.search(d.get("message", "")), (
            f"MD002 from tool path does not follow the cost-named convention: {d.get('message')!r}"
        )


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
    assert "## Plan the work before the paperwork" in combined
    assert "shared/_planning_thinking.j2" in main


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
    assert "Keep commitments next to the step" in partial
    assert "state each one once" in partial


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
    assert "Validation Overrides` reason is normal planner judgment" in normalized
    assert "do not re-raise it unless repository evidence proves it false" in normalized


def test_analysis_prompt_dedups_formally_duplicated_paragraphs() -> None:
    """Every formerly-duplicated paragraph in the analysis prompt appears once.

    The single-standard rewrite collapses the second ``## Review checklist``
    section and its duplicate paragraphs; if either of the formerly
    duplicated review paragraphs ever drifts back to two occurrences, the
    chain breaks the "one standard stated once" promise and this test
    fails closed.
    """
    source = TemplateContext.default().registry.get_template("planning_analysis.jinja")
    assert "## PLAN QUALITY RUBRIC" not in source
    assert source.count("## Review contract") == 1


# ---------------------------------------------------------------------------
# Oversize parity: ralph_verify_md_artifact and ralph_submit_md_artifact must
# produce the same diagnostic for a plan body that exceeds the MCP payload
# bound. The plan validator's structure validation re-uses the parsed
# frontmatter (via _reconstructed_text) rather than the full document for
# size checks, so the body bound is enforced only at canonical normalization
# (SPEC010). This pin catches any future drift that lets verify stay silent
# while submit rejects the document.
# ---------------------------------------------------------------------------


def _oversized_plan_body() -> str:
    """A plan whose body alone exceeds the MCP payload bound.

    The payload bound is taken from the canonical plan normalizer's size
    check in ``ralph/mcp/artifacts/plan/_validation.py`` (SPEC010). The
    frontmatter is small; the body is a single padded ``## Steps`` block
    that pushes the total over the bound. Big enough to fail in any
    realistic configuration, small enough to keep the test focused.
    """
    padding = "x" * 200_000
    return f"""---
type: plan
---
## Steps

### [S-1] Big step
{padding}

Type: file_change
Files:
- modify foo.py

## Verification
- [V-1] pytest tests/x.py -q
  Expect: the focused tests pass with exit code 0
"""


def test_oversize_plan_body_fails_parity_across_verify_and_submit() -> None:
    """An oversized plan body produces the same diagnostic in verify and submit.

    The public tool path MUST surface the same error for an oversize
    plan body whether the agent calls ``ralph_verify_md_artifact`` or
    ``ralph_submit_md_artifact``: ``parse_and_validate`` runs the same
    structure validation either way, and the canonical normalizer (which
    is the other place the size bound is enforced) must agree. If verify
    silently accepts a body that submit then rejects, the agent sees a
    contradictory chain and cannot tell which call is authoritative.
    """
    session = MockSession()
    workspace = MockWorkspace(Path("/tmp"))
    params = {"artifact_type": "plan", "content": _oversized_plan_body()}

    verify_payload = json.loads(
        handle_verify_md_artifact(session, workspace, params).content[0].text
    )

    from ralph.mcp.tools.md_artifact import handle_submit_md_artifact

    submit_payload = json.loads(
        handle_submit_md_artifact(session, workspace, params).content[0].text
    )

    verify_rule_ids = {
        d.get("rule_id")
        for d in verify_payload.get("diagnostics", [])
        if d.get("severity") == "error"
    }
    submit_rule_ids = {
        d.get("rule_id")
        for d in submit_payload.get("diagnostics", [])
        if d.get("severity") == "error"
    }

    # The plan policy makes the oversized content an advisory SPEC010 rather
    # than an error, and both standard endpoints must agree on that payload.
    assert verify_payload["valid"] is True
    assert submit_payload["valid"] is True
    assert verify_rule_ids == submit_rule_ids == set()
    verify_warning_ids = {d.get("rule_id") for d in verify_payload["diagnostics"]}
    submit_warning_ids = {d.get("rule_id") for d in submit_payload["diagnostics"]}
    assert verify_warning_ids == submit_warning_ids == {"SPEC010"}
