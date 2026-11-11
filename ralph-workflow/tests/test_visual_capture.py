from __future__ import annotations

import importlib
import inspect
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.executor._process_result import ProcessResult
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.server._wire_ledger import (
    WIRE_LEDGER_RELPATH,
    _ledger_path,
    verify_chain,
)
from ralph.mcp.tools.workspace._media_capture import (
    DEFAULT_PER_CELL_TIMEOUT_SECONDS,
    MediaCaptureError,
    handle_media_capture,
    register_media_capture_result,
)
from ralph.testing import audit_repo_structure
from ralph.visual.capture_request import CaptureRequest
from ralph.visual.policy_facts import (
    DEFAULT_THEMES,
    REQUIRED_STATES,
    Viewport,
)

if TYPE_CHECKING:
    from ralph.executor._process_run_options import ProcessRunOptions


_PNG_SIGNATURE: bytes = b"\x89PNG\r\n\x1a\n"


def test_visual_capture_regression_has_one_public_top_level_class() -> None:
    media_capture_module = importlib.import_module("ralph.mcp.tools.workspace._media_capture")
    source = inspect.getsource(media_capture_module)
    public_classes, _, _ = audit_repo_structure._scan_structure(source, tuple(source.splitlines()))

    assert public_classes == ("MediaCaptureError",)


def test_policy_facts_has_one_public_top_level_class() -> None:
    from ralph.visual import policy_facts

    source = inspect.getsource(policy_facts)
    public_classes, _, _ = audit_repo_structure._scan_structure(source, tuple(source.splitlines()))

    assert public_classes == ("Viewport",)


def _build_minimal_png(width: int, height: int) -> bytes:
    return (
        _PNG_SIGNATURE
        + b"\x00\x00\x00\x0d"
        + b"IHDR"
        + width.to_bytes(4, byteorder="big", signed=False)
        + height.to_bytes(4, byteorder="big", signed=False)
    )


class _FakeExecutor:
    def __init__(
        self,
        *,
        png_width: int = 320,
        png_height: int = 180,
        fail_on_cell_id: str | None = None,
        fail_message: str = "synthetic renderer failure",
        omit_writes: bool = False,
    ) -> None:
        self._png_width = png_width
        self._png_height = png_height
        self._fail_on_cell_id = fail_on_cell_id
        self._fail_message = fail_message
        self._omit_writes = omit_writes
        self.calls: list[tuple[str, tuple[str, ...], ProcessRunOptions | None]] = []

    def __call__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        options: ProcessRunOptions | None = None,
    ) -> ProcessResult:
        self.calls.append((command, tuple(args), options))
        if options is None or options.env is None:
            return ProcessResult(
                (command, *args),
                1,
                "",
                "fake executor: no env provided",
            )
        cell_id = options.env.get("RALPH_CAPTURE_CELL_ID", "")
        output_path = options.env.get("RALPH_CAPTURE_OUTPUT", "")
        if self._fail_on_cell_id is not None and cell_id == self._fail_on_cell_id:
            return ProcessResult(
                (command, *args),
                1,
                "",
                self._fail_message,
            )
        if not self._omit_writes and output_path:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(_build_minimal_png(self._png_width, self._png_height))
        return ProcessResult((command, *args), 0, "", "")


def _read_ledger_rows(
    workspace_root: Path,
    secret: str | None = None,
) -> list[dict[str, object]]:
    path = (
        _ledger_path(workspace_root, secret)
        if secret is not None
        else workspace_root / WIRE_LEDGER_RELPATH
    )
    if not path.exists():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parsed: object = json.loads(stripped)
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


_TEST_SECRET: str = "test-broker-secret"


def _build_request(*, target: str) -> CaptureRequest:
    viewports = (
        Viewport(name="narrow", width=375, height=812),
        Viewport(name="wide", width=1440, height=900),
    )
    themes: tuple[str, ...] = (DEFAULT_THEMES[0],)
    states: tuple[str, ...] = REQUIRED_STATES
    return CaptureRequest.build(
        target=target,
        viewports=viewports,
        themes=themes,
        states=states,
    )


def test_handle_media_capture_mints_one_handle_per_cell(tmp_path: Path) -> None:
    request = _build_request(target="checkout")
    fake = _FakeExecutor(png_width=400, png_height=240)
    result = handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --target={target} --viewport=narrow",
        secret=_TEST_SECRET,
        executor=fake,
    )

    assert result.target == "checkout"
    assert len(result.cells) == len(request.matrix)

    seen_ids: set[str] = set()
    for cell_result, expected_cell in zip(result.cells, request.matrix, strict=True):
        assert cell_result.artifact_id not in seen_ids
        seen_ids.add(cell_result.artifact_id)
        assert cell_result.uri == f"ralph://media/{cell_result.artifact_id}"
        assert cell_result.cell.cell_id == expected_cell.cell_id
        assert cell_result.width == 400
        assert cell_result.height == 240
        assert len(cell_result.sha256) == 64
        assert cell_result.size_bytes > 0
        assert not Path(cell_result.output_path).is_absolute()


def test_handle_media_capture_registers_every_minted_handle_for_replay(tmp_path: Path) -> None:
    request = _build_request(target="checkout")
    result = handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --target={target}",
        secret=_TEST_SECRET,
        executor=_FakeExecutor(),
    )
    manifest = MediaManifest()

    register_media_capture_result(manifest, workspace_root=tmp_path, result=result)

    entries = manifest.list_entries()
    assert [entry.uri for entry in entries] == [cell.uri for cell in result.cells]
    assert [entry.source_path for entry in entries] == [cell.output_path for cell in result.cells]
    assert [entry.load_bytes() for entry in entries] == [
        (tmp_path / cell.output_path).read_bytes() for cell in result.cells
    ]


def test_handle_media_capture_appends_ledger_records(tmp_path: Path) -> None:
    request = _build_request(target="dashboard")
    fake = _FakeExecutor()
    handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --target={target}",
        secret=_TEST_SECRET,
        executor=fake,
    )

    rows = _read_ledger_rows(tmp_path, _TEST_SECRET)
    assert len(rows) == len(request.matrix)
    assert verify_chain(tmp_path, _TEST_SECRET) is True

    cell_ids_in_ledger: set[str] = set()
    for row in rows:
        assert row.get("method") == "tools/call"
        assert row.get("tool_name") == "media_capture"
        assert row.get("run_id") == "run-1"
        assert isinstance(row.get("params_digest"), str)
        assert isinstance(row.get("hmac"), str)
        params_blob = row.get("params_digest")
        assert isinstance(params_blob, str)
        cell_ids_in_ledger.add(params_blob)
    assert len(cell_ids_in_ledger) == len(request.matrix)


def test_handle_media_capture_records_geometry_and_sha256(tmp_path: Path) -> None:
    request = _build_request(target="profile")
    fake = _FakeExecutor(png_width=640, png_height=480)
    result = handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --target={target}",
        secret=_TEST_SECRET,
        executor=fake,
    )
    by_cell_id: dict[str, object] = {
        cell_result.cell.cell_id: cell_result for cell_result in result.cells
    }
    rows = _read_ledger_rows(tmp_path, _TEST_SECRET)
    assert len(rows) == len(request.matrix)
    for row in rows:
        assert row.get("tool_name") == "media_capture"
    for cell_result in result.cells:
        assert cell_result.width == 640
        assert cell_result.height == 480
        assert len(cell_result.sha256) == 64
        assert cell_result.uri == f"ralph://media/{cell_result.artifact_id}"
    assert set(by_cell_id.keys()) == request.cell_ids


def test_handle_media_capture_substitutes_target_placeholder(tmp_path: Path) -> None:
    request = _build_request(target="landing")
    fake = _FakeExecutor()
    handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --target={target} --theme=light",
        secret=_TEST_SECRET,
        executor=fake,
    )
    assert fake.calls
    for command, args, _options in fake.calls:
        joined = " ".join((command, *args))
        assert "landing" in joined
        assert "{target}" not in joined


def test_handle_media_capture_appends_target_as_trailing_argv(tmp_path: Path) -> None:
    request = _build_request(target="settings")
    fake = _FakeExecutor()
    handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --theme=light",
        secret=_TEST_SECRET,
        executor=fake,
    )
    assert fake.calls
    for _command, args, _options in fake.calls:
        assert args[-1] == "settings"


def test_handle_media_capture_rejects_an_unbounded_declared_argv(tmp_path: Path) -> None:
    request = _build_request(target="settings")
    command = "bin/capture " + " ".join(f"--option-{index}" for index in range(33))

    with pytest.raises(MediaCaptureError, match="too many argv"):
        handle_media_capture(
            tmp_path,
            run_id="run-1",
            capture_request=request,
            design_capture_command=command,
            secret=_TEST_SECRET,
            executor=_FakeExecutor(),
        )


def test_handle_media_capture_invokes_one_process_per_cell(tmp_path: Path) -> None:
    request = _build_request(target="cart")
    fake = _FakeExecutor()
    handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --target={target}",
        secret=_TEST_SECRET,
        executor=fake,
    )
    assert len(fake.calls) == len(request.matrix)


def test_handle_media_capture_fails_when_renderer_exits_nonzero(tmp_path: Path) -> None:
    request = _build_request(target="orders")
    failing_cell_id = request.matrix[1].cell_id
    fake = _FakeExecutor(fail_on_cell_id=failing_cell_id)
    with pytest.raises(MediaCaptureError) as excinfo:
        handle_media_capture(
            tmp_path,
            run_id="run-1",
            capture_request=request,
            design_capture_command="bin/capture --target={target}",
            secret=_TEST_SECRET,
            executor=fake,
        )
    assert excinfo.value.cell_id == failing_cell_id
    assert excinfo.value.returncode == 1
    rows = _read_ledger_rows(tmp_path)
    assert rows == []


def test_handle_media_capture_fails_when_output_missing(tmp_path: Path) -> None:
    request = _build_request(target="cart")
    fake = _FakeExecutor(omit_writes=True)
    with pytest.raises(MediaCaptureError) as excinfo:
        handle_media_capture(
            tmp_path,
            run_id="run-1",
            capture_request=request,
            design_capture_command="bin/capture --target={target}",
            secret=_TEST_SECRET,
            executor=fake,
        )
    assert "did not write" in excinfo.value.reason
    assert _read_ledger_rows(tmp_path, _TEST_SECRET) == []


def test_handle_media_capture_fails_when_output_not_png(tmp_path: Path) -> None:
    request = _build_request(target="cart")
    _FakeExecutor()

    def _write_non_png(
        command: str,
        args: Sequence[str] = (),
        *,
        options: ProcessRunOptions | None = None,
    ) -> ProcessResult:
        if options is None or options.env is None:
            return ProcessResult((command, *args), 1, "", "no env")
        output = Path(options.env.get("RALPH_CAPTURE_OUTPUT", ""))
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"not a png, just text")
        return ProcessResult((command, *args), 0, "", "")

    with pytest.raises(MediaCaptureError) as excinfo:
        handle_media_capture(
            tmp_path,
            run_id="run-1",
            capture_request=request,
            design_capture_command="bin/capture --target={target}",
            secret=_TEST_SECRET,
            executor=_write_non_png,
        )
    assert "not a PNG" in excinfo.value.reason
    assert _read_ledger_rows(tmp_path) == []


def test_handle_media_capture_fails_when_executor_raises(tmp_path: Path) -> None:
    request = _build_request(target="cart")

    def _raising_executor(
        command: str,
        args: Sequence[str] = (),
        *,
        options: ProcessRunOptions | None = None,
    ) -> ProcessResult:
        del command, args, options
        raise RuntimeError("synthetic executor fault")

    with pytest.raises(MediaCaptureError) as excinfo:
        handle_media_capture(
            tmp_path,
            run_id="run-1",
            capture_request=request,
            design_capture_command="bin/capture --target={target}",
            secret=_TEST_SECRET,
            executor=_raising_executor,
        )
    assert "synthetic executor fault" in excinfo.value.reason
    assert _read_ledger_rows(tmp_path) == []


def test_handle_media_capture_fails_when_executor_returns_wrong_type(
    tmp_path: Path,
) -> None:
    request = _build_request(target="cart")

    def _wrong_type(
        command: str,
        args: Sequence[str] = (),
        *,
        options: ProcessRunOptions | None = None,
    ) -> object:
        del command, args, options
        return "not-a-process-result"

    with pytest.raises(MediaCaptureError) as excinfo:
        handle_media_capture(
            tmp_path,
            run_id="run-1",
            capture_request=request,
            design_capture_command="bin/capture --target={target}",
            secret=_TEST_SECRET,
            executor=_wrong_type,
        )
    assert "expected ProcessResult" in excinfo.value.reason


def test_handle_media_capture_fails_on_workspace_root_escape(tmp_path: Path) -> None:
    request = _build_request(target="cart")
    with pytest.raises(ValueError):
        handle_media_capture(
            tmp_path,
            run_id="run-1",
            capture_request=request,
            design_capture_command="bin/capture --target={target}",
            secret=_TEST_SECRET,
            executor=_FakeExecutor(),
            output_dir_relpath="../../../etc/visual-captures",
        )


def test_handle_media_capture_uses_bounded_per_cell_timeout(tmp_path: Path) -> None:
    request = _build_request(target="cart")
    fake = _FakeExecutor()
    handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --target={target}",
        secret=_TEST_SECRET,
        executor=fake,
        per_cell_timeout_seconds=DEFAULT_PER_CELL_TIMEOUT_SECONDS,
    )
    for _command, _args, options in fake.calls:
        assert options is not None
        assert options.timeout == DEFAULT_PER_CELL_TIMEOUT_SECONDS
        assert options.timeout > 0
        assert options.label is not None
        assert options.label.startswith("media-capture:")


def test_handle_media_capture_sets_cell_env(tmp_path: Path) -> None:
    request = _build_request(target="cart")
    fake = _FakeExecutor()
    handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --target={target}",
        secret=_TEST_SECRET,
        executor=fake,
    )
    expected_env_keys = {
        "RALPH_CAPTURE_OUTPUT",
        "RALPH_CAPTURE_TARGET",
        "RALPH_CAPTURE_VIEWPORT",
        "RALPH_CAPTURE_VIEWPORT_WIDTH",
        "RALPH_CAPTURE_VIEWPORT_HEIGHT",
        "RALPH_CAPTURE_THEME",
        "RALPH_CAPTURE_STATE",
        "RALPH_CAPTURE_CELL_ID",
    }
    cell_ids_in_env: set[str] = set()
    for _command, _args, options in fake.calls:
        assert options is not None
        assert options.env is not None
        assert expected_env_keys.issubset(set(options.env.keys()))
        assert options.env["RALPH_CAPTURE_TARGET"] == "cart"
        cell_ids_in_env.add(options.env["RALPH_CAPTURE_CELL_ID"])
    assert cell_ids_in_env == set(request.cell_ids)


def test_handle_media_capture_skips_ledger_when_secret_is_none(tmp_path: Path) -> None:
    request = _build_request(target="cart")
    fake = _FakeExecutor()
    result = handle_media_capture(
        tmp_path,
        run_id="run-1",
        capture_request=request,
        design_capture_command="bin/capture --target={target}",
        secret=None,
        executor=fake,
    )
    assert result.cells
    assert _read_ledger_rows(tmp_path) == []
