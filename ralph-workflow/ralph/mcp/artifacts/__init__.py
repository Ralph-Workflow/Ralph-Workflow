from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.mcp.artifacts._artifact_error import ArtifactError
    from ralph.mcp.artifacts._artifact_persistence import (
        DEFAULT_ARTIFACT_PERSISTENCE,
        ArtifactPersistence,
    )
    from ralph.mcp.artifacts.canonical_submit import (
        SubmitResult,
        promote_fallback_artifact,
        submit_artifact_canonical,
    )
    from ralph.mcp.artifacts.citation_verifier import verify_reject_citations

__all__ = [
    "DEFAULT_ARTIFACT_PERSISTENCE",
    "ArtifactError",
    "ArtifactPersistence",
    "SubmitResult",
    "promote_fallback_artifact",
    "submit_artifact_canonical",
    "verify_reject_citations",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "DEFAULT_ARTIFACT_PERSISTENCE": (
        "ralph.mcp.artifacts._artifact_persistence",
        "DEFAULT_ARTIFACT_PERSISTENCE",
    ),
    "ArtifactError": ("ralph.mcp.artifacts._artifact_error", "ArtifactError"),
    "ArtifactPersistence": ("ralph.mcp.artifacts._artifact_persistence", "ArtifactPersistence"),
    "SubmitResult": ("ralph.mcp.artifacts.canonical_submit", "SubmitResult"),
    "promote_fallback_artifact": (
        "ralph.mcp.artifacts.canonical_submit",
        "promote_fallback_artifact",
    ),
    "submit_artifact_canonical": (
        "ralph.mcp.artifacts.canonical_submit",
        "submit_artifact_canonical",
    ),
    "verify_reject_citations": ("ralph.mcp.artifacts.citation_verifier", "verify_reject_citations"),
}


def __getattr__(name: str) -> object:
    export = _LAZY_EXPORTS.get(name)
    if export is not None:
        defining_module, attr_name = export
        module = import_module(defining_module)
        value = cast("dict[str, object]", module.__dict__)[attr_name]
        cast("dict[str, object]", globals())[name] = value
        return value

    submodule = f"{__name__}.{name}"
    try:
        return import_module(submodule)
    except ModuleNotFoundError as exc:
        if exc.name != submodule:
            raise
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
