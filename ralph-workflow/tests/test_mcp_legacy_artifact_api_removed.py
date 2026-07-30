"""Regression coverage for removing the retired JSON artifact API."""

from __future__ import annotations

from importlib.util import find_spec

from ralph import mcp
from ralph.mcp import artifacts

_RETIRED_PUBLIC_NAMES = frozenset(
    {
        "Artifact",
        "ArtifactExistsError",
        "ArtifactNotFoundError",
        "ArtifactSubmitOptions",
        "ArtifactUpdateOptions",
        "BridgeConfig",
        "BridgeError",
        "MCPBridge",
        "delete_artifact",
        "get_artifact",
        "list_artifacts",
        "submit_artifact",
        "update_artifact",
    }
)

_RETIRED_MODULES = frozenset(
    {
        "ralph.mcp.artifacts._artifact_exists_error",
        "ralph.mcp.artifacts._artifact_not_found_error",
        "ralph.mcp.artifacts._artifact_submit_options",
        "ralph.mcp.artifacts._artifact_update_options",
        "ralph.mcp.artifacts._bridge_artifact_deps",
        "ralph.mcp.artifacts._bridge_config",
        "ralph.mcp.artifacts._bridge_error",
        "ralph.mcp.artifacts._mcp_tool",
        "ralph.mcp.artifacts.bridge",
        "ralph.mcp.artifacts.store",
    }
)


def test_mcp_package_does_not_export_retired_json_artifact_api() -> None:
    assert _RETIRED_PUBLIC_NAMES.isdisjoint(mcp.__all__)
    assert all(not hasattr(mcp, name) for name in _RETIRED_PUBLIC_NAMES)


def test_artifacts_package_does_not_export_retired_json_artifact_api() -> None:
    assert _RETIRED_PUBLIC_NAMES.isdisjoint(artifacts.__all__)
    assert all(not hasattr(artifacts, name) for name in _RETIRED_PUBLIC_NAMES)


def test_retired_json_artifact_modules_are_removed() -> None:
    assert all(find_spec(module_name) is None for module_name in _RETIRED_MODULES)
