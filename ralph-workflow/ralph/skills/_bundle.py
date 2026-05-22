"""Baseline skill bundle installation specifications."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillInstallSpec:
    """A skill installable via `claude plugin install <plugin_id>`."""

    plugin_id: str
    display_name: str


BASELINE_SKILL_BUNDLE: tuple[SkillInstallSpec, ...] = (
    SkillInstallSpec(
        plugin_id="obra/superpowers",
        display_name="Superpowers skill bundle",
    ),
    SkillInstallSpec(
        plugin_id="affaan-m/everything-claude-code",
        display_name="ECC security/verification/coding-standards skills",
    ),
)


__all__ = ["BASELINE_SKILL_BUNDLE", "SkillInstallSpec"]
