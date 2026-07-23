"""Configured non-main auto-integration target contracts."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


def _config(target: str | None) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": target,
            }
        }
    )


def _target_stubs(
    origin_head: str | None,
    existing: set[str],
    lookups: list[str],
) -> tuple[Callable[[Path], str | None], Callable[[Path, str], bool]]:
    def resolve_origin(_root: Path) -> str | None:
        return origin_head

    def branch_exists(_root: Path, branch: str) -> bool:
        lookups.append(branch)
        return branch in existing

    return resolve_origin, branch_exists


def test_configured_non_main_target_is_honored_verbatim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each configured existing branch wins without consulting origin or main."""
    targets = ["develop", "unstable", "operator-named-integration"]

    def branch_exists(_root: Path, branch: str) -> bool:
        return branch in {*targets, "main"}

    monkeypatch.setattr(auto_integrate, "branch_exists", branch_exists)

    for target in targets:
        assert (
            auto_integrate.resolve_integration_target(
                _config(target),
                Path("/repo"),
            )
            == target
        )


def test_missing_configured_target_does_not_fall_back_to_main(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A typo is surfaced as no target instead of silently landing elsewhere."""
    lookups: list[str] = []

    def branch_exists(_root: Path, branch: str) -> bool:
        lookups.append(branch)
        return branch == "main"

    monkeypatch.setattr(auto_integrate, "branch_exists", branch_exists)

    assert (
        auto_integrate.resolve_integration_target(
            _config("missing-release"),
            Path("/repo"),
        )
        is None
    )
    assert lookups == ["missing-release"]


def test_unconfigured_target_uses_origin_then_standard_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Origin HEAD only participates when the operator configured no target."""
    cases = [
        ("trunk", {"trunk", "main"}, "trunk", ["trunk"]),
        ("main", {"develop", "main"}, "main", ["main"]),
        (None, {"master"}, "master", ["main", "master"]),
    ]

    for origin_head, existing, expected, lookups in cases:
        actual_lookups: list[str] = []
        resolve_origin, branch_exists = _target_stubs(
            origin_head,
            existing,
            actual_lookups,
        )

        monkeypatch.setattr(
            auto_integrate,
            "resolve_origin_head_branch",
            resolve_origin,
        )
        monkeypatch.setattr(auto_integrate, "branch_exists", branch_exists)

        assert (
            auto_integrate.resolve_integration_target(
                _config(None),
                Path("/repo"),
            )
            == expected
        )
        assert actual_lookups == list(lookups)
