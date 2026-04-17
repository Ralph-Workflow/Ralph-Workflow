import pytest
from collections.abc import Callable
from dataclasses import fields, is_dataclass
from ralph.mcp.server.factory import McpServerFactory, McpServerHandle


class FakeMcpServerFactory:
    def build(self, session: object) -> McpServerHandle:
        return McpServerHandle(
            endpoint="http://127.0.0.1:12345",
            pid=99999,
            shutdown=lambda: None,
        )


def test_mcp_server_handle_is_dataclass() -> None:
    assert is_dataclass(McpServerHandle)


def test_mcp_server_handle_frozen() -> None:
    handle = McpServerHandle(endpoint="http://127.0.0.1:0", pid=1, shutdown=lambda: None)
    with pytest.raises((AttributeError, TypeError)):
        handle.pid = 2  # type: ignore[misc]


def test_mcp_server_handle_fields() -> None:
    field_names = {f.name for f in fields(McpServerHandle)}
    assert "endpoint" in field_names
    assert "pid" in field_names
    assert "shutdown" in field_names


def test_mcp_server_handle_endpoint_is_str() -> None:
    handle = McpServerHandle(endpoint="http://127.0.0.1:0", pid=1, shutdown=lambda: None)
    assert isinstance(handle.endpoint, str)


def test_mcp_server_handle_pid_is_int() -> None:
    handle = McpServerHandle(endpoint="http://127.0.0.1:0", pid=42, shutdown=lambda: None)
    assert isinstance(handle.pid, int)


def test_mcp_server_handle_shutdown_callable() -> None:
    called: list[bool] = []
    handle = McpServerHandle(
        endpoint="http://127.0.0.1:0", pid=1, shutdown=lambda: called.append(True)
    )
    handle.shutdown()
    assert called == [True]


def test_protocol_runtime_checkable() -> None:
    fake = FakeMcpServerFactory()
    assert isinstance(fake, McpServerFactory)


def test_factory_importable() -> None:
    from ralph.mcp.server.factory import McpServerFactory, McpServerHandle  # noqa: F401

    assert McpServerFactory is not None
    assert McpServerHandle is not None
