"""Explicit registry shared by markdown submission and check-only callers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._spec import MdArtifactSpec

_SPECS: dict[str, MdArtifactSpec] = {}  # bounded-accumulator-ok: closed startup registry


def register_spec(spec: MdArtifactSpec) -> None:
    """Register one artifact spec, rejecting accidental replacement."""
    if spec.artifact_type in _SPECS:
        raise ValueError(f"markdown spec already registered for {spec.artifact_type!r}")
    _SPECS[spec.artifact_type] = spec


def get_spec(artifact_type: str) -> MdArtifactSpec:
    """Return the registered spec or a clear unsupported-type error."""
    try:
        return _SPECS[artifact_type]
    except KeyError as exc:
        raise ValueError(f"no markdown spec registered for {artifact_type!r}") from exc


def registered_specs() -> tuple[MdArtifactSpec, ...]:
    """Expose registered specs without a mutable registry view."""
    return tuple(_SPECS.values())


__all__ = ["get_spec", "register_spec", "registered_specs"]
