"""Coverage for the closed-list 'is this a plan?' detector.

The detector fires only on the four classes of "not a plan" that the brief
allows to block — empty / markup-only, under 100 characters of actual content,
or recognisably some other kind of text that arrived in the plan's place.
Every doubt-in-favor case stays advisory-free so a thin or unusual real plan
is never rejected for being one.

Tests are pure in-memory parse / validate calls — no I/O, no subprocess, no
sleep — so they fit inside the 60-second combined test budget.
"""

from __future__ import annotations

import re

import pytest

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs import PLAN_SPEC
from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document

# The closed-list detector uses a single rule_id (PLAN001) so error gating
# remains one rule_id per check and tests can pin it without coupling to a
# free-form message. The blocking convention requires each message to name
# its downstream consumer (analysis + executor have nothing to work with).
_BLOCKING_CONSUMER_RE = re.compile(
    r"blocking because [^;]+(?:;[^;]+)*; resolve by [^;]+(?:;[^;]+)*$"
)


# ---------------------------------------------------------------------------
# (a) Closed-list error cases — each must emit a PLAN001 error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("empty_string", ""),
        ("whitespace_only", "   \n\n   \n"),
        ("bare_heading", "## Heading\n"),
        ("horizontal_rule_only", "---\n---\n"),
        ("empty_code_fence", "```\n```\n"),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_empty_or_markup_only_text_emits_plan001_error(label: str, text: str) -> None:
    """Empty / whitespace / pure markup is not a plan — error, not advice."""
    content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d
        for d in diagnostics
        if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors, (
        f"expected PLAN001 error for {label!r}, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    for d in plan001_errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"PLAN001 for {label!r} does not name its consumer: {d.message!r}"
        )
    # The validator cannot map an empty document to a plan payload.
    assert content == {}


def test_text_under_100_actual_chars_emits_plan001_error() -> None:
    """Under 100 characters of actual content is not enough to be a plan."""
    # 80 chars of prose, well under the 100-char floor.
    text = "a quick plan to refactor the validator and verify it under pytest"
    assert len(text) < 100
    content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors, (
        f"expected PLAN001 for short text, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    assert content == {}


@pytest.mark.parametrize(
    ("label", "text"),
    [
        (
            "refusal_apology",
            "I'm sorry, I cannot help with that. I am an AI assistant without "
            "the ability to plan your project for you in this environment.",
        ),
        (
            "question_to_user",
            "What outcome are you trying to achieve? Could you tell me more "
            "about what you would like this plan to cover end to end here?",
        ),
        (
            "status_progress_message",
            "Working on the plan now. Currently inspecting repository state and "
            "drafting the orienting section before any concrete step block.",
        ),
        (
            "raw_tool_output",
            "ls -la ralph-workflow\ndrwxr-xr-x  user staff  B \"Jan 01 00:00\" ralph\n"
            "-rw-r--r--  user staff  B \"Jan 01 00:00\" AGENTS.md\n",
        ),
        (
            "stack_trace",
            "Traceback (most recent call last):\n  File \"/x.py\", line 5\n"
            "    raise RuntimeError(\"boom\")\nRuntimeError: boom\n",
        ),
        (
            "placeholder_todo",
            "TODO: plan goes here, finish later, see notes on the whiteboard",
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_recognisably_other_text_emits_plan001_error(
    label: str, text: str
) -> None:
    """An obvious non-plan that arrived in the plan's place is an error."""
    # Force the frontmatter so the parser does not surface a different error
    # ahead of the closed-list detector.
    wrapped = f"---\ntype: plan\n---\n{text}"
    content, diagnostics = parse_and_validate(wrapped, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors, (
        f"expected PLAN001 for {label!r}, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    for d in plan001_errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"PLAN001 for {label!r} does not name its consumer: {d.message!r}"
        )
    assert content == {}


# ---------------------------------------------------------------------------
# (b) Doubt-in-favor cases — a reasonable reader would call this a plan
# ---------------------------------------------------------------------------


def test_format_doc_tiny_example_is_a_plan() -> None:
    """The format doc's compact 'tiny task' example is the floor example."""
    text = """---
type: plan
---
## Checklist

### [S-1] Update and prove the timeout default
Change the default and add one focused regression test.

Type: file_change
Files:
- modify src/settings.py
- modify tests/test_settings.py
Verify: pytest tests/test_settings.py -q
Expect: the focused settings tests pass with exit code 0

## Acceptance Criteria
- [AC-01] The configured default is returned and covered by the regression test.
  Satisfied by: S-1
  Verify: pytest tests/test_settings.py -q
  Expect: the focused settings tests pass with exit code 0
"""
    content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors == [], (
        f"tiny example should not trip PLAN001, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    assert content != {}


def test_prose_plan_under_custom_headings_is_a_plan() -> None:
    """An unconventional prose plan under custom headings is still a plan."""
    text = """---
type: plan
---
## Orient
The user wants the validator to coach rather than police. Today the
validator refuses plans that a reasonable reader would call plans, and that
is the failure mode this change targets.

## Characterize
A plan with no embedded JSON, real Files:/Verify:/Expect: pairings, and
explicit Risks/Verification passes today with zero diagnostics — that is the
baseline the change must preserve.

## Change
Demote every content/shape finding to warning. Add a closed-list
not-a-plan detector that fires only on empty / under-100-char / obviously-
other-text submissions.

## Verify
Run `pytest tests/mcp/ -q`; the new not-a-plan tests must pass and every
existing relaxation test must still pass.
"""
    _content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors == [], (
        f"prose plan under custom headings should not trip PLAN001, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    # A prose-only plan without an `### [S-n]` block legitimately draws
    # PLAN022 — but at warning severity, not error.
    assert any(d.rule_id == "PLAN022" and d.severity == "warning" for d in diagnostics)


def test_minimal_noop_plan_is_exempt_from_length_floor() -> None:
    """The noop exemption covers the canonical zero-content plan."""
    text = """---
type: plan
noop: true
---"""
    content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors == [], (
        f"noop plan should not trip PLAN001, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    assert content == {"noop": True}


def test_plan_with_dangling_depends_on_is_a_plan() -> None:
    """A dangling Depends on is a warning, never a PLAN001 error.

    The plan is substantive (>100 chars of actual content) so the
    closed-list length floor does not reject it on its way to the
    advisory findings.
    """
    text = """---
type: plan
---
## Steps

### [S-1] First step
Implements the validating reducer that splits the buffer into headed and
non-headed sections before emitting a single normalized output structure.
Depends on: S-99

### [S-2] Second step
Lints the output structure against the canonical schema and surfaces any
deviation as a single warning diagnostic that names the line and the rule.
"""
    content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors == [], (
        f"dangling Depends on should not trip PLAN001, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    assert content != {}
    # The dangling reference surfaces at warning severity, not error.
    assert any(
        d.rule_id in {"PLAN021", "REF003"} and d.severity == "warning"
        for d in diagnostics
    )


def test_plan_with_dependency_cycle_is_a_plan() -> None:
    """A dependency cycle is a warning, never a PLAN001 error.

    Substantive (>100 chars of actual content) so the length floor does
    not reject it on its way to the advisory findings.
    """
    text = """---
type: plan
---
## Steps

### [S-1] First
The first step outlines the data flow from the input buffer to the parser
and captures the headline dependencies for downstream consumers.
Depends on: S-2

### [S-2] Second
The second step captures the schema validation pipeline and documents the
ordering invariant that downstream readers depend on for cycle detection.
Depends on: S-1
"""
    _content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors == [], (
        f"dependency cycle should not trip PLAN001, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    # The step dependency cycle is detected on the pydantic side
    # (SPEC010 with a cycle message); the plan-scoped severity policy
    # demotes the pydantic branch from error to warning.
    assert any(
        d.rule_id == "SPEC010" and d.severity == "warning" for d in diagnostics
    )


def test_file_change_step_without_files_is_a_plan() -> None:
    """A missing Files: line is a warning, never a PLAN001 error.

    Substantive (>100 chars of actual content) so the length floor does
    not reject it on its way to the advisory findings.
    """
    text = """---
type: plan
---
## Steps

### [S-1] Apply the change
The apply-the-change step rewrites the central router to call the new
helper in place of the legacy redirect, while keeping the public route
table byte-for-byte identical to the previous release.
Type: file_change
"""
    _content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors == [], (
        f"missing Files: should not trip PLAN001, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    assert any(d.rule_id == "PLAN010" and d.severity == "warning" for d in diagnostics)


def test_plan_with_malformed_step_id_is_a_plan() -> None:
    """A malformed step ID is a warning, never a PLAN001 error.

    Substantive (>100 chars of actual content) so the length floor does
    not reject it on its way to the advisory findings.
    """
    text = """---
type: plan
---
## Steps

### [STEP-1] Mistyped step
This heading is intended to be a plan step but uses the wrong prefix,
which the validator flags as a content-shape warning rather than as a
blocking plan-rejection diagnostic.
Type: action
"""
    _content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors == [], (
        f"malformed step ID should not trip PLAN001, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )


# ---------------------------------------------------------------------------
# (c) Plan-shaped under-100-character error case — the floor must fire even
#     when a single step block is present, since a truncated response
#     cannot state an outcome.
# ---------------------------------------------------------------------------


def test_plan_shaped_under_100_chars_emits_plan001_error() -> None:
    """A plan-shaped document whose body is under 100 chars is not a plan.

    The plan-shape bypass only protects the recognizably-other-text
    checks; the actual-content 100-character floor fires before the
    bypass so a truncated response that happens to include a single
    step block cannot avoid rejection on a shape coincidence.
    """
    text = """---
type: plan
---
## Steps

### [S-1] Do
"""
    _content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors, (
        f"expected PLAN001 error for plan-shaped under-100-char text, got: "
        f"{[(d.rule_id, d.severity) for d in diagnostics]}"
    )
    for d in plan001_errors:
        assert _BLOCKING_CONSUMER_RE.search(d.message), (
            f"PLAN001 for plan-shaped under-100-char text does not name its "
            f"consumer: {d.message!r}"
        )


def test_analyze_plan_document_propagates_plan001_error() -> None:
    """The MCP wire-shape helper sees the same PLAN001 error."""
    content, diagnostics, _overridden = analyze_plan_document("")
    plan001_errors = [
        d for d in diagnostics if d.rule_id == "PLAN001" and d.severity == "error"
    ]
    assert plan001_errors, "analyze_plan_document must surface PLAN001 on empty text"
    assert content == {}


def test_plan001_message_names_consumer_and_fix() -> None:
    """Every PLAN001 message follows the blocking consumer convention."""
    cases = [
        "",  # empty
        "## Heading\n",  # bare heading
        "I cannot help with that, sorry.",  # refusal
    ]
    for case in cases:
        wrapped = f"---\ntype: plan\n---\n{case}"
        _content, diagnostics = parse_and_validate(wrapped, PLAN_SPEC)
        for d in diagnostics:
            if d.rule_id == "PLAN001":
                assert _BLOCKING_CONSUMER_RE.search(d.message), (
                    f"PLAN001 message does not follow convention for case {case!r}: "
                    f"{d.message!r}"
                )
                assert "analysis" in d.message.lower() or "executor" in d.message.lower(), (
                    f"PLAN001 message must name analysis or executor as consumer: "
                    f"{d.message!r}"
                )
