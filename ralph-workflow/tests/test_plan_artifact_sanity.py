"""Public sanity boundary and partial extraction regression scenarios."""

from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document


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
            "dependencies": [],
            "step_ids": ["S-1"],
        }
    ]
    assert content["parallel_plan"] == [
        {
            "id": "tests",
            "description": "Update tests",
            "edit_area": {"directories": [], "paths": ["tests/api.py"]},
            "depends_on": [],
            "step_ids": ["S-2"],
        }
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
