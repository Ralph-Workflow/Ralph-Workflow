from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline._runner_session import (
    apply_session_capture,
    pop_last_captured_session_id,
)
from ralph.pipeline.integration_resolution import RESOLVED
from ralph.pipeline.integration_resolution_types import IntegrationResolutionVerdict
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy import analysis as policy_analysis
from ralph.project_policy import cli_integration
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.config.models import AgentConfig as _AgentConfig
    from ralph.pipeline.events import PipelineEvent


_POLICY_REM_FAKE_SESSION_ID: str = "sess-policy-remediation-ready"
_POLICY_ANALYSIS_FAKE_SESSION_ID: str = "sess-policy-analysis-approved"

_POLICY_ANALYSIS_STALE_MARKER: str = "STALE-PRE-POLICY-ANALYSIS-MARKER-DO-NOT-SEE"


class _ShutdownCountingBridge:
    def __init__(self) -> None:
        self.run_id: str = "shutdown-counting-bridge"

    def shutdown(self) -> None:
        _shutdown_counter["count"] += 1

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:0/mcp"

    def reset_tool_registry(self) -> None:
        pass


_shutdown_counter: dict[str, int] = {"count": 0}


class _RecordingShutdownBridgeFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return _ShutdownCountingBridge()


def _seed_complete_policy_in_git_repo(workspace_root: Path) -> None:
    from ralph.project_policy import markers as pp_markers
    from ralph.project_policy import starters
    from tests.project_policy.test_validator import _complete_policy_body

    workspace = FsWorkspace(workspace_root, allowed_roots=[workspace_root])
    canonical = pp_markers.CANONICAL_DIR.rstrip("/")
    workspace.write(
        pp_markers.PORTFOLIO_PATH,
        starters.read_starter("policy-portfolio.toml").replace(
            "PROJECT-FACT-UNRESOLVED", "make test"
        ),
    )
    for filename in pp_markers.CORE_POLICY_FILES:
        workspace.write(
            f"{canonical}/{filename}",
            _complete_policy_body(
                filename=filename,
                lang=None
                if filename not in {"typechecking-policy.md", "linting-policy.md"}
                else "Python",
            ),
        )
    _seed_agents_md_for_path(workspace_root)
    _seed_claude_md_for_path(workspace_root)


def _seed_agents_md_for_path(workspace_root: Path) -> None:
    from ralph.project_policy import markers as pp_markers

    workspace = FsWorkspace(workspace_root, allowed_roots=[workspace_root])
    workspace.write(
        pp_markers.AGENTS_MD,
        f"{pp_markers.AGENTS_BLOCK_BEGIN}\n"
        f"See {pp_markers.CANONICAL_DIR}.\n"
        f"{pp_markers.AGENTS_BLOCK_END}\n",
    )


def _seed_claude_md_for_path(workspace_root: Path) -> None:
    from ralph.project_policy import markers as pp_markers

    workspace = FsWorkspace(workspace_root, allowed_roots=[workspace_root])
    workspace.write(
        pp_markers.CLAUDE_MD,
        "# CLAUDE.md\n\nSee AGENTS.md for project policy.\n",
    )


def _seed_analysis_approval(workspace_root: Path) -> None:
    workspace = FsWorkspace(workspace_root, allowed_roots=[workspace_root])
    workspace.write(
        policy_analysis.ANALYSIS_ARTIFACT_REL_PATH,
        "---\n"
        "type: policy_remediation_analysis_decision\n"
        "status: completed\n"
        "---\n"
        "\n"
        "## Summary\n"
        "\n"
        "- [SUM-1] Policy is sound; every committed criterion is met.\n"
        "\n"
        "## Criterion Verdicts\n"
        "\n"
        "- [PR-001] Criterion: the declared verification command resolves. "
        "Expected observation: `make verify` invokes a target. "
        "Verdict: met. Evidence: make reports the rule. "
        "Location: verification-policy.md RALPH-COMMAND.\n",
    )


def _seed_stale_analysis_artifact(workspace_root: Path) -> None:
    workspace = FsWorkspace(workspace_root, allowed_roots=[workspace_root])
    workspace.write(
        policy_analysis.ANALYSIS_ARTIFACT_REL_PATH,
        f"{_POLICY_ANALYSIS_STALE_MARKER}\n"
        "type: policy_remediation_analysis_decision\n"
        "status: failed\n"
        "\n"
        "## Summary\n"
        "\n"
        "- [SUM-1] stale-content-discriminator\n",
    )


def _build_load_result(
    workspace_root: Path,
    *,
    policy_bundle: object | None = None,
    initial_phase: str = "planning",
) -> run_module._LoadResult:
    return run_module._LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(
            root=str(workspace_root),
            allowed_roots=[str(workspace_root)],
        ),
        initial_state=PipelineState(phase=initial_phase, policy_entry_phase=initial_phase),
        policy_bundle=policy_bundle,
        run_id="test-run-id",
    )


def _run_ready_route(
    tmp_git_repo: Path,
    load_result: run_module._LoadResult,
) -> tuple[int, FsWorkspace, object, list[object], run_module._LoadResult]:
    _shutdown_counter["count"] = 0
    workspace = FsWorkspace(tmp_git_repo, allowed_roots=[tmp_git_repo])

    _seed_stale_analysis_artifact(tmp_git_repo)

    display_context = make_display_context()

    deps = make_test_pipeline_deps(
        display_context,
        bridge_factory=_RecordingShutdownBridgeFactory(),
    )

    state = {"invocation": 0}

    def policy_session_id_for_invocation() -> str:
        state["invocation"] += 1
        if state["invocation"] % 2 == 1:
            _seed_complete_policy_in_git_repo(tmp_git_repo)
            return _POLICY_REM_FAKE_SESSION_ID
        _seed_analysis_approval(tmp_git_repo)
        return _POLICY_ANALYSIS_FAKE_SESSION_ID

    def fake_invoke_agent(
        config: _AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[object]:
        del config, prompt_file, options
        return iter(
            [
                f"Session ID: {policy_session_id_for_invocation()}",
                '{"type":"result"}',
            ]
        )

    original_deps_factory = cli_integration._build_pipeline_deps_for_remediation
    original_executor = effect_executor_module.execute_agent_effect
    original_inspect = effect_executor_module.inspect_integration_resolution
    cli_integration._build_pipeline_deps_for_remediation = lambda _lr, _dc: deps
    effect_executor_module.inspect_integration_resolution = lambda *_args: (
        IntegrationResolutionVerdict(RESOLVED)
    )

    def patched_executor(
        effect: object,
        config: object,
        pipeline_deps: object,
        workspace_scope: object,
        *args: object,
        **opts: object,
    ) -> PipelineEvent:
        from ralph.agents.invoke import AgentInvocationError

        opts_with_invoke = dict(opts)
        opts_with_invoke.setdefault("invoke_agent", fake_invoke_agent)
        opts_with_invoke.setdefault("agent_invocation_error", AgentInvocationError)
        return original_executor(
            effect,
            config,
            pipeline_deps,
            workspace_scope,
            *args,
            **opts_with_invoke,
        )

    effect_executor_module.execute_agent_effect = patched_executor
    try:
        rc = cli_integration.run_project_policy_readiness(
            load_result=load_result,
            display_context=display_context,
            workspace_factory=lambda: workspace,
            emit_factory=lambda _m: None,
            is_tty=lambda: False,
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor
        effect_executor_module.inspect_integration_resolution = original_inspect
        cli_integration._build_pipeline_deps_for_remediation = original_deps_factory

    calls: list[dict[str, object]] = list(deps.bridge_factory.calls)
    return rc, workspace, deps, calls, load_result


def _assert_ready_cache_written(tmp_git_repo: Path) -> None:
    from ralph.project_policy import markers as pp_markers

    cache_path = tmp_git_repo / pp_markers.CACHE_REL_PATH
    assert cache_path.exists(), (
        f"READY cache not written at {cache_path}; the run did not reach READY"
    )
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload.get("status") == "ready", (
        f"cache status is {payload.get('status')!r}, not 'ready'; the run did not reach READY"
    )


def _collect_retry_artifact_paths(tmp_git_repo: Path) -> list[str]:
    tmp_dir = tmp_git_repo / ".agent" / "tmp"
    if not tmp_dir.exists():
        return []
    return sorted(
        str(p.relative_to(tmp_git_repo)) for p in tmp_dir.glob("agent_retry_*.md") if p.is_file()
    )


def _read_analysis_artifact(tmp_git_repo: Path) -> str | None:
    path = tmp_git_repo / policy_analysis.ANALYSIS_ARTIFACT_REL_PATH
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


@pytest.mark.timeout_seconds(15)
def test_post_remediation_ready_route_ledger_records_executor_body_channels(
    tmp_git_repo: Path,
) -> None:
    from ralph.process.manager import get_process_manager

    pre_run_threads = {t.ident for t in threading.enumerate() if t.ident is not None}

    bundle = load_policy(default_dir())
    load_result = _build_load_result(tmp_git_repo, policy_bundle=bundle)
    initial_state_before = load_result.initial_state
    initial_state_id_before = id(initial_state_before)
    initial_state_phase_before = initial_state_before.phase

    pre_run_process_records = len(get_process_manager().list_active())

    rc, _workspace, _deps, bridge_calls, load_result = _run_ready_route(tmp_git_repo, load_result)

    _assert_ready_cache_written(tmp_git_repo)
    assert rc == 0, (
        f"ready-route preflight should exit 0; got rc={rc}. The real executor body "
        "may have failed before _finalize_ready_state ran."
    )

    initial_state_after = load_result.initial_state
    initial_state_id_after = id(initial_state_after)
    initial_state_phase_after = initial_state_after.phase

    _drained_check = apply_session_capture(initial_state_after)
    assert id(_drained_check) == initial_state_id_before, (
        "apply_session_capture returned a fresh state instead of "
        "mutating the live one in place; the hand-back contract is "
        "expecting identity-preserving replay"
    )

    leaked_session_id = pop_last_captured_session_id()
    print(f"channel=capture before=None after={leaked_session_id!r}")

    bridge_constructions = len(bridge_calls)
    bridge_shutdowns = _shutdown_counter["count"]
    print(f"channel=bridge before=0 after={bridge_constructions} shutdowns={bridge_shutdowns}")

    print(
        f"channel=initial_state_identity before={initial_state_id_before} "
        f"after={initial_state_id_after}"
    )

    print(
        f"channel=initial_state_phase before={initial_state_phase_before!r} "
        f"after={initial_state_phase_after!r}"
    )

    retry_artifacts = _collect_retry_artifact_paths(tmp_git_repo)
    print(f"channel=retry_artifacts before=[] after={retry_artifacts!r}")

    checkpoint_path = tmp_git_repo / ".agent" / "checkpoint.json"
    checkpoint_written = checkpoint_path.exists()
    print(f"channel=checkpoint before=absent after={checkpoint_written}")

    analysis_after = _read_analysis_artifact(tmp_git_repo)
    stale_marker_present = bool(
        analysis_after is not None and _POLICY_ANALYSIS_STALE_MARKER in analysis_after
    )
    print(
        f"channel=cleared_artifacts before={_POLICY_ANALYSIS_STALE_MARKER!r} "
        f"after=stale_marker_absent={not stale_marker_present}"
    )

    post_run_process_records = get_process_manager().list_active()
    print(
        f"channel=process_manager before={pre_run_process_records} "
        f"after={len(post_run_process_records)}"
    )

    new_thread_names = sorted(
        t.name
        for t in threading.enumerate()
        if t.ident is not None and t.ident not in pre_run_threads
    )
    print(f"channel=threads before=[] after={new_thread_names!r}")

    assert leaked_session_id is None, (
        f"capture channel: post-READY session id {leaked_session_id!r} not drained"
    )
    assert bridge_constructions == bridge_shutdowns, (
        f"bridge channel: {bridge_constructions} bridge(s) constructed but only "
        f"{bridge_shutdowns} shut down; the S-4 boundary must pair every "
        "construction with a shutdown before _finalize_ready_state returns"
    )
    assert initial_state_id_after == initial_state_id_before, (
        f"initial_state_identity channel: post-READY state has id "
        f"{initial_state_id_after}, expected pre-policy id "
        f"{initial_state_id_before}; the orchestrator must not mutate "
        "load_result.initial_state in place"
    )
    assert initial_state_phase_after == initial_state_phase_before, (
        f"initial_state_phase channel: post-READY state has phase "
        f"{initial_state_phase_after!r}, expected pre-policy phase "
        f"{initial_state_phase_before!r}"
    )
    assert retry_artifacts == [], (
        f"retry_artifacts channel: READY preflight left retry artifacts "
        f"{retry_artifacts!r}; _finalize_ready_state must drain every "
        "retry prompt the run wrote"
    )
    assert not checkpoint_written, (
        "checkpoint channel: .agent/checkpoint.json was written by the "
        "policy preflight; the pipeline runner owns the checkpoint, not "
        "the policy subsystem"
    )
    assert not stale_marker_present, (
        f"cleared_artifacts channel: post-READY analysis artifact at "
        f"{policy_analysis.ANALYSIS_ARTIFACT_REL_PATH} still carries the "
        f"stale marker {_POLICY_ANALYSIS_STALE_MARKER!r}; "
        "clear_phase_output_artifacts inside _start_bridge did not run "
        "for the policy_remediation_analysis drain"
    )
    assert post_run_process_records == [], (
        f"process_manager channel: READY preflight left active process "
        f"records {[r.label for r in post_run_process_records]!r}; "
        "the policy subsystem must not strand background processes"
    )
    assert new_thread_names == [], (
        f"threads channel: READY preflight left threads {new_thread_names!r}"
    )
