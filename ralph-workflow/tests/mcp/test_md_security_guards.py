"""Security contracts for commit markdown artifact specs."""

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.specs.commit_cleanup import COMMIT_CLEANUP_SPEC
from ralph.mcp.artifacts.markdown.specs.commit_message import COMMIT_MESSAGE_SPEC


def test_commit_cleanup_spec_preserves_path_and_pattern_injection_guards() -> None:
    valid, valid_diagnostics = parse_and_validate(
        "---\ntype: commit_cleanup\nanalysis_complete: false\n---\n"
        "## Actions\n- [A1] add_to_gitignore | *.pyc\n",
        COMMIT_CLEANUP_SPEC,
    )

    assert valid == {
        "analysis_complete": False,
        "actions": [{"action": "add_to_gitignore", "pattern": "*.pyc"}],
    }
    assert valid_diagnostics == []

    for injected_value in ("#disabled-rule", "caf\u00e9"):
        content, diagnostics = parse_and_validate(
            "---\ntype: commit_cleanup\nanalysis_complete: false\n---\n## Actions\n"
            f"- [A1] add_to_gitignore | {injected_value}\n",
            COMMIT_CLEANUP_SPEC,
        )

        assert content == {}
        assert any(diagnostic.severity == "error" for diagnostic in diagnostics)


def test_commit_message_spec_preserves_conventional_subject_validation() -> None:
    valid, valid_diagnostics = parse_and_validate(
        "---\ntype: commit\nsubject: fix(parser): reject unsafe cleanup patterns\n---\n"
        "## Body\n- [B1] Preserve the existing validation boundary.\n",
        COMMIT_MESSAGE_SPEC,
    )
    invalid, invalid_diagnostics = parse_and_validate(
        "---\ntype: commit\nsubject: unsafe cleanup patterns\n---\n",
        COMMIT_MESSAGE_SPEC,
    )

    assert valid == {
        "type": "commit",
        "subject": "fix(parser): reject unsafe cleanup patterns",
        "body": "Preserve the existing validation boundary.",
    }
    assert valid_diagnostics == []
    assert invalid == {}
    assert any("conventional commit format" in diagnostic.message for diagnostic in invalid_diagnostics)

