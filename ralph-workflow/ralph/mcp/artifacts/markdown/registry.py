
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._spec import MdArtifactSpec

_SPECS: dict[str, MdArtifactSpec] = {}


def register_spec(spec: MdArtifactSpec) -> None:
    if spec.artifact_type in _SPECS:
        raise ValueError(f"markdown spec already registered for {spec.artifact_type!r}")
    _SPECS[spec.artifact_type] = spec


def get_spec(artifact_type: str) -> MdArtifactSpec:
    try:
        return _SPECS[artifact_type]
    except KeyError as exc:
        raise ValueError(f"no markdown spec registered for {artifact_type!r}") from exc


def registered_specs() -> tuple[MdArtifactSpec, ...]:
    return tuple(_SPECS.values())


__all__ = ["get_spec", "register_spec", "registered_specs"]
