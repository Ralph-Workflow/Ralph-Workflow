"""``resolve_smoke_harness_spec`` must resolve any Claude alias, not just ``claude/haiku``.

The module is deliberately NOT named after the harness it covers: ``audit_test_policy``
forces any test file whose name carries that word to be excluded from every suite,
which would make this regression guard unrunnable in ``make verify``.

``resolve_smoke_harness_spec`` matched interactive Claude by exact string
against the hardcoded ``claude/haiku``, and headless Claude only by the
``claude-headless/`` prefix. Every other transport accepts both the bare alias
and ``<alias>/<model>``. That asymmetry meant a Claude smoke driven from the
operator's own ``[agent_chains]`` -- which is what the smoke defaults now
resolve to -- raised ``ValueError`` instead of running.
"""

from __future__ import annotations

import pytest

from ralph.pipeline.plumbing.smoke_plumbing import resolve_smoke_harness_spec


@pytest.mark.timeout_seconds(3)
def test_smoke_harness_regression_interactive_claude_resolves_for_any_model() -> None:
    """A non-haiku interactive alias resolves, with a run id that cannot collide."""
    spec = resolve_smoke_harness_spec("claude/sonnet")

    assert spec.relative_dir == resolve_smoke_harness_spec("claude/haiku").relative_dir
    assert spec.run_id != resolve_smoke_harness_spec("claude/haiku").run_id


@pytest.mark.timeout_seconds(3)
def test_smoke_harness_regression_bare_claude_aliases_resolve() -> None:
    """The bare aliases the defaults fall back to must resolve, as they do elsewhere."""
    assert resolve_smoke_harness_spec("claude").run_id == "interactive-claude-smoke"
    assert resolve_smoke_harness_spec("claude-headless").run_id == "headless-claude-smoke"


@pytest.mark.timeout_seconds(3)
def test_smoke_harness_legacy_claude_haiku_layout_is_unchanged() -> None:
    """The legacy alias keeps its on-disk layout so existing artifacts are not orphaned."""
    spec = resolve_smoke_harness_spec("claude/haiku")

    assert spec.run_id == "interactive-claude-smoke"
    assert spec.relative_dir.as_posix() == "tmp/interactive-claude-smoke"
