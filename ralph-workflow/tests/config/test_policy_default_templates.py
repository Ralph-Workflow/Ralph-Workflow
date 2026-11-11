from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow.toml"
LOCAL_TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow-local.toml"


def _read_template() -> str:
    return TEMPLATE_PATH.read_text()


def test_policy_template_contains_agent_workspace_change_weights() -> None:
    text = _read_template()
    assert "agent_workspace_change_weights" in text, (
        f"ralph-workflow.toml must document the new"
        f" agent_workspace_change_weights key, got:\n{text}"
    )


def test_policy_template_new_key_near_existing_activity_ttl() -> None:
    text = _read_template()
    lines = text.splitlines()
    new_key_lines = [i for i, line in enumerate(lines) if "agent_workspace_change_weights" in line]
    existing_key_lines = [
        i for i, line in enumerate(lines) if "agent_idle_activity_evidence_ttl_seconds" in line
    ]
    assert new_key_lines, "new key not found in template"
    assert existing_key_lines, "existing key not found in template"
    assert (
        min(abs(new - existing) for new in new_key_lines for existing in existing_key_lines) <= 3
    ), (
        f"new key is too far from existing key:"
        f" new_key_lines={new_key_lines},"
        f" existing_key_lines={existing_key_lines}"
    )


def test_local_template_does_not_contain_new_key() -> None:
    if not LOCAL_TEMPLATE_PATH.exists():
        return
    text = LOCAL_TEMPLATE_PATH.read_text()
    assert "agent_workspace_change_weights" not in text, (
        "ralph-workflow-local.toml must NOT contain the new key"
        " (only the user-global template is updated in this iteration)"
    )


def test_policy_template_new_key_comment_mentions_all_kinds() -> None:
    text = _read_template()
    for line in text.splitlines():
        if "agent_workspace_change_weights" in line and line.lstrip().startswith("#"):
            for kind in ("source", "log", "cache", "artifact", "other"):
                assert kind in line, (
                    f"new key comment line must mention WorkspaceChangeKind {kind!r}, got: {line!r}"
                )

            return
    raise AssertionError(
        "new key comment line not found in template; the new key"
        " must be commented out with a docstring that lists all 5 kinds"
    )
