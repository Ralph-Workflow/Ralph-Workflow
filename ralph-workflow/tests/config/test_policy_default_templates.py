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

from ralph.config.general_config import GeneralConfig

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


_OBSOLETE_CEILING_WORDING = "ceiling for one conflict-resolution agent invocation"


def test_resolve_timeout_semantics_agree_across_every_operator_source() -> None:
    """Every source that states resolve-timeout semantics must say SHARED.

    Regression: the Sphinx ``configuration.md`` row was corrected to say
    one ceiling is shared across all rebase stops, rounds and sequential
    candidate invocations -- which is what
    :func:`ralph.pipeline.conflict_resolution.driver.resolution_deadline`
    actually implements -- but the two OTHER operator-facing sources for
    the same key still described a per-invocation ceiling.

    That mattered because ``configuration.md`` explicitly points readers
    at this template as "the canonical defaults and inline ``# comment``
    lines documenting the semantics of each key", and the Pydantic
    ``description`` is the machine-readable schema an operator's tooling
    reads. A fix applied to only one of the three leaves an operator one
    hop away from the wording that was wrong, so this pins all of them
    together rather than any single file's prose.
    """
    template_line = next(
        (
            line
            for line in _read_template().splitlines()
            if "auto_integrate_resolve_timeout_seconds" in line
        ),
        None,
    )
    assert template_line is not None, (
        "ralph-workflow.toml must document auto_integrate_resolve_timeout_seconds"
    )
    schema_description = GeneralConfig.model_fields[
        "auto_integrate_resolve_timeout_seconds"
    ].description
    assert schema_description is not None, (
        "the auto_integrate_resolve_timeout_seconds field must carry a description"
    )
    for source_name, text in (
        ("ralph-workflow.toml", template_line),
        ("GeneralConfig field description", schema_description),
    ):
        lowered = text.lower()
        assert _OBSOLETE_CEILING_WORDING not in lowered, (
            f"{source_name} still documents a per-invocation ceiling, which "
            f"contradicts driver.resolution_deadline, got: {text!r}"
        )
        assert "shared" in lowered, (
            f"{source_name} must say the ceiling is shared, got: {text!r}"
        )
        for token in ("stop", "round", "invocation"):
            assert token in lowered, (
                f"{source_name} must name what shares the ceiling ({token!r}), "
                f"got: {text!r}"
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
