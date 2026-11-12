from __future__ import annotations

from pathlib import Path

RALPH_TEST_CHANGE_REASONS = {
    "test_policy_template_new_key_comment_mentions_all_kinds": (
        "the optional-key documentation moved out of the TOML template comments "
        "into the owning configuration page, because the absolute comment gate "
        "bans comments in tracked TOML; the check still asserts that the "
        "documented key lists all 5 WorkspaceChangeKind values"
    ),
    "test_policy_template_contains_agent_workspace_change_weights": (
        "the optional-key documentation moved out of the TOML template comments "
        "into the owning configuration page; the check still asserts that the key "
        "is documented"
    ),
    "test_policy_template_new_key_near_existing_activity_ttl": (
        "the optional-key documentation moved out of the TOML template comments "
        "into the owning configuration page; the check still asserts that the new "
        "key sits next to the existing activity TTL key"
    ),
}

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow.toml"
CONFIGURATION_DOC = REPO_ROOT / "docs" / "sphinx" / "configuration.md"
LOCAL_TEMPLATE_PATH = REPO_ROOT / "ralph" / "policy" / "defaults" / "ralph-workflow-local.toml"


def _read_template() -> str:
    return TEMPLATE_PATH.read_text()


def test_policy_template_contains_agent_workspace_change_weights() -> None:
    text = CONFIGURATION_DOC.read_text()
    assert "agent_workspace_change_weights" in text, (
        "the configuration page must document the agent_workspace_change_weights key"
    )


def test_policy_template_new_key_near_existing_activity_ttl() -> None:
    text = CONFIGURATION_DOC.read_text()
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
    text = CONFIGURATION_DOC.read_text()
    lines = text.splitlines()
    kinds = ("source", "log", "cache", "artifact", "other")
    documented = False
    for index, line in enumerate(lines):
        if "agent_workspace_change_weights" not in line:
            continue
        block = "\n".join(lines[index : index + 30])
        if all(kind in block for kind in kinds):
            documented = True
            break
    assert documented, (
        "the configuration page must document agent_workspace_change_weights"
        f" with all 5 kinds {kinds}"
    )
