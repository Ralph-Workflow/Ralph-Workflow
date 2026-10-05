"""Public sanity boundary and partial extraction regression scenarios."""

from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document


def test_unit_ownership_only_references_usable_extracted_steps() -> None:
    oversized_id = "S-" + "9" * 4_301
    text = (
        "## Work Units\n- [api] Implement the requested API changes with focused behavior tests.\n"
        "  Paths: src/api.py\n"
        f"### [{oversized_id}] Keep this unextractable step as prose\n"
        "### [S-1] Implement the usable step\n"
    )
    content, diagnostics, _ = analyze_plan_document(text)
    assert diagnostics == []
    assert content["work_units"] == [{
        "unit_id": "api",
        "description": "Implement the requested API changes with focused behavior tests.",
        "allowed_directories": [],
        "allowed_paths": ["src/api.py"],
        "dependencies": [],
        "step_ids": ["S-1"],
    }]


def test_colon_prefixed_prose_outside_frontmatter_remains_main_session_work() -> None:
    text = (
        "---\nnoop: false\n---\n"
        "Integration: Verify all requested behavior after the independent API changes finish.\n"
        "## Work Units\n- [api] Implement API changes\n  Paths: src/api.py\n"
    )
    content, diagnostics, _ = analyze_plan_document(text)
    assert diagnostics == []
    assert content.get("unextractable_work_units") is True



def test_plan_regression_heading_only_tasks_remain_main_session_work() -> None:
    """DA-016: retain residual tasks expressed in headings, not only body prose."""
    units = (
        "## Work Units\n"
        "- [one] Implement first component\n  Paths: src/one.py\n"
        "- [two] Implement second component\n  Paths: src/two.py\n"
    )
    task = "Prepare release/manifest.json after both components finish"
    for text in (
        "## Release preparation\n" + task + "\n" + units,
        units + "## " + task + "\n",
        "## " + task + "\n" + units,
        units + "### " + task + "\n",
        "## " + task + "\n### Work Units\n" + units.removeprefix("## Work Units\n"),
    ):
        content, diagnostics, _ = analyze_plan_document(text)
        assert diagnostics == []
        assert content.get("unextractable_work_units") is True

    content, diagnostics, _ = analyze_plan_document("# Implementation plan\n" + units)
    assert diagnostics == []
    assert content.get("unextractable_work_units") is True


def test_sanity_applies_to_noop_and_invalid_unicode() -> None:
    for text in (
        "---\ntype: plan\nnoop: true\n---",
        "one two three four five six seven eight nine",
        "\ud800 " * 10,
    ):
        content, diagnostics, overrides = analyze_plan_document(text)
        assert content == {}
        assert [item.rule_id for item in diagnostics] == ["PLAN001"]
        assert overrides == []
    for text in (
        "one two three four five six seven eight nine ten",
        "---\nnoop: true\n---\nNo changes are needed because all requested behavior already works correctly.",
    ):
        _, diagnostics, _ = analyze_plan_document(text)
        assert diagnostics == []


def test_partial_extraction_keeps_canonical_steps_targets_and_unit_paths() -> None:
    text = """Implement the requested changes and verify the resulting public behavior carefully.
## Work Units
- [api] Update API
  Paths: src/api.py
### [S-1] Update API behavior
Files:
- modify src/api.py
Depends on: S-2
### [S-bad] Ignore unusable identifier
### [S-1] Duplicate remains raw prose
## Parallel Plan
- [tests] Update tests
  Paths: tests/api.py
### [S-2] Prove behavior
Files:
- create tests/api.py
Depends on: S-1
"""
    content, diagnostics, _ = analyze_plan_document(text)
    assert diagnostics == []
    assert content["steps"] == [
        {
            "id": "S-1",
            "number": 1,
            "title": "Update API behavior",
            "content": "Files:\n- modify src/api.py\nDepends on: S-2",
            "depends_on": [2],
            "targets": [{"path": "src/api.py", "action": "modify"}],
        },
        {
            "id": "S-2",
            "number": 2,
            "title": "Prove behavior",
            "content": "Files:\n- create tests/api.py\nDepends on: S-1",
            "depends_on": [1],
            "targets": [{"path": "tests/api.py", "action": "create"}],
        },
    ]
    assert content["work_units"] == [
        {
            "unit_id": "api",
            "description": "Update API",
            "allowed_directories": [],
            "allowed_paths": ["src/api.py"],
            "dependencies": ["tests"],
            "step_ids": ["S-1"],
        }
    ]
    assert content["parallel_plan"] == [
        {
            "id": "tests",
            "description": "Update tests",
            "edit_area": {"directories": [], "paths": ["tests/api.py"]},
            "depends_on": ["api"],
            "step_ids": ["S-2"],
        }
    ]

    document = """Implement independent API changes after the shared contract and verify everything.
## Work Units
- [contract] Define shared contract
  Paths: src/types.py
- [api] Implement API behavior
  Paths: src/api.py
## Steps
### [S-1] Define shared contract
Files:
- modify src/types.py
### [S-2] Implement API
Files:
- modify src/api.py
Depends on: S-1
"""
    content, diagnostics, _ = analyze_plan_document(document)
    assert diagnostics == []
    assert content["work_units"] == [
        {
            "unit_id": "contract",
            "description": "Define shared contract",
            "allowed_directories": [],
            "allowed_paths": ["src/types.py"],
            "dependencies": [],
            "step_ids": ["S-1"],
        },
        {
            "unit_id": "api",
            "description": "Implement API behavior",
            "allowed_directories": [],
            "allowed_paths": ["src/api.py"],
            "dependencies": ["contract"],
            "step_ids": ["S-2"],
        },
    ]


def test_unreadable_and_obvious_nonplan_text_is_rejected() -> None:
    prose = "Implement the requested changes and verify public behavior with focused tests."
    for text in (
        "",
        prose + "\x00",
        "\x01" * 40 + prose,
        "ID3\x03\x01\x02" * 100,
        "I cannot complete this request because I cannot access any repository files.",
        "todo: plan " + prose,
    ):
        _, diagnostics, _ = analyze_plan_document(text)
        assert [item.rule_id for item in diagnostics] == ["PLAN001"]
    for text in ("---\nbroken frontmatter\n" + prose, "---\ntype: plan\ntype: plan\n---\n" + prose):
        content, diagnostics, _ = analyze_plan_document(text)
        assert diagnostics == []
        assert content == {}


def test_legitimate_aid_and_ai_self_description_are_accepted() -> None:
    """DA-001 regression: actionable prose beginning ``As an aid`` or
    ``As an AI engineer`` must not be flagged as an AI refusal. The
    original detector substring-matched the literal ``as an ai``, which
    also caught the distinct English word ``as an aid`` (a, i, d) and
    legitimate self-descriptions like ``As an AI engineer, inspect the
    parser...``. Per the product criteria, refusal detection is reduced
    to obvious cases (``I cannot``/``I'm sorry``); the ``as an ai``
    family is dropped entirely so every readable, on-topic plan
    receives a receipt.
    """
    legitimate_plans = (
        "As an aid to maintainers, document the public API, implement "
        "regression tests, and verify the resulting behavior thoroughly.",
        "As an AI engineer, inspect the parser, implement the fix, and "
        "verify the resulting public behavior with focused regression tests.",
        "As an AI language model with access to the repository, here is "
        "the proposed plan for the parser fix and its verification steps.",
    )
    for text in legitimate_plans:
        _, diagnostics, _ = analyze_plan_document(text)
        assert diagnostics == [], (
            f"unexpected PLAN001 for legitimate plan: {text!r}; got {[d.rule_id for d in diagnostics]}"
        )
    # Genuine refusals still must fail the sanity gate via the
    # ``I cannot`` / ``I'm sorry`` prefixes.
    for text in (
        "I cannot complete this request because I cannot access the repository secrets.",
        "I'm sorry, I cannot help with that request as an AI assistant without more information.",
    ):
        _, diagnostics, _ = analyze_plan_document(text)
        assert [item.rule_id for item in diagnostics] == ["PLAN001"]


def test_legitimate_constraint_clauses_with_implementation_work_are_accepted() -> None:
    """DA-001 regression: actionable prose that *opens* with a constraint
    clause (``I cannot change X, so implement Y and verify Z``) must
    receive a receipt. The original detector prefix-matched the
    literal ``I cannot`` and rejected the whole message, even when
    the rest of the text prescribes concrete implementation and
    verification work. A real refusal (``I cannot access the
    repository secrets``) describes no action and remains a refusal.
    """
    legitimate_plans = (
        # The demonstrated DA-001 counterexample: opens with a
        # constraint clause, then prescribes implementation and
        # regression verification.
        "I cannot change the public API without breaking "
        "compatibility, so implement the fix internally and verify "
        "existing callers with regression tests.",
        # ``I cannot`` constraint followed by an action chain
        # ``build, run, verify``.
        "I cannot extend the legacy adapter to support the new "
        "field, so add a small translator, run the regression "
        "suite, and verify the resulting public behavior is "
        "unchanged for existing callers.",
        # ``I'm sorry`` opening with a follow-up implementation
        # sentence is also an actionable plan, not a refusal.
        "I'm sorry, I cannot make that change, but I can refactor "
        "the helper to keep the public contract and add a focused "
        "regression test for the affected branch.",
    )
    for text in legitimate_plans:
        _, diagnostics, _ = analyze_plan_document(text)
        assert diagnostics == [], (
            f"unexpected PLAN001 for legitimate plan: {text!r}; "
            f"got {[d.rule_id for d in diagnostics]}"
        )
    # Genuine refusals still must fail the sanity gate: every
    # ``I cannot`` / ``I'm sorry`` line that does not prescribe
    # any implementation, verification, or test work is a refusal.
    for text in (
        "I cannot complete this request because I cannot access "
        "any repository files.",
        "I cannot complete this request because policy prevents me "
        "from helping you today.",
        "I'm sorry, I cannot help with that request as an AI "
        "assistant without more information.",
    ):
        _, diagnostics, _ = analyze_plan_document(text)
        assert [item.rule_id for item in diagnostics] == ["PLAN001"], (
            f"expected PLAN001 for refusal: {text!r}; "
            f"got {[d.rule_id for d in diagnostics]}"
        )


def test_multiline_constraint_preface_followed_by_plan_is_accepted() -> None:
    """DA-001 regression: a multi-line submission that opens with an
    explicit ``I cannot implement this request because X`` constraint
    preface and follows with concrete implementation, test, and
    verification work is a constrained plan, not a refusal. The
    detector must reject refusals only when the entire message is a
    refusal.
    """
    legitimate_plans = (
        # The exact DA-001 counterexample probed against the public
        # handler: constraint preface line followed by actionable
        # implementation guidance.
        "I cannot implement this request because production credentials are unavailable.\n"
        "Implement an offline adapter using recorded responses, add deterministic tests, and verify the public interface.",
        # ``I cannot complete`` preface followed by an explicit
        # numbered step list on subsequent lines.
        "I cannot complete this request because the upstream service is unreachable.\n"
        "1. Stub the upstream calls with deterministic responses.\n"
        "2. Wire the stub through the public interface.\n"
        "3. Add a regression test that exercises the stubbed path.",
        # ``I'm sorry`` preface followed by a separate sentence
        # prescribing work.
        "I'm sorry, I cannot run that command without elevated access.\n"
        "Mock the elevated call, exercise the public surface, and verify the regression coverage.",
    )
    for text in legitimate_plans:
        _, diagnostics, _ = analyze_plan_document(text)
        assert diagnostics == [], (
            f"unexpected PLAN001 for legitimate multiline plan: {text!r}; "
            f"got {[d.rule_id for d in diagnostics]}"
        )
    # Single-line refusals still must fail the sanity gate.
    for text in (
        "I cannot complete this request because production credentials are unavailable.",
        "I cannot implement this request because policy blocks me from helping you today.",
        "I'm sorry, I cannot help with that request as an AI assistant without more information.",
    ):
        _, diagnostics, _ = analyze_plan_document(text)
        assert [item.rule_id for item in diagnostics] == ["PLAN001"], (
            f"expected PLAN001 for single-line refusal: {text!r}; "
            f"got {[d.rule_id for d in diagnostics]}"
        )


def test_legitimate_plans_mentioning_placeholder_text_are_accepted() -> None:
    """DA-001 regression: a real plan that *discusses* a placeholder
    marker (for example, instructing removal of the obsolete text) must
    not be flagged as a placeholder. Placeholder detection is anchored
    to the first non-empty line so discussions of the marker stay valid.
    """
    legitimate = (
        "Remove the obsolete todo: plan placeholder from documentation "
        "and verify the updated planning instructions with focused tests."
    )
    _, diagnostics, _ = analyze_plan_document(legitimate)
    assert diagnostics == [], [d.rule_id for d in diagnostics]
    # Other marker names that must not falsely trip the placeholder
    # check when they appear in the middle of a real plan.
    for text in (
        "Refactor the fixme: plan annotation away and re-run the regression "
        "tests for the affected module today.",
        "Document the tbd: plan status in the planning section and add the "
        "outstanding items to the next iteration's tracker today.",
        "Reuse the existing plan goes here comment as a header above the "
        "new code path and add the regression in the same change today.",
    ):
        _, diagnostics, _ = analyze_plan_document(text)
        assert diagnostics == [], f"{text!r} unexpectedly rejected"


def test_steps_section_prose_outside_owned_blocks_remains_main_session_work() -> None:
    text = """## Work Units
- [api] Implement the API change
  Paths: src/api.py
- [tests] Implement the API tests
  Paths: tests/api_test.py
## Steps
### [S-1] Implement the API change
Files:
- modify src/api.py
### [S-2] Implement the API tests
Files:
- modify tests/api_test.py
Prepare release/manifest.json after both units are complete.
"""
    content, diagnostics, _ = analyze_plan_document(text)

    assert diagnostics == []
    assert content["unextractable_work_units"] is True


def test_metadata_section_prose_without_steps_remains_main_session_work() -> None:
    for section_name in ("Summary", "Steps", "Verification"):
        text = f"""## {section_name}
Prepare release/manifest.json after both units finish and verify publication.
## Work Units
- [one] Implement the first component
  Paths: src/one.py
- [two] Implement the second component
  Paths: src/two.py
"""
        content, diagnostics, _ = analyze_plan_document(text)

        assert diagnostics == []
        assert content["unextractable_work_units"] is True


def test_prose_before_owned_step_blocks_remains_main_session_work() -> None:
    text = """## Work Units
- [one] Implement the first component
  Paths: src/one.py
- [two] Implement the second component
  Paths: src/two.py
## Steps
Prepare release/manifest.json after both units finish and verify publication.
### [S-1] Implement the first component
Files:
- modify src/one.py
### [S-2] Implement the second component
Files:
- modify src/two.py
"""
    content, diagnostics, _ = analyze_plan_document(text)

    assert diagnostics == []
    assert content["unextractable_work_units"] is True


def test_prose_in_owned_work_unit_step_block_remains_main_session_work() -> None:
    text = """## Work Units
- [one] Implement the first component
  Paths: src/one.py
### [S-1] Implement the first component
Files:
- modify src/one.py
Prepare release/manifest.json after the first component is complete.
- [two] Implement the second component
  Paths: src/two.py
"""
    content, diagnostics, _ = analyze_plan_document(text)

    assert diagnostics == []
    assert content["unextractable_work_units"] is True


def test_plan_regression_duplicate_units_cannot_reassign_nested_steps() -> None:
    """U-1: ambiguous identifiers retain prose without inventing a worker owner."""
    for second_section in ("Work Units", "Parallel Plan"):
        text = f"""## Work Units
- [api] Implement the first API component
  Paths: src/first.py
### [S-1] Implement the first API behavior
Files:
- modify src/first.py
## {second_section}
- [api] Implement the second API component
  Paths: src/second.py
### [S-2] Implement the second API behavior
Files:
- modify src/second.py
"""
        content, diagnostics, overrides = analyze_plan_document(text)

        assert diagnostics == []
        assert overrides == []
        assert content["unextractable_work_units"] is True
        assert content["work_units"] == [{
            "unit_id": "api",
            "description": "Implement the first API component",
            "allowed_directories": [],
            "allowed_paths": ["src/first.py"],
            "dependencies": [],
            "step_ids": [],
        }]
        if second_section == "Parallel Plan":
            assert content["parallel_plan"] == [{
                "id": "api",
                "description": "Implement the second API component",
                "edit_area": {"directories": [], "paths": ["src/second.py"]},
                "depends_on": [],
                "step_ids": [],
            }]


def test_plan_regression_duplicate_steps_make_worker_ownership_unextractable() -> None:
    """Ambiguous duplicate steps never permit unsafe native fan-out."""
    text = """## Work Units
- [api] First API component
  Paths: src/first.py
### [S-1] First behavior
## Parallel Plan
- [web] Second web component
  Paths: src/second.py
### [S-1] Second behavior
Depends on: S-99
"""

    content, diagnostics, overrides = analyze_plan_document(text)

    assert diagnostics == []
    assert overrides == []
    assert content["unextractable_work_units"] is True
