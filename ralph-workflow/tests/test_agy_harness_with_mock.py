"""Black-box end-to-end tests for the AGY smoke harness using the mock binary."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import smoke as smoke_module
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.smoke_test_result import SmokeTestResult
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunResult,
    resolve_smoke_harness_spec,
    run_smoke_plumbing,
)
from ralph.workspace.scope import WorkspaceScope

import_module("ralph.mcp.artifacts.markdown.specs")

if TYPE_CHECKING:
    from collections import deque

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
    agent_name: str = "agy/Claude Sonnet 4.6 (Thinking)",
) -> SmokeRunResult:
    """Drive ``run_smoke_plumbing`` with the mock AGY binary in ``tmp_path``."""
    mock_path = _mock_agy_path()
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_path))
    monkeypatch.setenv("MOCK_AGY_BEHAVIOR", behavior)
    monkeypatch.setenv("MOCK_AGY_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(
        smoke_module,
        "resolve_workspace_scope",
        lambda *_args, **_kwargs: WorkspaceScope(tmp_path),
    )

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
_smoke_result_cache: dict[
    tuple[str, str], tuple[SmokeRunResult, Path, object]
] = {}


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
    key = ("normal", "agy/Claude Sonnet 4.6 (Thinking)")
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
            agent_name="agy/Claude Sonnet 4.6 (Thinking)",
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
    assert result.explicit_completion_seen is True
    assert result.tool_activity_seen is True
    assert result.artifact_submitted is True
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


def test_agy_harness_captures_both_sinks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``execute_agent_effect`` receives both raw and rendered output sinks."""
    captured_raw: deque[str] | None = None
    captured_rendered: deque[str] | None = None

    original_execute = effect_executor_module.execute_agent_effect

    def _wrapped_execute(*args: object, **kwargs: object) -> object:
        nonlocal captured_raw, captured_rendered
        captured_raw = kwargs.get("raw_output_sink")
        captured_rendered = kwargs.get("rendered_output_sink")
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _wrapped_execute,
    )
    _run_agy_smoke_plumbing(tmp_path, monkeypatch)
    assert captured_raw is not None
    assert captured_rendered is not None
    assert len(captured_raw) >= 3


def test_agy_harness_session_id_present_with_mock(
    cached_default_smoke: tuple[SmokeRunResult, Path],
) -> None:
    """The harness extracts a session id matching the AGY smoke run id pattern."""
    result, _workspace = cached_default_smoke
    assert result.session_id is not None
    assert result.session_id.startswith("interactive-agy-smoke-")


def test_agy_smoke_promotes_artifact_to_canonical_receipt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Mock-binary AGY end-to-end proves the canonical receipt promotion contract.

    Drives the full smoke harness with the deterministic mock AGY binary
    using the ``agy/Gemini 3.5 Flash (Medium)`` alias (the same alias used
    by the live regression suite and by the smoke CLI default). Asserts
    the four contract surfaces the user explicitly asked for:

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
       ``.agent/receipts/<run_id>/<artifact_type>.json`` file path is
       read-only fallback during the dual-read rollout window.
    3. The receipt is identified by ``(run_id, artifact_type)`` with
       ``artifact_type == "smoke_test_result"``. Asserting presence via
       the public ``artifact_receipt_present`` API verifies the
       promotion contract without coupling to the storage-layout choice
       between the DB and the legacy file path.
    4. The mock wrote the file the prompt asked for at
       ``tmp_path / 'tmp' / 'interactive-agy-smoke' / 'todo-list.js'``.

    The expected ``run_id`` is computed from
    ``resolve_smoke_harness_spec('agy/Gemini 3.5 Flash (Medium)').run_id``
    (= ``interactive-agy-smoke-Gemini-3.5-Flash-Medium``) so the assertion
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
        agent_name="agy/Gemini 3.5 Flash (Medium)",
    )
    assert result.artifact_submitted is True
    assert result.file_created is True

    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.md"
    assert artifact_path.is_file(), f"Expected the promoted canonical artifact at {artifact_path}"

    todo_path = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    assert todo_path.is_file(), f"Expected the mock-written todo file at {todo_path}"

    expected_run_id = resolve_smoke_harness_spec("agy/Gemini 3.5 Flash (Medium)").run_id
    # RFC-013 P3: the canonical receipt store is the per-workspace
    # .agent/state.db. The legacy .agent/receipts/<run_id>/<type>.json
    # path is read-only fallback during the dual-read rollout window,
    # so production writes don't double-write to both stores. Asserting
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
        f"as read-only fallback during the dual-read rollout window)."
    )
