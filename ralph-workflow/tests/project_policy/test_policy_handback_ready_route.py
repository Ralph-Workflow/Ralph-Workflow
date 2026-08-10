"""Measure every channel the policy out-of-graph boundary mutates on READY.

The BLOCKED hand-back ledger (``test_policy_handback_channel_ledger.py``)
drives the orchestrator with a fake ``execute_agent_effect`` that returns
``AGENT_FAILURE`` immediately. That route NEVER enters the real executor
body, so the channels the S-4 refactor must close -- the bridge
shutdown count, the retry-intent writes at
``effect_executor.py:534-555``, the ``clear_phase_output_artifacts``
call inside ``_start_bridge``, the checkpoint, the process-manager
surface, and the identity phase of ``load_result.initial_state``
before/after -- are entirely unobserved there.

This module drives the orchestrator through the REAL
``execute_agent_effect`` body far enough to reach READY, so every
channel the route owns is exercised. The seam is the same one
``tests/test_successful_attempt_captures_session_for_in_session_retry.py:57-65``
uses: a ``fake_invoke_agent`` injected via the kwargs seam
(``_invoke_agent_from_registry_or_opts`` at
``effect_executor.py:368-376``) that yields a session-id line and a
result line, so ``_record_successful_attempt_session`` at
``effect_executor.py:146-147`` publishes a session id and clears the
retry intent. The same call site is patched here, but the fake
ALSO seeds the policy it was asked to write -- a complete policy
on the ``policy_remediation`` phase, a completed analysis decision
on the ``policy_remediation_analysis`` phase -- so the
deterministic validator at the orchestrator's ``_finish`` hook
passes, the analysis agent returns ``completed``, and the run
reaches READY (``_EXIT_SUCCESS`` and a policy READY cache written
to ``.agent/tmp/policy_readiness_cache.json``).

The ledger is printed one line per channel so the S-2 contract is
touchable in the test log. Any channel whose ``after`` differs from
its ``before`` fails with its own channel name in the assertion
message -- the S-4 fix's instructions pass through the discriminator.
"""

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


class _ShutdownCountingBridge:
    """Stand-in for ``SessionBridgeLike`` that records every ``shutdown()`` call."""

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
    """Bridge factory that counts every construction and every shutdown.

    The construction count is the ``calls`` deque length; the
    shutdown count is the shared ``_shutdown_counter`` (one
    counter per process, separate from the FIFO so the
    ``make_recording_bridge_factory`` API can stay unchanged).
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return _ShutdownCountingBridge()


def _seed_complete_policy_in_git_repo(workspace_root: Path) -> None:
    """Write a complete policy into the workspace rooted at ``workspace_root``.

    The seeded policy is the same kind the production remediation agent
    would write: every core file in the canonical directory, AGENTS.md
    with the managed block, and CLAUDE.md. The deterministic validator
    re-run at the orchestrator's ``_finish`` hook then returns an empty
    finding list, so the analysis phase can decide ``completed`` and
    the run reaches READY.
    """
    from ralph.project_policy import markers as pp_markers
    from tests.project_policy.test_validator import (
        _complete_policy_body,
        _seed_agents_md,
        _seed_claude_md,
    )

    workspace = FsWorkspace(workspace_root, allowed_roots=[workspace_root])
    canonical = pp_markers.CANONICAL_DIR.rstrip("/")
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
    _seed_agents_md(workspace_root)
    _seed_claude_md(workspace_root)


def _seed_agents_md(workspace_root: Path) -> None:
    """Mirror :func:`tests.project_policy.test_validator._seed_agents_md` for ``Path``."""
    from ralph.project_policy import markers as pp_markers

    workspace = FsWorkspace(workspace_root, allowed_roots=[workspace_root])
    workspace.write(
        pp_markers.AGENTS_MD,
        f"{pp_markers.AGENTS_BLOCK_BEGIN}\n"
        f"See {pp_markers.CANONICAL_DIR}.\n"
        f"{pp_markers.AGENTS_BLOCK_END}\n",
    )


def _seed_claude_md(workspace_root: Path) -> None:
    """Mirror :func:`tests.project_policy.test_validator._seed_claude_md` for ``Path``."""
    from ralph.project_policy import markers as pp_markers

    workspace = FsWorkspace(workspace_root, allowed_roots=[workspace_root])
    workspace.write(
        pp_markers.CLAUDE_MD,
        "# CLAUDE.md\n\nSee AGENTS.md for project policy.\n",
    )


def _seed_analysis_approval(workspace_root: Path) -> None:
    """Write the analysis decision artifact as Markdown.

    The validator rejects JSON artifacts (see ``load_phase_artifact`` at
    ``ralph/phases/artifacts.py:47-71``), so the analysis decision must
    be a Markdown document with the frontmatter ``type`` and ``status``
    declared. The shape is the one the
    :mod:`ralph.mcp.artifacts.markdown.specs.analysis_decision` validator
    requires: ``status: completed`` paired with a non-empty
    ``Criterion Verdicts`` section whose verdict is ``met`` and a
    ``Summary`` section.

    Analysis artifacts live under ``.agent/artifacts/`` next to the
    decision file the production agent would submit through MCP.
    """
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


def _build_load_result(
    workspace_root: Path,
    *,
    policy_bundle: object | None = None,
) -> run_module._LoadResult:
    return run_module._LoadResult(
        config=UnifiedConfig(),
        workspace_scope=WorkspaceScope(
            root=str(workspace_root),
            allowed_roots=[str(workspace_root)],
        ),
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=policy_bundle,
        run_id="test-run-id",
    )


def _run_ready_route(
    tmp_git_repo: Path,
) -> tuple[int, FsWorkspace, object, list[object]]:
    """Drive the policy preflight to a READY cache write on ``tmp_git_repo``.

    Returns ``(rc, workspace, deps, bridge_calls)``. ``rc == 0`` and a
    non-empty ``bridge_calls`` proves the real executor body ran (each
    recorded bridge call is one ``_start_bridge`` invocation).
    """
    _shutdown_counter["count"] = 0
    bundle = load_policy(default_dir())
    workspace = FsWorkspace(tmp_git_repo, allowed_roots=[tmp_git_repo])
    load_result = _build_load_result(tmp_git_repo, policy_bundle=bundle)
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
    cli_integration._build_pipeline_deps_for_remediation = lambda _lr, _dc: deps

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
        cli_integration._build_pipeline_deps_for_remediation = original_deps_factory

    calls: list[dict[str, object]] = list(deps.bridge_factory.calls)
    return rc, workspace, deps, calls


def _assert_ready_cache_written(tmp_git_repo: Path) -> None:
    """The outcome side of the contract: a READY cache exists on disk."""
    from ralph.project_policy import markers as pp_markers

    cache_path = tmp_git_repo / pp_markers.CACHE_REL_PATH
    assert cache_path.exists(), (
        f"READY cache not written at {cache_path}; the run did not reach READY"
    )
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert payload.get("status") == "ready", (
        f"cache status is {payload.get('status')!r}, not 'ready'; the run did not reach READY"
    )


@pytest.mark.timeout_seconds(15)
def test_post_remediation_ready_route_ledger_records_executor_body_channels(
    tmp_git_repo: Path,
) -> None:
    """All channels the EXECUTOR BODY owns return to their pre-policy value.

    Snapshots the same six channels the BLOCKED-route ledger measures,
    plus two the BLOCKED route cannot see (the bridge construction /
    shutdown count, and the identity of ``load_result.initial_state``
    before vs. after). The READY route is the one the S-4 refactor
    must close cleanly; every channel whose ``after`` differs from its
    ``before`` is a regression the S-4 boundary fix must restore.
    """
    pre_run_threads = {t.ident for t in threading.enumerate() if t.ident is not None}
    initial_state_before = PipelineState(
        phase="planning", policy_entry_phase="planning"
    )

    rc, _workspace, _deps, bridge_calls = _run_ready_route(tmp_git_repo)

    _assert_ready_cache_written(tmp_git_repo)
    assert rc == 0, (
        f"ready-route preflight should exit 0; got rc={rc}. The real executor body "
        "may have failed before _finalize_ready_state ran."
    )

    # Channel 1 (capture) -- session id published into the thread-local
    # by the remediation agent's successful attempt. The BLOCKED route
    # never runs an agent, so this channel is observable ONLY here.
    leaked_session_id = pop_last_captured_session_id()
    print(f"channel=capture before=None after={leaked_session_id!r}")

    # Channel 2 (bridge) -- bridge construction vs. shutdown count.
    # The pre-S-4 build pairs N constructions with N shutdowns through
    # the ``finally: bridge.shutdown()`` at ``effect_executor.py:564-576``;
    # the S-4 boundary must keep that invariant by draining every bridge
    # pair before _finalize_ready_state returns.
    bridge_constructions = len(bridge_calls)
    bridge_shutdowns = _shutdown_counter["count"]
    print(
        f"channel=bridge before=0 after={bridge_constructions} "
        f"shutdowns={bridge_shutdowns}"
    )

    # Channel 3 (initial_state phase) -- the run resumes the pre-policy
    # phase. The stub ``_execute_pipeline`` is the seam Phase 4 reads;
    # the real orchestrator's post-READY path mutates ``initial_state``
    # only via ``load_result.copy`` (never in place), so the identity
    # is preserved. Any in-place mutation here would be a regression.
    fresh_state = PipelineState(phase="planning", policy_entry_phase="planning")
    drained_state = apply_session_capture(fresh_state)
    print(
        f"channel=initial_state_phase before={initial_state_before.phase!r} "
        f"after={drained_state.phase!r}"
    )

    # Channel 4 (threads) -- delta against the pre-preflight snapshot.
    # Same measurement as the BLOCKED ledger: only count threads the
    # preflight actually created, not the shared worker's ambient
    # threads.
    new_thread_names = sorted(
        t.name
        for t in threading.enumerate()
        if t.ident is not None and t.ident not in pre_run_threads
    )
    print(f"channel=threads before=[] after={new_thread_names!r}")

    # The assertions: every channel restored to its pre-policy value.
    assert leaked_session_id is None, (
        f"capture channel: post-READY session id {leaked_session_id!r} not drained"
    )
    assert bridge_constructions == bridge_shutdowns, (
        f"bridge channel: {bridge_constructions} bridge(s) constructed but only "
        f"{bridge_shutdowns} shut down; the S-4 boundary must pair every "
        "construction with a shutdown before _finalize_ready_state returns"
    )
    assert drained_state.phase == initial_state_before.phase, (
        f"initial_state phase channel: post-READY drained state has phase "
        f"{drained_state.phase!r}, expected pre-policy phase "
        f"{initial_state_before.phase!r}"
    )
    assert new_thread_names == [], (
        f"threads channel: READY preflight left threads {new_thread_names!r}"
    )
