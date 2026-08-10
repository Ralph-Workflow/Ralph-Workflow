"""Discoverability contract for auto-integration configuration documentation."""

from __future__ import annotations

from tests.doc_roots import PACKAGE_DOCS_SPHINX_DIR

_PATH = PACKAGE_DOCS_SPHINX_DIR / "configuration.md"
_CONTENT = _PATH.read_text()
_LIVE_KEYS = (
    "auto_integrate_enabled",
    "auto_integrate_target",
    "auto_integrate_remote_enabled",
    "auto_integrate_remote",
    "auto_integrate_remote_interval_seconds",
    "auto_integrate_reclaim_target_worktree",
)
_RETIRED_KEYS = (
    "auto_integrate_fetch_enabled",
    "auto_integrate_push_enabled",
    "auto_integrate_remote_sync_enabled",
    "auto_integrate_remote_target",
    "auto_integrate_fetch_timeout_seconds",
    "auto_integrate_push_timeout_seconds",
    "auto_integrate_resolve_timeout_seconds",
    "auto_integrate_remote_sync_interval_seconds",
    "auto_integrate_remote_backoff_max_seconds",
    "auto_integrate_remote_wait_seconds",
)


def _general_rows(content: str) -> list[str]:
    """Return auto-integration rows from the ``[general]`` settings table."""
    return [line for line in content.splitlines() if line.startswith("| `auto_integrate_")]


def test_configuration_md_documents_exactly_the_six_live_auto_integrate_keys_in_order() -> None:
    """S-2: the operator reference matches the six-key configuration surface."""
    rows = _general_rows(_CONTENT)
    assert [row.split("`")[1] for row in rows] == list(_LIVE_KEYS)


def test_configuration_md_documents_defaults_and_opt_out_contract() -> None:
    """S-8: operators can discover defaults and the local/remote safety split."""
    content = _CONTENT
    rows = _general_rows(content)
    assert "`true`" in rows[0]
    assert '`"main"`' in rows[1]
    assert "when the remote exists" in rows[2]
    assert '`"origin"`' in rows[3]
    assert "`0.0`" in rows[4]
    assert "`true`" in rows[5]
    section = (
        content.partition("## Auto-integration")[2]
        .partition("## Agent chains and drains")[0]
        .lower()
    )
    for token in ("five seams", "force-push", "snapshot", "local-only"):
        assert token in section


def test_configuration_md_omits_retired_auto_integrate_keys() -> None:
    """S-8: removed configuration names do not remain discoverable in docs."""
    content = _CONTENT
    assert not any(key in content for key in _RETIRED_KEYS)
