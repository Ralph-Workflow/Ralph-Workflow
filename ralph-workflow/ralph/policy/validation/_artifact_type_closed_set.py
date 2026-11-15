from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts._artifact_types import KNOWN_ARTIFACT_TYPES
from ralph.policy.validation._policy_validation_error import PolicyValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.policy.models import ArtifactsPolicy


def supported_artifact_types() -> frozenset[str]:
    return KNOWN_ARTIFACT_TYPES


def _default_type_for_drain(
    default_artifacts_policy: ArtifactsPolicy | None,
    drain: str,
) -> str | None:
    if default_artifacts_policy is None:
        return None
    for contract in default_artifacts_policy.artifacts.values():
        if contract.drain == drain:
            return contract.artifact_type
    return None


def validate_artifact_types_supported(
    artifacts_policy: ArtifactsPolicy,
    *,
    default_artifacts_policy: ArtifactsPolicy | None = None,
    phases_by_drain: Mapping[str, str] | None = None,
) -> None:
    supported = supported_artifact_types()
    for name, contract in sorted(artifacts_policy.artifacts.items()):
        if contract.artifact_type in supported:
            continue
        phase = phases_by_drain.get(contract.drain) if phases_by_drain else None
        expected = _default_type_for_drain(default_artifacts_policy, contract.drain)
        expected_text = f"'{expected}'" if expected else "no default contract for that drain"
        subject = f"Phase '{phase}'" if phase else f"Drain '{contract.drain}'"
        raise PolicyValidationError(
            f"{subject} is graded on unsupported artifact type '{contract.artifact_type}' "
            f"by artifacts contract '{name}' (drain '{contract.drain}'). The expected artifact "
            f"type for that drain is {expected_text}. Supported artifact types: "
            f"{sorted(supported)}. A phase is graded on exactly one supported artifact type, so "
            f"a stale contract would mis-grade the phase instead of failing loudly."
        )


__all__ = ["supported_artifact_types", "validate_artifact_types_supported"]
