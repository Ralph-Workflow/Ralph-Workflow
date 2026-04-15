from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

from ralph.mcp.transport import StdioTransport


def test_stdio_transport_uses_injected_process_and_thread_factories() -> None:
    started: list[str] = []
    created: dict[str, object] = {}

    process = SimpleNamespace(
        stdin=BytesIO(),
        stdout=BytesIO(),
        stderr=BytesIO(),
        terminate=lambda: None,
        wait=lambda timeout=None: 0,
        kill=lambda: None,
    )

    def fake_process_factory(command: list[str], cwd: str | None = None):
        created["command"] = command
        created["cwd"] = cwd
        return process

    def fake_thread_factory(target, daemon: bool):
        name = target.__name__
        started.append(name)
        return SimpleNamespace(start=lambda: started.append(f"started:{name}"))

    transport = StdioTransport(
        ["python", "-m", "demo"],
        cwd="/tmp/demo",
        process_factory=fake_process_factory,
        thread_factory=fake_thread_factory,
    )

    transport.start()

    assert created["command"] == ["python", "-m", "demo"]
    assert created["cwd"] == "/tmp/demo"
    assert started == ["_read_loop", "_write_loop", "started:_read_loop", "started:_write_loop"]
