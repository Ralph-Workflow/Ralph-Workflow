"""Configured non-main auto-integration target contracts."""

from __future__ import annotations

from pathlib import Path

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate, auto_integrate_catchup


def _config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
            }
        }
    )


def test_configured_non_main_local_target_is_honored_verbatim(monkeypatch) -> None:
    """A configured local target is authoritative without fallback detection."""
    monkeypatch.setattr(
        auto_integrate_catchup,
        "observe_branch_sha",
        lambda _root, _target: ("target-sha", True),
    )
    for target in ("develop", "unstable", "operator-named-integration"):
        assert auto_integrate.resolve_integration_target(_config(target), Path("/repo")) == target
