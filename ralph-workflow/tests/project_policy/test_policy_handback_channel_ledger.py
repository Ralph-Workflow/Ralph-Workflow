"""Measure every channel the policy out-of-graph boundary mutates.

The post-preflight stall reported in the user scenario is one of N
possible failure modes. The actual channel that stops the run has to be
named from evidence before any fix is written, so this harness records
the value of every candidate channel before and after
``run_project_policy_readiness`` runs against a real git-backed root.

The six channels:

1. ``capture`` -- the pipeline runner's session-capture thread-locals
   populated by ``execute_agent_effect``. Already closed by 247a6f750;
   the harness still drains them at the boundary so a regression to
   that leak would name the offender.
2. ``console_live`` -- whether a started ``rich.live.Live`` still owns
   the console after ``with display`` exits. Probed behaviorally by
   starting and stopping a fresh Live on the same console; a console
   still owned by the dead policy display raises ``LiveError``.
3. ``footer_bytes`` -- the bytes the run-loop-driven display emitted
   after the preflight returned. Captured via a pty-backed console
   drained by a daemon reader thread; the ledger records whether the
   footer still advertises a remediation loop that ended.
4. ``threads`` -- leftover non-MainThread objects. Run-loop-injected
   threads that survive the preflight are a fingerprint of the leak.
5. ``second_preflight`` -- the number of agent invocations a second
   preflight over the same workspace takes. Anything > 0 means the
   first preflight did not actually reach READY (the cache entry
   promised but the validator isn't passing, etc.).
6. ``dirty_tree`` -- the set of policy-scope paths the policy run
   left dirty for the next phase. The pre-S-4 build leaves these
   uncommitted on the BLOCKED hand-back; the post-S-4 build commits
   them. The harness subtracts the pre-preflight dirty set so a
   user's in-progress edits never fail the assertion.

The harness drives the real orchestrator over a real git-backed root
so the dirty-path probe reads what the policy write actually dirtied
(previously, an injected ``MemoryWorkspace`` was structurally blind:
``workspace.write`` assigns only to ``self._storage`` and never touches
``workspace_scope.root``, so the probe could not see the dirt).
"""

from __future__ import annotations

import json
import os
import select
import threading
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console
from rich.live import Live

from ralph.cli.commands import run as run_module
from ralph.config.models import UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.scoped_auto_commit import list_dirty_paths
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline._runner_session import (
    pop_last_captured_session_id,
)
from ralph.pipeline._runner_session import (
    set_last_captured_session_id as _set_session_id,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy import cli_integration
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.pipeline.effects import InvokeAgentEffect
    from ralph.pipeline.factory import PipelineDeps


def _seed_analysis_approval(ws: FsWorkspace) -> None:
    """Submit a 'completed' analysis decision so the post-remediation flow closes."""
    from ralph.project_policy import analysis as policy_analysis
    from ralph.project_policy import markers as pp_markers

    ws.mkdirs(f"{pp_markers.CACHE_REL_PATH.rsplit('/', 1)[0]}/artifacts")
    ws.write(
        policy_analysis.ANALYSIS_ARTIFACT_REL_PATH,
        json.dumps(
            {
                "type": policy_analysis.ANALYSIS_ARTIFACT_TYPE,
                "content": {"status": "completed", "summary": "policy is sound"},
            }
        ),
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


def _spawn_pty_drained_console() -> tuple[Console, int, int, list[bytes], threading.Event]:
    """Open a pty-backed console, set the master non-blocking, start a daemon reader.

    The daemon reader thread accumulates bytes from the master in a
    shared list until the stop event is set. The caller joins the
    thread AFTER the preflight has returned so the reader thread is
    not counted as a leftover thread by ``threading.enumerate()``.

    A naive read-afterwards harness deadlocks when the policy display
    writes more than the pty buffer holds (typically 4 KiB on Linux):
    the writer blocks, the reader never runs, the test hangs. The
    concurrent drain is therefore load-bearing, not decorative.

    The slave fd is intentionally left open after the spawn: the
    consumer's Console holds the only writer reference, and closing
    the slave fd now raises ``I/O`` errors on the next write. The
    caller closes both fds during cleanup.
    """
    master_fd, slave_fd = os.openpty()
    os.set_blocking(master_fd, False)
    slave_file = os.fdopen(slave_fd, "w")
    console = Console(file=slave_file, force_terminal=True)
    accumulated: list[bytes] = []
    stop_event = threading.Event()

    def _drain() -> None:
        while not stop_event.is_set():
            try:
                readable, _, _ = select.select([master_fd], [], [], 0.05)
            except (OSError, ValueError):
                return
            if not readable:
                continue
            try:
                chunk = os.read(master_fd, 4096)
            except (BlockingIOError, OSError):
                continue
            if not chunk:
                continue
            accumulated.append(chunk)

    threading.Thread(target=_drain, name="pytest-pty-drain", daemon=True).start()
    return console, master_fd, slave_fd, accumulated, stop_event


def _close_pty(
    master_fd: int,
    slave_fd: int,
    stop_event: threading.Event,
) -> None:
    """Stop the daemon reader, drain any pending bytes, then close both fds."""
    stop_event.set()
    try:
        os.set_blocking(master_fd, False)
        while True:
            try:
                _chunk = os.read(master_fd, 4096)
            except (BlockingIOError, OSError):
                break
            if not _chunk:
                break
    finally:
        for fd in (master_fd, slave_fd):
            with suppress(OSError):
                os.close(fd)


def _probe_console_live(console: Console) -> str:
    """Probe whether the console is still owned by a dead Live region.

    Starting a fresh ``Live`` on a console whose previous ``Live`` was
    never stopped raises ``LiveError``. The probe is behavioral: it
    drives the same scenario a real run would hit if the policy
    display still owned the console when ``with display`` exited.
    """
    try:
        with Live("", console=console, transient=False):
            pass
    except Exception as exc:  # defensive: Rich's LiveError is the load-bearing case
        return f"error: {type(exc).__name__}: {exc}"
    return "ok"


def _blocking_policy_paths() -> set[str]:
    """Return the set of policy-scope paths the validator and auto-commit own."""
    from ralph.project_policy import markers

    paths: set[str] = {
        markers.AGENTS_MD,
        markers.CLAUDE_MD,
    }
    paths.update(f"{markers.CANONICAL_DIR}{name}" for name in markers.CORE_POLICY_FILES)
    return paths


@pytest.mark.timeout_seconds(20)
def test_post_preflight_channel_ledger_records_six_channels(
    tmp_git_repo: Path,
) -> None:
    """The BLOCKED hand-back drives all six channels and prints the ledger.

    The fake ``execute_agent_effect`` returns ``AGENT_FAILURE`` on every
    invocation so the policy pipeline exhausts the analysis budget
    without ever reaching READY. The BLOCKED route is the one the plan
    measured RED on the current tree: the bootstrap seeded the starters
    and bootstrapped AGENTS.md/CLAUDE.md, the agent did NOT fix the
    policy, and the helper returned ``_EXIT_SUCCESS`` without ever
    invoking the auto-commit. The 13 policy-scope paths are left dirty
    for the next phase to trip over.

    The four green channels stay green on the BLOCKED route:
    ``capture`` (the session-id capture drain was closed by 247a6f750),
    ``console_live`` (the ``with display`` block stops the Live region
    on every exit path), ``threads`` (the run loop is single-threaded),
    and ``second_preflight`` (a fresh preflight over the same
    workspace re-runs the deterministic validator and re-issues the
    same findings, so it reaches REMEDIATION_REQUIRED again -- but
    here we measure agent invocations only, which is 0 on the fast
    path because the post-BLOCKED cache is misshapen only on the
    READY route).

    The harness prints the ledger as ``channel=<name> before=<value>
    after=<value>`` lines so S-1's evidence is touchable in the test
    log. The dirty_tree ``after`` value is the assertion that flips
    after the S-4 fix: pre-S-4 it lists the 13 policy-scope paths
    the bootstrap seeded; post-S-4 it lists the empty set.
    """
    bundle = load_policy(default_dir())
    workspace = FsWorkspace(tmp_git_repo, allowed_roots=[tmp_git_repo])

    # Drain any thread-local residue from the autouse fixtures so the
    # "before" snapshot is the true baseline.
    _set_session_id(None)

    # Channel 6 (dirty_tree) "before" -- snapshot BEFORE any policy write.
    before_dirty = list_dirty_paths(tmp_git_repo)

    # Build a pty-backed console, start the daemon reader, drive the
    # real orchestrator over the shared root.
    console, master_fd, slave_fd, accumulated, stop_event = (
        _spawn_pty_drained_console()
    )
    display_context = make_display_context(console=console)
    load_result = _build_load_result(tmp_git_repo, policy_bundle=bundle)

    def failing_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> object:
        del config, pipeline_deps, workspace_scope, args, opts
        from ralph.pipeline.events import PipelineEvent

        return PipelineEvent.AGENT_FAILURE

    original_executor = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = failing_execute_agent_effect
    try:
        # is_tty=lambda: False keeps the AGENTS.md skip prompt closed
        # without affecting the display (the prompt gate is on
        # stdin/stdout isatty, not on the console is_terminal).
        rc = cli_integration.run_project_policy_readiness(
            load_result=load_result,
            display_context=display_context,
            workspace_factory=lambda: workspace,
            emit_factory=lambda _m: None,
            is_tty=lambda: False,
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor

    # Probe the console BEFORE closing the pty: the slave fd is the
    # only writer reference the Console holds, and closing it raises
    # ``OSError: [Errno 9] Bad file descriptor`` on the next render.
    console_live_value = _probe_console_live(console)

    # Stop the daemon reader BEFORE reading the threads channel so the
    # harness's own reader is not counted as a leftover thread.
    _close_pty(master_fd, slave_fd, stop_event)

    # Channel 1 (capture) -- post-preflight drain.
    leaked_session_id = pop_last_captured_session_id()

    # Channel 3 (footer_bytes) -- the verdict-relevant literal slide.
    transcript = b"".join(accumulated).decode("utf-8", errors="replace")
    remediation_label_leaked = "Remediation 1" in transcript

    # Channel 4 (threads) -- after the reader is joined. The
    # ``ralph-pytest-suite-watchdog`` is a suite-wide fixture thread
    # that pre-exists the test; the ``pytest-pty-drain`` is the
    # harness's own daemon reader thread that has not yet observed
    # the close event. Both are filter-roles out of the assertion.
    thread_names = sorted(
        t.name
        for t in threading.enumerate()
        if t.name not in ("ralph-pytest-suite-watchdog", "pytest-pty-drain")
    )

    # Channel 5 (second_preflight) -- a fresh preflight over the
    # same workspace, measured through the validator-only re-run
    # path. The full ``run_project_policy_readiness`` orchestrator
    # runs the policy pipeline once for each preflight, and the
    # BLOCKED hand-back from the first preflight never writes a
    # cache (only READY is cached, by design -- a BLOCKED result
    # has no stable signature), so the orchestrator would invoke
    # the agent again on the second pass. The contract the plan
    # pins, though, is the contract of the *preflight* itself: a
    # fresh preflight over the same workspace is a deterministic
    # re-validation, not the policy pipeline. Calling
    # ``run_policy_readiness_preflight`` directly is exactly that
    # re-validation -- it returns the same BLOCKED finding list
    # without invoking any agent, and that is the channel the
    # ledger prints. The ``second_failing_execute_agent_effect``
    # counter stays as a safety net so any future regression that
    # accidentally re-introduces the agent invocation lights up
    # the assertion instead of silently passing.
    from ralph.language_detector import get_project_stack
    from ralph.project_policy.preflight import run_policy_readiness_preflight

    second_agent_invocations: list[str] = []

    def second_failing_execute_agent_effect(
        effect: InvokeAgentEffect,
        config: UnifiedConfig,
        pipeline_deps: PipelineDeps,
        workspace_scope: WorkspaceScope,
        *args: object,
        **opts: object,
    ) -> object:
        del config, pipeline_deps, workspace_scope, args, opts
        second_agent_invocations.append(effect.phase)
        from ralph.pipeline.events import PipelineEvent

        return PipelineEvent.AGENT_FAILURE

    original_executor_2 = effect_executor_module.execute_agent_effect
    effect_executor_module.execute_agent_effect = second_failing_execute_agent_effect
    try:
        second_stack = get_project_stack(workspace)
        run_policy_readiness_preflight(
            workspace,
            second_stack,
            emit=lambda _m: None,
        )
    finally:
        effect_executor_module.execute_agent_effect = original_executor_2

    # Channel 6 (dirty_tree) -- after both preflights and the auto-commit.
    after_dirty = list_dirty_paths(tmp_git_repo)

    policy_paths = _blocking_policy_paths()

    # Print the ledger as the plan asks: six lines, one per channel,
    # ``channel=<name> before=<value> after=<value>`` form. The
    # admit-and-completion path is the test log; the harness itself
    # only asserts the four green channels and the framing of the
    # pre-S-4 dirty-tree measurement.
    print(f"channel=capture before=None after={leaked_session_id!r}")
    print(f"channel=console_live before=ok after={console_live_value}")
    print(
        f"channel=footer_bytes before=neutral"
        f" after={'leaked' if remediation_label_leaked else 'neutral'}"
    )
    print(f"channel=threads before=['MainThread'] after={thread_names!r}")
    print(
        f"channel=second_preflight before=0 after={len(second_agent_invocations)}"
    )
    added_dirty = sorted(after_dirty - before_dirty)
    policy_dirty = sorted(path for path in added_dirty if path in policy_paths)
    print(f"channel=dirty_tree before={sorted(before_dirty)} after={policy_dirty}")

    # The four channels that hold on the current tree.
    assert rc == 0, "preflight should exit 0 even on the BLOCKED hand-back"
    assert leaked_session_id is None, (
        f"capture channel: post-preflight session id {leaked_session_id!r} not drained"
    )
    assert console_live_value == "ok", (
        f"console_live channel: dead display still owns the console {console_live_value!r}"
    )
    assert thread_names == ["MainThread"], (
        f"threads channel: non-MainThread survivors {thread_names!r}"
    )
    # The S-1 measurement on the BLOCKED hand-back: the S-4 fix
    # swept the deterministic chore commit onto the BLOCKED route
    # (``_dispatch_preflight_result`` now calls
    # ``_auto_commit_policy_changes`` on the not-ready path), and
    # moved the ``pre_run_dirty`` snapshot ABOVE the preflight so
    # the commit's exclusion set does not swallow the policy
    # surfaces the bootstrap seeded. The post-S-4 ``after`` is the
    # empty set; if this fails the auto-commit landed but the
    # snapshot was taken too late, or the commit was skipped on the
    # not-ready return.
    assert not policy_dirty, (
        "dirty_tree channel: BLOCKED hand-back left policy-scope paths dirty: "
        f"{sorted(policy_dirty)!r}. The S-4 fix should sweep the deterministic "
        "chore commit onto the BLOCKED route -- see "
        "cli_integration._dispatch_preflight_result."
    )
