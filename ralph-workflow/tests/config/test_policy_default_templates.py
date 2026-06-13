"""Black-box tests for the policy default TOML templates.

These tests pin the template-level content of
``ralph-workflow/ralph/policy/defaults/ralph-workflow.toml`` and
``ralph-workflow-local.toml`` so the new
``agent_workspace_change_weights`` key is documented in the right
place (immediately after the existing
``agent_idle_activity_evidence_ttl_seconds`` line) and the
project-local template is intentionally NOT modified in this
iteration.

The plan specifies: the new key lives on line 57, immediately
after the existing ``agent_idle_activity_evidence_ttl_seconds`` line
on line 56, under the ``[general]`` table header on line 34.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow.toml"
LOCAL_TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow-local.toml"


def _read_template() -> str:
    return TEMPLATE_PATH.read_text()


def test_policy_template_contains_agent_workspace_change_weights() -> None:
    """The bundled ``ralph-workflow.toml`` template documents the
    new ``agent_workspace_change_weights`` key (commented out)."""
    text = _read_template()
    assert "agent_workspace_change_weights" in text, (
        f"ralph-workflow.toml must document the new"
        f" agent_workspace_change_weights key, got:\n{text}"
    )


def test_policy_template_new_key_near_existing_activity_ttl() -> None:
    """The new ``agent_workspace_change_weights`` line appears
    WITHIN 3 LINES of the existing
    ``agent_idle_activity_evidence_ttl_seconds`` line (line 56).
    Operators reading the config see the two related keys together."""
    text = _read_template()
    lines = text.splitlines()
    new_key_lines = [i for i, line in enumerate(lines) if "agent_workspace_change_weights" in line]
    existing_key_lines = [
        i for i, line in enumerate(lines) if "agent_idle_activity_evidence_ttl_seconds" in line
    ]
    assert new_key_lines, "new key not found in template"
    assert existing_key_lines, "existing key not found in template"
    # The new key must be within 3 lines of the existing key.
    assert (
        min(abs(new - existing) for new in new_key_lines for existing in existing_key_lines) <= 3
    ), (
        f"new key is too far from existing key:"
        f" new_key_lines={new_key_lines},"
        f" existing_key_lines={existing_key_lines}"
    )


def test_local_template_does_not_contain_new_key() -> None:
    """The project-local ``ralph-workflow-local.toml`` template is
    intentionally NOT modified in this iteration. The plan says
    the local template does not currently carry the
    ``agent_idle_activity_evidence_ttl_seconds`` key and is
    not updated for the new tunable either.
    """
    if not LOCAL_TEMPLATE_PATH.exists():
        # If the local template does not exist, the test trivially
        # passes (the absence of the new key is implied).
        return
    text = LOCAL_TEMPLATE_PATH.read_text()
    assert "agent_workspace_change_weights" not in text, (
        "ralph-workflow-local.toml must NOT contain the new key"
        " (only the user-global template is updated in this iteration)"
    )


def test_policy_template_new_key_comment_mentions_all_kinds() -> None:
    """The new key's comment block mentions all 5
    ``WorkspaceChangeKind`` values so operators reading the
    template understand the policy."""
    text = _read_template()
    # Find the line containing the new key.
    for line in text.splitlines():
        if "agent_workspace_change_weights" in line and line.lstrip().startswith("#"):
            # The comment line for the new key must mention all 5 kinds.
            for kind in ("source", "log", "cache", "artifact", "other"):
                assert kind in line, (
                    f"new key comment line must mention WorkspaceChangeKind {kind!r}, got: {line!r}"
                )

            return
    raise AssertionError(
        "new key comment line not found in template; the new key"
        " must be commented out with a docstring that lists all 5 kinds"
    )
