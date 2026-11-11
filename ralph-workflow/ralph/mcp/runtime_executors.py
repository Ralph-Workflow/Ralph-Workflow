
from ralph.mcp.server import _saturated_dispatch
from ralph.mcp.websearch import _bounded_sdk_call


def shutdown_runtime_executors() -> None:
    _saturated_dispatch.shutdown(wait=False)
    _bounded_sdk_call.shutdown(wait=False)


__all__ = [
    "shutdown_runtime_executors",
]
