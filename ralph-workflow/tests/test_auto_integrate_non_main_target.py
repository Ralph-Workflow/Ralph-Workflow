"""Configured non-main auto-integration target contracts."""

from __future__ import annotations

from pathlib import Path

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate


def _config(target: str) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
            }
        }
    )


def test_configured_non_main_target_is_honored_verbatim() -> None:
    """The configured target is authoritative without branch detection."""
    for target in ("develop", "unstable", "operator-named-integration"):
        assert (
            auto_integrate.resolve_integration_target(
                _config(target),
                Path("/repo"),
            )
            == target
        )
