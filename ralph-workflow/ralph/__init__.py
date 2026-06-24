"""Top-level package for Ralph Workflow.

The public Python package is intentionally small at the root: it exposes version
metadata and points users toward the major subpackages that make up the system.

Useful pydoc entry points:

- ``ralph.cli`` for the Typer CLI application
- ``ralph.config`` for configuration models and loading
- ``ralph.pipeline`` for orchestration state and reducer/orchestrator logic
- ``ralph.phases`` for phase dispatch
- ``ralph.session_runtime`` for host-owned mini-pipeline / standalone session runtime helpers
- ``ralph.mcp`` for the MCP bridge and standalone server helpers
- ``ralph.git`` for GitPython-backed repository operations
- ``ralph.workspace`` for filesystem abstractions used by production code and tests
"""

__version__ = "0.8.16"
version = __version__
__all__ = ["__version__", "version"]
