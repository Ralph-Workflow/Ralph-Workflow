"""Black-box end-to-end tests for the AGY smoke harness using the mock binary."""

from __future__ import annotations

import os
import unittest.mock
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from ralph.agents.completion_signals import _check_completion_sentinel
from ralph.agents.display_capabilities import DisplayCapability
from ralph.agents.parsers.agy import AgyParser
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import smoke as smoke_module
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.smoke_test_result import SmokeTestResult
from ralph.mcp.server._wire_ledger import wire_evidence_for
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunResult,
    resolve_smoke_harness_spec,
    run_smoke_plumbing,
)
from ralph.workspace.scope import WorkspaceScope

import_module("ralph.mcp.artifacts.markdown.specs")

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


def _mock_agy_path() -> Path:
    """Return the absolute path to the mock AGY shell wrapper."""
    return Path(__file__).resolve().parent / "_support" / "mock_agy.sh"


def _write_smoke_prompt(prompt_file: Path) -> None:
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(
        "Create a small JavaScript todo list at tmp/interactive-agy-smoke/todo-list.js.",
        encoding="utf-8",
    )


def _run_agy_smoke_plumbing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    behavior: str = "normal",
    agent_name: str = "agy/gemini-3.6-flash-low",
    subagents: bool = False,
) -> SmokeRunResult:
    """Drive ``run_smoke_plumbing`` with the mock AGY binary in ``tmp_path``."""
    mock_path = _mock_agy_path()
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_path))
    monkeypatch.setenv("MOCK_AGY_BEHAVIOR", behavior)
    monkeypatch.setenv("MOCK_AGY_ARTIFACT_DIR", str(tmp_path))
    if subagents:
        monkeypatch.setenv("MOCK_AGY_SUBAGENT", "1")

    def resolve_scope(*_args: object, **_kwargs: object) -> WorkspaceScope:
        return WorkspaceScope(tmp_path)

    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", resolve_scope)

    workspace_scope = WorkspaceScope(tmp_path)
    config = load_config(None, {}, workspace_scope=workspace_scope)
    config = smoke_module._apply_agy_binary_override_to_config(config)
    # Dynamic agy/<model> aliases are resolved from builtins, not from
    # config.agents, so inject the overridden config under the exact
    # agent name so the mock binary is honored.
    agent_config = AgentRegistry.from_config(config).get(agent_name)
    if agent_config is not None:
        agent_config = smoke_module._maybe_apply_agy_binary_override(agent_config)
        overridden_agents = dict(config.agents)
        overridden_agents[agent_name] = agent_config
        config = config.model_copy(update={"agents": overridden_agents})

    display_context = make_display_context()
    deps = DefaultPipelineFactory().build(config, display_context)

    smoke_dir = tmp_path / "tmp" / "interactive-agy-smoke"
    prompt_file = smoke_dir / "PROMPT.md"
    _write_smoke_prompt(prompt_file)

    return run_smoke_plumbing(
        config=config,
        workspace_root=tmp_path,
        agent_name=agent_name,
        prompt_file=prompt_file,
        output_file=smoke_dir / "todo-list.js",
        display_context=display_context,
        pipeline_deps=deps,
        subagents=subagents,
    )


# Module-scoped cache: the expensive smoke plumbing (subprocess startup
# + pipeline build + mock AGY invocation) is shared across all tests
# that use the SAME (behavior, agent_name) pair. The cache key is the
# tuple; the cached value is a triple (SmokeRunResult, tmp_path,
# deps) so tests can either assert against the result object
# directly OR read the persisted artifact / todo files from the
# cached tmp_path without re-running the subprocess.
#
# The cached ``tmp_path`` is the FIRST ``tmp_path`` seen for this
# cache key (later ``tmp_path`` fixtures are skipped via cache hit).
# All cached tests share that single tmp_path so they read the
# same files. Non-cached tests (quota_exhausted, Gemini agent,
# captures-both-sinks with monkeypatched execute_agent_effect) get
# a fresh invocation per test.
#
# Without this cache, the 7 tests in this file each spent ~1.7 s on
# real subprocess + pipeline setup, totaling ~12 s — well over the
# 60 s cumulative subprocess_e2e budget. With the cache, only 3 of
# the 7 tests drive a fresh subprocess; the other 4 share the cached
# result and run in <100 ms each.
_smoke_result_cache: dict[tuple[str, str], tuple[SmokeRunResult, Path, object]] = {}


@pytest.fixture(scope="module")
def cached_default_smoke(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[SmokeRunResult, Path]:
    """Module-scoped default smoke plumbing result shared across tests.

    Returns ``(result, workspace_tmp_path)``. The ``tmp_path`` is owned
    by the cache so all tests reading the persisted artifact /
    todo-list.js files see the SAME files written by the one shared
    smoke run.
    """
    key = ("normal", "agy/claude-sonnet-4-6")
    cached = _smoke_result_cache.get(key)
    if cached is not None:
        return cached[0], cached[1]

    workspace = tmp_path_factory.mktemp("agy_default_smoke_workspace")
    monkeypatch = pytest.MonkeyPatch()
    try:
        result = _run_agy_smoke_plumbing(
            workspace,
            monkeypatch,
            behavior="normal",
            agent_name="agy/claude-sonnet-4-6",
        )
        deps = None  # placeholder for future seam
        _smoke_result_cache[key] = (result, workspace, deps)
        return result, workspace
    finally:
        monkeypatch.undo()


def test_agy_harness_produces_real_output_with_mock(
    cached_default_smoke: tuple[SmokeRunResult, Path],
) -> None:
    """The full harness reports file=yes, tool activity=yes, artifact=yes, no breaks."""
    result, _workspace = cached_default_smoke
    assert result.file_created is True
    assert result.session_id is not None
    assert result.explicit_completion_seen.holds is True
    assert result.tool_activity_seen.holds is True
    assert any("tool_use: createTodoList" in line for line in result.meaningful_output_lines)
    assert any(line.startswith("tool_result:") for line in result.meaningful_output_lines)
    assert result.artifact_submitted.holds is True
    assert result.parsed_event_count > 0
    text_lines = [line for line in result.meaningful_output_lines if line.startswith("text:")]
    assert text_lines, (
        f"Expected at least one text-classified line, got: {result.meaningful_output_lines}"
    )
    assert all("raw:" not in line for line in result.meaningful_output_lines), (
        f"No line should be classified as raw, got: {result.meaningful_output_lines}"
    )
    assert any(len(line) > len("text: ") for line in text_lines), (
        f"Expected at least one text-classified line with non-empty content, "
        f"got: {result.meaningful_output_lines}"
    )


def test_agy_harness_subagent_stream_json_is_observed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """S-6: mock stream-json dispatch/result satisfies the subagent smoke contract.

    The mock emits TWO subagents in one frame (matching the measured live
    multi-subagent capture); S-4 relaxed the contract from "exactly one
    dispatch" to "at least one dispatch, each with a correlated result", so
    both are expected here.
    """
    result = _run_agy_smoke_plumbing(tmp_path, monkeypatch, subagents=True)

    assert result.subagent_dispatch_count == 2
    assert result.subagent_dispatch_seen is True
    assert result.subagent_result_seen is True
    assert result.post_subagent_activity_seen is True
    assert "subagent dispatch was not observed" not in result.errors
    assert "not every subagent dispatch has a correlated result" not in result.errors


def test_agy_harness_writes_artifact_with_correct_schema(
    cached_default_smoke: tuple[SmokeRunResult, Path],
) -> None:
    """The canonical Markdown artifact validates against the spec and SmokeTestResult."""
    _result, workspace = cached_default_smoke
    artifact_path = workspace / ".agent" / "artifacts" / "smoke_test_result.md"
    markdown = artifact_path.read_text(encoding="utf-8")
    content, diagnostics = parse_and_validate(markdown, get_spec("smoke_test_result"))
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert errors == [], f"Expected a spec-clean canonical artifact, got: {errors}"
    validated = SmokeTestResult.model_validate(content)
    assert validated.status == "passed"
    assert validated.output_file == "tmp/interactive-agy-smoke/todo-list.js"
    assert validated.observed_breaks == []
    assert "tool activity" in validated.headless_guide_checks
    assert "no output" not in (validated.observed_working or [])
    assert validated.summary


def test_agy_harness_writes_todo_list_with_expected_methods(
    cached_default_smoke: tuple[SmokeRunResult, Path],
) -> None:
    """The todo-list.js file exports a function and contains the expected method names."""
    _result, workspace = cached_default_smoke
    todo_path = workspace / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    text = todo_path.read_text(encoding="utf-8")
    assert "function createTodoList" in text
    assert "module.exports" in text
    for method in ("add", "list", "complete", "remove"):
        assert method in text


def test_agy_harness_quota_branch_emits_informational_not_live_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With MOCK_AGY_BEHAVIOR=quota_exhausted the harness reports the mock-empty note."""
    result = _run_agy_smoke_plumbing(tmp_path, monkeypatch, behavior="quota_exhausted")
    assert any("mock AGY produced empty stdout by design" in error for error in result.errors)
    assert not any("individual API quota exhausted" in error for error in result.errors)
    assert not any("RESOURCE_EXHAUSTED" in error for error in result.errors)


def test_agy_harness_session_id_present_with_mock(
    cached_default_smoke: tuple[SmokeRunResult, Path],
) -> None:
    """The harness extracts a session id matching the AGY smoke run id pattern."""
    result, _workspace = cached_default_smoke
    assert result.session_id is not None
    assert result.session_id.startswith("interactive-agy-smoke-")


def test_agy_smoke_promotes_artifact_and_records_completion_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mock AGY proves receipt promotion plus explicit completion durability.

    Drives the full smoke harness with the deterministic mock AGY binary
    using the ``agy/gemini-3.6-flash-low`` alias (the same alias used
    by the live regression suite and by the smoke CLI default). Asserts
    the five contract surfaces the completion contract requires:

    1. The canonical Markdown artifact exists at
       ``tmp_path / '.agent' / 'artifacts' / 'smoke_test_result.md'``
       (the mock authors the fallback ``.agent/tmp/smoke_test_result.md``
       document; promotion validates it and writes the canonical artifact).
    2. The canonical receipt is durably present for
       ``(run_id, artifact_type)`` — under RFC-013 P3 the canonical
       receipt store is the per-workspace ``.agent/state.db`` (one row
       per ``(run_id, artifact_type)``); ``promote_fallback_artifact`` at
       ``ralph/mcp/artifacts/canonical_submit.py`` calls
       ``write_artifact_receipt`` which inserts that row. The legacy
       ``.agent/receipts/<run_id>/<artifact_type>.json`` file path is a
       migration read fallback and a durable write fallback when DB
       persistence is unavailable.
    3. The receipt is identified by ``(run_id, artifact_type)`` with
       ``artifact_type == "smoke_test_result"``. Asserting presence via
       the public ``artifact_receipt_present`` API verifies the
       promotion contract without coupling to the storage-layout choice
       between the DB and the legacy file path.
    4. The mock's ``declare_complete`` simulation produced the independent
       run-scoped completion sentinel.
    5. The mock wrote the file the prompt asked for at
       ``tmp_path / 'tmp' / 'interactive-agy-smoke' / 'todo-list.js'``.

    The expected ``run_id`` is computed from
    ``resolve_smoke_harness_spec('agy/gemini-3.5-flash-medium').run_id``
    (= ``interactive-agy-smoke-gemini-3.5-flash-medium``) so the assertion
    stays in sync with the harness's sanitization rule.

    This test is the always-green mock-binary regression-proof that AGY
    artifact submission works just like any other agent. The companion
    live-binary test in ``tests/test_agy_live_regression.py`` covers the
    same contract against the real binary (with an xfail gate for
    documented upstream-blocked states).
    """
    result = _run_agy_smoke_plumbing(
        tmp_path,
        monkeypatch,
        agent_name="agy/gemini-3.5-flash-medium",
    )
    assert result.artifact_submitted.holds is True
    assert result.explicit_completion_seen.holds is True
    assert result.file_created is True

    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.md"
    assert artifact_path.is_file(), f"Expected the promoted canonical artifact at {artifact_path}"

    todo_path = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    assert todo_path.is_file(), f"Expected the mock-written todo file at {todo_path}"

    expected_run_id = resolve_smoke_harness_spec("agy/gemini-3.5-flash-medium").run_id
    # RFC-013 P3: the canonical receipt store is the per-workspace
    # .agent/state.db. The legacy .agent/receipts/<run_id>/<type>.json
    # path is a migration read fallback and the durable write fallback
    # when DB persistence fails, so successful DB writes do not double-write.
    # Asserting
    # via artifact_receipt_present (the public read API) verifies the
    # behavioral promotion contract -- the agent's fallback
    # .agent/tmp/smoke_test_result.md write was promoted to a durable
    # receipt -- without coupling to which physical store the receipt
    # landed in.
    assert artifact_receipt_present(tmp_path, expected_run_id, "smoke_test_result") is True, (
        f"Expected a canonical receipt for run_id={expected_run_id!r} "
        f"artifact_type='smoke_test_result'. The harness's "
        f"_is_smoke_artifact_submitted must call is_artifact_submitted -> "
        f"promote_fallback_artifact -> write_artifact_receipt to durably "
        f"stamp the receipt. Under RFC-013 P3 this lands as a row in "
        f"{tmp_path}/.agent/state.db (with the legacy file path preserved "
        f"as a durable fallback when DB persistence is unavailable)."
    )


# --- v1.1.13 measured-vocabulary mode (wt-015-agy-support S-5) ---

#: Distinct broker secret so wire-ledger / sentinel HMAC verification in
#: these tests never depends on (or mutates) any ambient developer secret.
_V1_1_13_BROKER_SECRET = "test-mock-agy-v1-1-13-broker-secret"

#: Vocabulary that ONLY the ``MOCK_AGY_V1_1_13=1`` emitter produces. The
#: default (flag-unset) output must not contain any of it. (``invoke_subagent``
#: and ``view_file`` are NOT here: both already appear in the default output
#: via the init tool list / default tool steps.)
_V1_1_13_ONLY_VOCABULARY: tuple[str, ...] = (
    "call_mcp_tool",
    "system_message",
    "ralph_submit_md_artifact",
    "declare_complete",
)


def _capture_mock_stdout(
    artifact_dir: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    v1_1_13: str | None,
) -> str:
    """Run the mock AGY in-process and return its raw stream-json stdout.

    The mock's ``main()`` is a plain ``argv -> int`` entry point, so it is
    invoked directly (no subprocess) under a fully rebuilt environment: every
    mode-selecting ``MOCK_AGY_*`` / ``RALPH_MCP_*`` variable is normalized so
    an ambient ``MOCK_AGY_V1_1_13=1`` (the plan's second verify command
    exports it for the whole pytest run) can never leak into the
    byte-comparison baseline.
    """
    from tests._support import mock_agy

    env = os.environ.copy()
    env["MOCK_AGY_ARTIFACT_DIR"] = str(artifact_dir)
    env.pop("MOCK_AGY_SUBAGENT", None)
    env.pop("MOCK_AGY_ARTIFACT_DIR_OVERRIDE", None)
    env.pop("RALPH_MCP_ENDPOINT", None)
    env.pop("RALPH_MCP_RUN_ID", None)
    if v1_1_13 is None:
        env.pop("MOCK_AGY_V1_1_13", None)
    else:
        env["MOCK_AGY_V1_1_13"] = v1_1_13
    with unittest.mock.patch.dict(os.environ, env, clear=True):
        exit_code = mock_agy.main(
            [
                "--print",
                "--output-format",
                "stream-json",
                "--model",
                "gemini-3.6-flash-low",
            ]
        )
    assert exit_code == 0, f"mock AGY exited {exit_code}"
    return capsys.readouterr().out


def test_mock_v1_1_13_default_byte_compatible(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Flag unset or ``0``: the mock's stdout is byte-identical default output.

    Pins the default-mock contract the existing harness tests rely on: no
    v1.1.13 vocabulary (``call_mcp_tool``, ``system_message``,
    ``invoke_subagent``, ``ralph_*``, ``view_file``) may leak when the flag
    is off, and explicitly setting ``MOCK_AGY_V1_1_13=0`` must reproduce
    the flag-unset bytes exactly.
    """
    stdout_unset = _capture_mock_stdout(tmp_path / "unset", capsys, v1_1_13=None)
    stdout_zero = _capture_mock_stdout(tmp_path / "zero", capsys, v1_1_13="0")

    assert stdout_unset == stdout_zero, (
        "MOCK_AGY_V1_1_13=0 must be byte-compatible with the flag-unset default output"
    )
    for token in _V1_1_13_ONLY_VOCABULARY:
        assert token not in stdout_unset, (
            f"v1.1.13-only vocabulary {token!r} leaked into the default (flag-unset) mock output"
        )
    assert stdout_unset.strip(), "default mock output must not be empty"


def test_mock_v1_1_13_vocabulary_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``MOCK_AGY_V1_1_13=1``: measured vocabulary + real MCP / completion / preview.

    Drives the full in-process smoke harness with the v1.1.13 mock and a
    real broker secret, then asserts the plan's six signals:

    (a) the subagent ACTIVE/DONE pair (step-level ``tool_name``
        ``invoke_subagent``, role "Todo Edge Case Researcher") parses to a
        correlated tool_use/tool_result pair;
    (b) the bodiless ``system_message`` frame surfaces as a lifecycle event;
    (c) the ``call_mcp_tool`` -> ``ralph_submit_md_artifact`` ACTIVE/DONE
        pair parses with the measured ``ServerName``/``ToolName`` envelope
        and precedes the ``declare_complete`` pair;
    (d) a verified wire-ledger record for ``ralph_submit_md_artifact`` on
        this run exists (``wire_evidence_for`` with the broker secret);
    (e) the durable completion sentinel validates via the public
        ``_check_completion_sentinel`` helper for the run id;
    (f) the display's capability recorder observed SYNTAX_HIGHLIGHTING
        (via the ``write_to_file`` pair's correlated result) and
        FILE_PREVIEW (via the ``read_file`` pair).
    """
    monkeypatch.setenv("MOCK_AGY_V1_1_13", "1")
    monkeypatch.setenv("RALPH_BROKER_SECRET", _V1_1_13_BROKER_SECRET)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MOCK_AGY_SUBAGENT", raising=False)

    result = _run_agy_smoke_plumbing(tmp_path, monkeypatch)

    # --- transcript-level parser assertions (a), (b), (c) ---
    stdout = _capture_mock_stdout(tmp_path / "transcript", capsys, v1_1_13="1")
    parser = AgyParser()
    events = list(parser.parse(iter(stdout.splitlines())))

    subagent_uses = [
        event
        for event in events
        if event.type == "tool_use" and event.content == "Todo Edge Case Researcher"
    ]
    subagent_results = [
        event
        for event in events
        if event.type == "tool_result" and event.content.startswith("Todo Edge Case Researcher")
    ]
    assert subagent_uses, "expected the invoke_subagent dispatch to parse as a tool_use"
    assert subagent_results, (
        "expected the invoke_subagent DONE frame to parse as a correlated tool_result"
    )

    system_message_events = [
        event for event in events if event.content == "agy step system_message"
    ]
    assert system_message_events, (
        "expected the bodiless system_message frame to surface as a lifecycle event"
    )

    def _event_pos(predicate: Callable[[Any], bool]) -> int:
        for index, event in enumerate(events):
            if predicate(event):
                return index
        return -1

    subagent_result_pos = _event_pos(
        lambda event: (
            event.type == "tool_result" and event.content.startswith("Todo Edge Case Researcher")
        )
    )
    system_message_pos = _event_pos(lambda event: event.content == "agy step system_message")
    submit_use_pos = _event_pos(
        lambda event: (
            event.type == "tool_use"
            and (event.metadata or {}).get("tool") == "call_mcp_tool"
            and (event.metadata or {}).get("tool_info", {}).get("parameters", {}).get("ToolName")
            == "ralph_submit_md_artifact"
        )
    )
    declare_pos = _event_pos(
        lambda event: (
            event.type == "tool_use"
            and (event.metadata or {}).get("tool") == "call_mcp_tool"
            and (event.metadata or {}).get("tool_info", {}).get("parameters", {}).get("ToolName")
            == "declare_complete"
        )
    )
    submit_result_pos = _event_pos(
        lambda event: (
            event.type == "tool_result"
            and (event.metadata or {}).get("tool") == "call_mcp_tool"
            and (event.metadata or {}).get("tool_info", {}).get("parameters", {}).get("ToolName")
            == "ralph_submit_md_artifact"
        )
    )
    assert -1 not in {subagent_result_pos, system_message_pos, submit_use_pos, declare_pos}, (
        "expected subagent result, system_message lifecycle, and both call_mcp_tool uses"
    )
    assert subagent_result_pos < system_message_pos < submit_use_pos < declare_pos, (
        "v1.1.13 frames must arrive in the measured order: subagent pair, "
        "system_message, ralph_submit_md_artifact pair, declare_complete pair"
    )
    assert submit_result_pos > submit_use_pos, "submit tool_result must follow its tool_use"

    # --- harness-level assertions (d), (e), (f) ---
    run_id = resolve_smoke_harness_spec("agy/gemini-3.6-flash-low").run_id

    assert result.parsed_event_count >= 8, (
        f"expected at least 8 parser events, got {result.parsed_event_count}"
    )
    assert result.subagent_dispatch_seen is True
    assert result.subagent_result_seen is True
    assert result.subagent_dispatch_count >= 1
    assert result.artifact_submitted.holds is True
    assert result.explicit_completion_seen.holds is True

    assert (
        wire_evidence_for(
            tmp_path,
            run_id,
            tool_name="ralph_submit_md_artifact",
            secret=_V1_1_13_BROKER_SECRET,
        )
        is True
    ), "expected a verified wire-ledger tools/call record for ralph_submit_md_artifact"

    assert (
        _check_completion_sentinel(
            tmp_path,
            run_id,
            sentinel_secret=_V1_1_13_BROKER_SECRET,
        )
        is True
    ), "expected the durable, HMAC-verified completion sentinel for the run"

    assert DisplayCapability.SYNTAX_HIGHLIGHTING in result.observed_capabilities, (
        f"expected SYNTAX_HIGHLIGHTING to be observed, got {result.observed_capabilities}"
    )
    assert DisplayCapability.FILE_PREVIEW in result.observed_capabilities, (
        f"expected FILE_PREVIEW to be observed, got {result.observed_capabilities}"
    )
    assert not any("capability" in error.lower() for error in result.errors), (
        f"unexpected capability breaks: {result.errors}"
    )
