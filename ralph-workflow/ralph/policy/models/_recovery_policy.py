"""RecoveryPolicy Pydantic model."""

from __future__ import annotations

from typing import cast

from pydantic import Field, model_validator

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel


class RecoveryPolicy(_FrozenPolicyModel):
    """Pipeline-wide recovery policy."""

    cycle_cap: int = Field(default=200, ge=1)
    failed_route: str = Field(
        default="failed_terminal",
        description=(
            "Phase to route to on terminal pipeline failure. "
            "Must reference a declared phase with role='terminal' and terminal_outcome='failure'. "
            "'phase_failed', 'exit_failure', and 'failed' are no longer accepted. "
            "Example: declare [phases.failed_terminal] and set failed_route='failed_terminal'."
        ),
    )
    terminal_failure_phase: str | None = Field(
        default=None,
        description=(
            "Optional name of the declared terminal failure phase "
            "(must have role='terminal' and terminal_outcome='failure' in pipeline.phases). "
            "When set, failure routing references this policy-declared phase."
        ),
    )
    preserve_session_on_categories: tuple[str, ...] = ("agent",)

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_route_fields(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        d = cast("dict[str, object]", dict(data))
        if "terminal_recovery_route" in d:
            raise ValueError(
                "recovery.terminal_recovery_route is deprecated; rename it to "
                "recovery.failed_route. See docs/sphinx/concepts.md."
            )
        failed_route = d.get("failed_route")
        if failed_route in ("phase_failed", "exit_failure"):
            raise ValueError(
                f"recovery.failed_route: '{failed_route}' is no longer supported. "
                "Declare a terminal failure phase with role='terminal' and "
                "terminal_outcome='failure' and reference it via recovery.failed_route "
                "(and optionally recovery.terminal_failure_phase). "
                "See docs/sphinx/policy-driven-overhaul-migration.md."
            )
        if failed_route == "failed":
            raise ValueError(
                "recovery.failed_route: 'failed' is no longer accepted as a pseudo-phase alias. "
                "Declare a phase with role='terminal' and terminal_outcome='failure' "
                "and reference it via recovery.failed_route. "
                "Example: add [phases.failed_terminal] with role='terminal' and "
                "terminal_outcome='failure', then set failed_route='failed_terminal'. "
                "See docs/sphinx/policy-driven-overhaul-migration.md."
            )
        return d
