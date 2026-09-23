"""Public lifecycle facade for MCP runtime executor singletons."""

from ralph.mcp.server import _saturated_dispatch
from ralph.mcp.websearch import _bounded_sdk_call


def shutdown_runtime_executors() -> None:
    """Release executor ownership without waiting for wedged running calls."""
    _saturated_dispatch.shutdown(wait=False)
    _bounded_sdk_call.shutdown(wait=False)


__all__ = [
    "shutdown_runtime_executors",
]
