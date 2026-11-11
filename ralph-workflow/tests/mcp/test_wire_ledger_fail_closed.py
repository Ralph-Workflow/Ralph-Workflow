from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

import pytest

from ralph.mcp.server._wire_ledger import (
    WireLedgerIOError,
    append_wire_record,
)


class TestWireLedgerFailClosed:
    def test_append_raises_wire_ledger_io_error_on_oserror(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:

        def fake_open_for_append(*args: object, **kwargs: object) -> object:
            raise OSError(13, "Permission denied")

        original_open = Path.open

        def raising_open(self: Path, mode: str = "r", *a: object, **kw: object) -> object:
            if "a" in mode:
                raise OSError(13, "Permission denied")
            return original_open(self, mode, *a, **kw)

        monkeypatch.setattr(Path, "open", raising_open)

        with pytest.raises(WireLedgerIOError) as exc_info:
            append_wire_record(
                workspace_root=tmp_path,
                method="tools/call",
                tool_name="test_tool",
                params={"arg": "value"},
                run_id="test-run-123",
                secret="test-secret-123",
            )

        assert "wire_ledger_io_error" in str(exc_info.value)

    def test_append_stays_fail_open_for_test_fake_attribute_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:

        result = append_wire_record(
            workspace_root=tmp_path,
            method="tools/call",
            tool_name="test_tool",
            params={"arg": "value"},
            run_id="test-run-failopen",
            secret="test-secret-456",
        )

        assert result is not None
        assert result.method == "tools/call"
        assert result.tool_name == "test_tool"

    def test_wire_ledger_io_error_message_contains_marker(self, tmp_path: Path) -> None:

        exc = WireLedgerIOError("wire_ledger_io_error: test error")
        assert "wire_ledger_io_error" in str(exc)

        assert issubclass(WireLedgerIOError, Exception)
