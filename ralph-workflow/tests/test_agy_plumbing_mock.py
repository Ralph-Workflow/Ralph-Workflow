"""Contract tests for AGY-specific smoke plumbing.

The prompt-contract and mock-diagnostic tests are fast unit tests that run
under ``make test``. The negative import-time invariant tests (guards firing
on bad values and surviving ``python -O``) are marked ``subprocess_e2e`` and
run under ``make test-subprocess-e2e``.

The RALPH_AGY_BINARY override is split into two contracts:

* A general binary override: a real wrapper, alternate live binary path, or
  ``agy`` on ``PATH``. Treated as a live AGY run.
* A mock binary override: the deterministic mock at
  ``tests/_support/mock_agy.sh`` (or ``mock_agy.py``). Empty stdout is
  expected under ``MOCK_AGY_BEHAVIOR=quota_exhausted|invalid_model`` and
  surfaces the informational mock-empty note.

The detection helper is :func:`is_mock_agy_override`; the smoke
diagnostic honours that helper to keep the two contracts from leaking
into each other.
"""

from __future__ import annotations

import contextlib
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from ralph.pipeline.plumbing.smoke_plumbing import (
    _AGENT_SESSION_CEILINGS,
    _SMOKE_IDLE_TIMEOUT_SECONDS,
    _SMOKE_MAX_TURNS,
    _agy_upstream_diagnostic,
    _build_smoke_prompt,
    is_mock_agy_override,
)


def test_agy_prompt_uses_canonical_markdown_submit_tool() -> None:
    """The AGY smoke prompt submits Markdown through the canonical MCP tool."""
    prompt_text = _build_smoke_prompt(
        "tmp/interactive-agy-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_md_artifact",
    )
    assert "Call `ralph_submit_md_artifact`" in prompt_text
    assert 'artifact_type="smoke_test_result"' in prompt_text
    assert "```markdown" in prompt_text
    assert "mandatory final action" in prompt_text
    assert "receipt is not phase completion" in prompt_text
    assert "Do not start background work" in prompt_text


def test_agy_prompt_allows_validated_fallback_when_submit_tool_is_unavailable() -> None:
    """AGY can leave the validated fallback without writing a canonical artifact."""
    prompt_text = _build_smoke_prompt(
        "tmp/interactive-agy-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_md_artifact",
    )
    assert "If the submission tool is unavailable" in prompt_text
    assert ".agent/tmp/smoke_test_result.md" in prompt_text
    assert "Do not write the canonical artifact directly" in prompt_text


def test_smoke_invariants_hold() -> None:
    """The smoke plumbing invariants are satisfied by the current constants."""
    assert _SMOKE_MAX_TURNS >= 1
    assert _SMOKE_IDLE_TIMEOUT_SECONDS > 0
    assert _AGENT_SESSION_CEILINGS["agy"] > _SMOKE_IDLE_TIMEOUT_SECONDS


def test_is_mock_agy_override_false_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without RALPH_AGY_BINARY the override is not the mock."""
    monkeypatch.delenv("RALPH_AGY_BINARY", raising=False)
    assert is_mock_agy_override() is False


def test_is_mock_agy_override_true_for_mock_shell_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mock shell wrapper is detected by basename."""
    mock_path = str(Path(__file__).resolve().parent / "_support" / "mock_agy.sh")
    monkeypatch.setenv("RALPH_AGY_BINARY", mock_path)
    assert is_mock_agy_override() is True


def test_is_mock_agy_override_true_for_mock_python_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mock Python module is detected by basename."""
    mock_path = str(Path(__file__).resolve().parent / "_support" / "mock_agy.py")
    monkeypatch.setenv("RALPH_AGY_BINARY", mock_path)
    assert is_mock_agy_override() is True


def test_is_mock_agy_override_false_for_real_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A general RALPH_AGY_BINARY override is not the mock binary."""
    monkeypatch.setenv("RALPH_AGY_BINARY", "/opt/agy-wrapper/agy")
    assert is_mock_agy_override() is False


def test_is_mock_agy_override_false_for_bare_agy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bare ``agy`` on PATH is the default live binary, not the mock."""
    monkeypatch.setenv("RALPH_AGY_BINARY", "agy")
    assert is_mock_agy_override() is False


def test_agy_mock_empty_stdout_diagnostic_is_informational(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mock-binary override surfaces the informational mock-empty note on empty stdout."""
    mock_path = str(Path(__file__).resolve().parent / "_support" / "mock_agy.sh")
    monkeypatch.setenv("RALPH_AGY_BINARY", mock_path)
    assert is_mock_agy_override() is True
    diagnostic = _agy_upstream_diagnostic([], tmp_path)
    assert diagnostic is not None
    assert "mock AGY produced empty stdout by design" in diagnostic
    assert "MOCK_AGY_BEHAVIOR=quota_exhausted or invalid_model" in diagnostic
    assert "harness captured this correctly" in diagnostic
    assert "individual API quota exhausted" not in diagnostic
    assert "RESOURCE_EXHAUSTED" not in diagnostic


def test_agy_non_mock_empty_stdout_does_not_use_mock_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A non-mock RALPH_AGY_BINARY override must not be diagnosed as a mock-empty run.

    Regression: the prior implementation treated every ``RALPH_AGY_BINARY``
    override as a mock run, so the live ``cli.log`` quota / model-id
    diagnostic was masked by the informational mock-empty note. A real
    wrapper or alternate live binary path must take the live-diagnostic
    branch so a genuine live-AGY failure is never hidden.
    """
    monkeypatch.setenv("RALPH_AGY_BINARY", "/opt/agy-wrapper/agy")
    assert is_mock_agy_override() is False
    diagnostic = _agy_upstream_diagnostic([], tmp_path)
    # A non-mock override must NOT be reported as a mock-empty run.
    if diagnostic is not None:
        assert "mock AGY produced empty stdout by design" not in diagnostic
        assert "MOCK_AGY_BEHAVIOR" not in diagnostic
        assert "harness captured this correctly" not in diagnostic
    # Whether the diagnostic is None (when no live cli.log issue is
    # present) or a live-diagnostic string, the contract is: never the
    # mock-empty note. The exact diagnostic is environment-dependent.


def test_agy_bare_agy_override_does_not_use_mock_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A bare ``agy`` override (i.e. the default live binary) takes the live branch."""
    monkeypatch.setenv("RALPH_AGY_BINARY", "agy")
    assert is_mock_agy_override() is False
    diagnostic = _agy_upstream_diagnostic([], tmp_path)
    if diagnostic is not None:
        assert "mock AGY produced empty stdout by design" not in diagnostic


def _get_smoke_plumbing_path() -> str:
    """Return the absolute path to ralph/pipeline/plumbing/smoke_plumbing.py."""
    test_dir = Path(__file__).parent
    return str(test_dir.parent / "ralph" / "pipeline" / "plumbing" / "smoke_plumbing.py")


def _run_patched_smoke_plumbing_import(
    *,
    max_turns: int | None = None,
    idle_timeout: float | None = None,
    agy_ceiling: float | None = None,
    minus_o: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess that patches smoke_plumbing.py constants and imports it."""
    smoke_path = _get_smoke_plumbing_path()
    # smoke_path is .../ralph-workflow/ralph/pipeline/plumbing/smoke_plumbing.py
    repo_root = str(Path(smoke_path).parent.parent.parent.parent)
    original = Path(smoke_path).read_text(encoding="utf-8")

    patched = original
    if max_turns is not None:
        patched = patched.replace(
            f"_SMOKE_MAX_TURNS = {5}",
            f"_SMOKE_MAX_TURNS = {max_turns}",
        )
    if idle_timeout is not None:
        patched = patched.replace(
            f"_SMOKE_IDLE_TIMEOUT_SECONDS = {30.0}",
            f"_SMOKE_IDLE_TIMEOUT_SECONDS = {idle_timeout}",
        )
    if agy_ceiling is not None:
        patched = patched.replace(
            '"agy": 360.0,',
            f'"agy": {agy_ceiling},',
        )

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="smoke_plumbing_patched_", delete=False
    ) as f:
        f.write(patched)
        f.flush()
        tmp_path = f.name

    try:
        runner = (
            "import sys\n"
            f"sys.path.insert(0, {repo_root!r})\n"
            "import importlib.util\n"
            "spec = importlib.util.spec_from_file_location(\n"
            "    'ralph.pipeline.plumbing.smoke_plumbing',\n"
            f"    {tmp_path!r},\n"
            ")\n"
            "mod = importlib.util.module_from_spec(spec)\n"
            "sys.modules['ralph.pipeline.plumbing.smoke_plumbing'] = mod\n"
            "spec.loader.exec_module(mod)\n"
            "print('OK')\n"
        )

        cmd = [sys.executable]
        if minus_o:
            cmd.append("-O")
        cmd.extend(["-c", runner])

        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=repo_root,
            check=False,
        )
    finally:
        with contextlib.suppress(OSError):
            Path(tmp_path).unlink()


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_smoke_max_turns_invariant_fires() -> None:
    """_SMOKE_MAX_TURNS < 1 must raise RuntimeError at import time."""
    result = _run_patched_smoke_plumbing_import(max_turns=0)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_SMOKE_MAX_TURNS must be >= 1" in result.stderr


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_smoke_max_turns_invariant_survives_minus_o() -> None:
    """_SMOKE_MAX_TURNS invariant must survive ``python -O``."""
    result = _run_patched_smoke_plumbing_import(max_turns=0, minus_o=True)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_SMOKE_MAX_TURNS must be >= 1" in result.stderr


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_smoke_idle_timeout_invariant_fires() -> None:
    """_SMOKE_IDLE_TIMEOUT_SECONDS <= 0 must raise RuntimeError at import time."""
    result = _run_patched_smoke_plumbing_import(idle_timeout=0.0)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_SMOKE_IDLE_TIMEOUT_SECONDS must be > 0" in result.stderr


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_smoke_idle_timeout_invariant_survives_minus_o() -> None:
    """_SMOKE_IDLE_TIMEOUT_SECONDS invariant must survive ``python -O``."""
    result = _run_patched_smoke_plumbing_import(idle_timeout=-1.0, minus_o=True)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_SMOKE_IDLE_TIMEOUT_SECONDS must be > 0" in result.stderr


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_agy_session_ceiling_invariant_fires() -> None:
    """AGY ceiling <= idle timeout must raise RuntimeError at import time."""
    result = _run_patched_smoke_plumbing_import(agy_ceiling=10.0)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_AGENT_SESSION_CEILINGS['agy'] must exceed _SMOKE_IDLE_TIMEOUT_SECONDS" in result.stderr


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_agy_session_ceiling_invariant_survives_minus_o() -> None:
    """AGY ceiling invariant must survive ``python -O``."""
    result = _run_patched_smoke_plumbing_import(agy_ceiling=10.0, minus_o=True)
    assert result.returncode != 0
    assert "RuntimeError" in result.stderr
    assert "_AGENT_SESSION_CEILINGS['agy'] must exceed _SMOKE_IDLE_TIMEOUT_SECONDS" in result.stderr


def test_execute_smoke_turns_stops_after_one_attempt_when_ceiling_below_wire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The turn loop must not retry once turn 1's ceiling already rules out WIRE.

    DA-001 regression: ``_execute_smoke_turns`` (``smoke_plumbing.py``) only
    re-enters its ``for _attempt in range(_SMOKE_MAX_TURNS)`` loop from the
    ``OpenCodeResumableExitError`` branch, which is supposed to ``break``
    instead of ``continue`` once ``transport_evidence_ceiling(...) <
    Provenance.WIRE``. Nothing previously drove that loop through a second
    turn to prove the early break actually fires -- a future edit that
    deleted it would silently regress to burning all ``_SMOKE_MAX_TURNS``
    turns on a run whose ceiling already ruled out ``PASS`` from turn 1,
    with no test catching it.

    A fake multi-turn transport double (not live agy) raises
    ``OpenCodeResumableExitError`` on every call and, on its first call,
    deposits an ``init`` frame built from the mock's own
    ``_MOCK_INIT_TOOL_NAMES`` (no ``ralph_*`` / ``mcp__ralph__*`` /
    ``call_mcp_tool`` route), mirroring
    ``test_mock_agy_evidence_ceiling_grades_below_wire`` in
    ``test_smoke_agy_end_to_end.py``. The double must be invoked exactly
    once, not ``_SMOKE_MAX_TURNS`` (5) times, and the ceiling computed from
    the returned lines must stay below ``WIRE``.
    """
    import json
    from collections.abc import MutableSequence

    from ralph.agents.invoke import InvokeOptions, OpenCodeResumableExitError
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
    from ralph.display.context import make_display_context
    from ralph.pipeline.plumbing.smoke_evidence import Provenance
    from ralph.pipeline.plumbing.smoke_plumbing import (
        SmokeRunParams,
        _execute_smoke_turns,
        transport_evidence_ceiling,
    )
    from tests._support.mock_agy import _MOCK_INIT_TOOL_NAMES

    init_line = json.dumps(
        {
            "event": "init",
            "conversation_id": "00000000-0000-0000-0000-000000000000",
            "init": {
                "model": "default",
                "cwd": ".",
                "tools": list(_MOCK_INIT_TOOL_NAMES),
                "permission_mode": "always-proceed",
            },
        }
    )

    call_count = 0

    def _fake_execute_agent_effect(
        effect: object,
        unified_config: object,
        pipeline_deps: object,
        workspace_scope: object,
        *,
        bridge: object,
        display_context: object,
        display: object = None,
        run_id: str,
        raw_output_sink: MutableSequence[str],
        rendered_output_sink: MutableSequence[str],
        raw_line_sink: object = None,
        set_session_id_cb: object,
        invoke_agent: object,
        raise_resumable_exit: object,
    ) -> object:
        nonlocal call_count
        call_count += 1
        raw_output_sink.append(init_line)
        raise OpenCodeResumableExitError("agy/test-model")

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    output_file = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    params = SmokeRunParams(
        agent_name="agy/test-model",
        config=config,
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=tmp_path,
        prompt_file=prompt_file,
        output_file=output_file,
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=object(),
        pipeline_deps=object(),
    )

    all_lines, _live_lines, _session_id, final_exception = _execute_smoke_turns(
        params, None, run_id="interactive-agy-smoke-test-model"
    )

    assert call_count == 1, (
        "the fake transport must be invoked exactly once: the turn loop must "
        f"break after turn 1 once the ceiling is below WIRE, got {call_count} calls"
    )
    assert isinstance(final_exception, OpenCodeResumableExitError)
    ceiling = transport_evidence_ceiling(config, all_lines)
    assert ceiling < Provenance.WIRE


def test_execute_smoke_turns_reports_ceiling_early_for_single_turn_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2 regression: the evidence ceiling is logged before the run ends,
    even for a single-turn run that never enters the
    ``OpenCodeResumableExitError`` retry branch S-3's regression covers.

    Before S-2, ``transport_evidence_ceiling(...)`` was only observed inside
    that retry branch, so an operator watching a live, possibly-long,
    single-turn run learned the ceiling only from the final report table
    (``smoke.py``'s "Ralph tools advertised" line) after the run finished.
    This pins that the ceiling is now logged as soon as the first
    ``init``-shaped frame is parsed, for the plain success path too.
    """
    import io
    import json
    from collections.abc import MutableSequence

    from loguru import logger

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.enums import AgentTransport
    from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
    from ralph.display.context import make_display_context
    from ralph.pipeline.events import PipelineEvent
    from ralph.pipeline.plumbing.smoke_plumbing import (
        SmokeRunParams,
        _execute_smoke_turns,
        transport_evidence_ceiling,
    )
    from tests._support.mock_agy import _MOCK_INIT_TOOL_NAMES

    init_line = json.dumps(
        {
            "event": "init",
            "conversation_id": "00000000-0000-0000-0000-000000000000",
            "init": {
                "model": "default",
                "cwd": ".",
                "tools": list(_MOCK_INIT_TOOL_NAMES),
                "permission_mode": "always-proceed",
            },
        }
    )

    call_count = 0

    def _fake_execute_agent_effect(
        effect: object,
        unified_config: object,
        pipeline_deps: object,
        workspace_scope: object,
        *,
        bridge: object,
        display_context: object,
        display: object = None,
        run_id: str,
        raw_output_sink: MutableSequence[str],
        rendered_output_sink: MutableSequence[str],
        raw_line_sink: object = None,
        set_session_id_cb: object,
        invoke_agent: object,
        raise_resumable_exit: object,
    ) -> object:
        nonlocal call_count
        call_count += 1
        raw_output_sink.append(init_line)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _fake_execute_agent_effect,
    )

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    output_file = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    config = AgentConfig(cmd="agy", transport=AgentTransport.AGY)
    params = SmokeRunParams(
        agent_name="agy/test-model",
        config=config,
        unified_config=UnifiedConfig(general=GeneralConfig()),
        workspace_root=tmp_path,
        prompt_file=prompt_file,
        output_file=output_file,
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=object(),
        pipeline_deps=object(),
    )

    buf = io.StringIO()
    logger.remove()
    handler_id = logger.add(buf, level="INFO")
    try:
        all_lines, _live_lines, _session_id, final_exception = _execute_smoke_turns(
            params, None, run_id="interactive-agy-smoke-test-model"
        )
    finally:
        logger.remove(handler_id)

    assert call_count == 1
    assert final_exception is None
    output = buf.getvalue()
    assert "transport evidence ceiling" in output, (
        f"Expected the early ceiling diagnostic to be logged, got: {output!r}"
    )
    ceiling = transport_evidence_ceiling(config, all_lines)
    assert ceiling.name in output
