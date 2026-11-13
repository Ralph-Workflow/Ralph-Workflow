from __future__ import annotations

import os
from pathlib import Path

from tests._real_config_guard_support import (
    _clear_recorded_write_violations,
    _install_config_guard_audit_hook,
)


def test_config_isolation_guard_reports_a_planted_synthetic_leak(
    tmp_path: Path,
    monkeypatch,
) -> None:
    guarded = tmp_path / "simulated-real-config"
    guarded.mkdir()
    monkeypatch.setenv("RALPH_TEST_GUARDED_CONFIG_DIRS", str(guarded))
    _install_config_guard_audit_hook()
    _clear_recorded_write_violations()

    leaked = guarded / "ralph-workflow-agents.toml"
    leaked.write_text("planted = true\n", encoding="utf-8")
    violations = _clear_recorded_write_violations()

    assert violations
    assert any(path == leaked.resolve() for _node, path in violations)
    assert all(str(path).startswith(str(tmp_path)) for _node, path in violations)
    os.environ.pop("RALPH_TEST_GUARDED_CONFIG_DIRS", None)
