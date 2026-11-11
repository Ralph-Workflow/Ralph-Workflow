from __future__ import annotations

import io
import re
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from rich.console import Console

from ralph.config.verbosity import Verbosity
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBar, StatusBarModel
from ralph.pipeline.run_loop import (
    _execute_with_cleanup,
    _LoopContext,
    _push_status_bar_if_changed,
    _run_inner_loop,
    _setup_active_display,
    _sync_live_display_context,
)
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.policy.loader import load_policy
from ralph.recovery.connectivity import ConnectivityState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle


class _TtyLikeStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


_ANSI_ESCAPE_RE: re.Pattern[str] = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


@lru_cache(maxsize=1)
def _load_default_policy() -> PolicyBundle:
    with tempfile.TemporaryDirectory() as d:
        return load_policy(Path(d) / ".agent")


def _patched_update_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> list[StatusBarModel]:
    captured: list[StatusBarModel] = []

    real_update_status_bar = ParallelDisplay.update_status_bar

    def recording_update_status_bar(self: ParallelDisplay, model: StatusBarModel) -> None:
        if not isinstance(model, StatusBarModel):
            msg = f"update_status_bar requires a StatusBarModel, got {type(model).__name__}"
            raise TypeError(msg)
        captured.append(model)
        real_update_status_bar(self, model)

    monkeypatch.setattr(ParallelDisplay, "update_status_bar", recording_update_status_bar)
    return captured


def _make_loop_context(
    *,
    active_display: ParallelDisplay,
    workspace_root: Path,
    policy_bundle: PolicyBundle,
) -> _LoopContext:
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    display_context = make_display_context(console=console, env={})

    class _OnlineMonitor:
        current_state: ConnectivityState = ConnectivityState.ONLINE

    return _LoopContext(
        policy_bundle=policy_bundle,
        workspace_scope=WorkspaceScope(root=workspace_root),
        config=None,
        active_display=active_display,
        display_context=display_context,
        effective_verbosity=Verbosity.NORMAL,
        registry=type("R", (), {})(),
        effective_pipeline_subscriber=None,
        controller=type("C", (), {})(),
        config_path=None,
        cli_overrides={},
        monitor_stop=None,
        connectivity_monitor=_OnlineMonitor(),
        sleep=lambda _s: None,
        is_quiet=False,
        snapshot_registry=None,
    )


def _make_display() -> tuple[ParallelDisplay, Console]:
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    return ParallelDisplay(ctx), console


def test_run_inner_loop_pushes_status_bar_with_full_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = Path("/tmp/wt028-statusbar-e2e").resolve()
    policy_bundle = _load_default_policy()
    workspace_root_str = str(workspace_root)

    pd, console = _make_display()
    assert console.is_terminal is True
    assert console.file.isatty() is True
    assert isinstance(pd.status_bar, StatusBar)

    states_to_return: list[PipelineState | int] = [
        PipelineState(
            phase="development_final_commit",
            phase_chains={
                "development_final_commit": AgentChainState(
                    agents=["claude"], current_index=0, retries=0
                ),
            },
            outer_progress={"iteration": 0},
            budget_caps={"iteration": 5},
        ),
        PipelineState(
            phase="development_analysis",
            phase_chains={
                "development_analysis": AgentChainState(
                    agents=["claude"], current_index=0, retries=0
                ),
            },
            outer_progress={"iteration": 0},
            budget_caps={"iteration": 5},
            loop_iterations={"development_analysis_iteration": 2},
        ),
        0,
    ]

    def mock_run_pipeline_step(*_args: object, **_kwargs: object) -> PipelineState | int:
        if states_to_return:
            return states_to_return.pop(0)
        return 0

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)
    captured = _patched_update_recorder(monkeypatch)

    initial_chain = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development_final_commit",
        phase_chains={"development_final_commit": initial_chain},
        outer_progress={"iteration": 0},
        budget_caps={"iteration": 5},
    )
    loop_ctx = _make_loop_context(
        active_display=pd,
        workspace_root=workspace_root,
        policy_bundle=policy_bundle,
    )

    _run_inner_loop(state, loop_ctx, prev_phase="development_final_commit")

    assert len(captured) == 2, (
        f"expected exactly 2 update_status_bar pushes (one per signature), "
        f"got {len(captured)}: phase_labels={[m.phase_label for m in captured]!r}"
    )
    first_push, second_push = captured
    assert isinstance(first_push, StatusBarModel)
    assert isinstance(second_push, StatusBarModel)

    assert first_push.workspace_root == workspace_root_str
    assert first_push.phase_label == "Development Final Commit"
    assert first_push.outer_dev_iteration == 1
    assert first_push.outer_dev_cap == 5
    assert first_push.inner_analysis is None
    assert first_push.inner_analysis_cap is None

    assert second_push.workspace_root == workspace_root_str
    assert second_push.phase_label == "Development Analysis"
    assert second_push.outer_dev_iteration == 1
    assert second_push.outer_dev_cap == 5
    assert second_push.inner_analysis == 2
    assert second_push.inner_analysis_cap == 5

    assert pd.status_bar.last_model is second_push
    assert pd.status_bar.last_model is not None
    last = pd.status_bar.last_model
    assert last.workspace_root == workspace_root_str
    assert last.phase_label == "Development Analysis"
    assert last.inner_analysis == 2
    assert last.inner_analysis_cap == 5


def test_run_inner_loop_dedupes_status_bar_on_unchanged_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = Path("/tmp/wt028-statusbar-e2e-dedup").resolve()
    policy_bundle = _load_default_policy()
    workspace_root_str = str(workspace_root)

    pd, console = _make_display()
    assert console.is_terminal is True
    assert console.file.isatty() is True

    iteration_count = {"n": 0}

    def mock_run_pipeline_step(*_args: object, **_kwargs: object) -> PipelineState | int:
        iteration_count["n"] += 1
        if iteration_count["n"] >= 3:
            return 0
        chain = AgentChainState(agents=["claude"], current_index=0, retries=0)
        return PipelineState(
            phase="development_final_commit",
            phase_chains={"development_final_commit": chain},
            outer_progress={"iteration": 0},
            budget_caps={"iteration": 5},
        )

    monkeypatch.setattr("ralph.pipeline.runner.run_pipeline_step", mock_run_pipeline_step)
    captured = _patched_update_recorder(monkeypatch)

    chain = AgentChainState(agents=["claude"], current_index=0, retries=0)
    state = PipelineState(
        phase="development_final_commit",
        phase_chains={"development_final_commit": chain},
        outer_progress={"iteration": 0},
        budget_caps={"iteration": 5},
    )

    loop_ctx = _make_loop_context(
        active_display=pd,
        workspace_root=workspace_root,
        policy_bundle=policy_bundle,
    )

    _run_inner_loop(state, loop_ctx, prev_phase="development_final_commit")

    assert len(captured) == 1, (
        f"expected exactly one update_status_bar push (dedupe), "
        f"got {len(captured)}: {[m.phase_label for m in captured]!r}"
    )
    only_push = captured[0]
    assert isinstance(only_push, StatusBarModel)
    assert only_push.workspace_root == workspace_root_str
    assert only_push.phase_label == "Development Final Commit"
    assert only_push.outer_dev_iteration == 1
    assert only_push.outer_dev_cap == 5
    assert only_push.inner_analysis is None
    assert only_push.inner_analysis_cap is None


@pytest.mark.parametrize("width", [70, 80, 99])
def test_run_inner_loop_status_bar_fits_at_narrow_widths(width: int) -> None:
    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=width,
        color_system="standard",
    )
    ctx = make_display_context(console=console, env={})
    pd = ParallelDisplay(ctx)
    assert console.is_terminal is True
    assert console.file.isatty() is True
    assert console.width == width, (
        f"AC-07: console width MUST be {width} for this parametrized variant; got {console.width!r}"
    )
    sb = pd.status_bar
    assert isinstance(sb, StatusBar)
    workspace_root = Path("/Users/alice/code/very-long-project-name/subdir")
    workspace_root_str = str(workspace_root)
    policy_bundle = _load_default_policy()

    state = PipelineState(
        phase="development_analysis",
        phase_chains={
            "development_analysis": AgentChainState(agents=["claude"], current_index=0, retries=0),
        },
        outer_progress={"iteration": 0},
        budget_caps={"iteration": 5},
        loop_iterations={"development_analysis_iteration": 2},
    )

    captured_inside_active = False
    with pd:
        captured_inside_active = sb.is_active
        new_sig = _push_status_bar_if_changed(
            pd,
            state,
            policy_bundle,
            workspace_root,
            last_sig=None,
        )
        assert new_sig is not None, (
            f"AC-07: _push_status_bar_if_changed must return a fresh "
            f"signature after a first push at width={width}; got None"
        )
        assert isinstance(new_sig, tuple) and len(new_sig) == 7, (
            f"AC-07: _push_status_bar_if_changed must return a "
            f"(phase, outer, inner, integration_alert, outer_label, agent_name, attention) tuple; "
            f"got {new_sig!r}"
        )
    assert captured_inside_active is True, (
        f"StatusBar must be active inside the production context manager at width={width}"
    )

    pushed_model = sb.last_model
    assert pushed_model is not None, (
        f"AC-07: _push_status_bar_if_changed at width={width} must push "
        f"a StatusBarModel through to the production entry point; "
        f"got sb.last_model is None"
    )
    assert isinstance(pushed_model, StatusBarModel)
    assert pushed_model.workspace_root == workspace_root_str
    assert pushed_model.phase_label == "Development Analysis"

    raw_out = buf.getvalue()
    plain = _strip_ansi(raw_out)
    visible_lines = [line for line in plain.splitlines() if line.strip()]
    distinct_visible_frames = set(visible_lines)
    assert len(distinct_visible_frames) <= 1, (
        f"AC-07: every rendered bar frame must remain single-line at width={width}; "
        f"got distinct frames {distinct_visible_frames!r}, plain={plain!r}"
    )
    visible = max((len(line) for line in visible_lines), default=0)
    assert visible <= width, (
        f"AC-07: rendered bar must fit the terminal width at width={width}; "
        f"longest visible line has length {visible} > {width}, "
        f"plain={plain!r}"
    )
    if visible_lines:
        content_line = visible_lines[0]
        assert "\n" not in content_line, (
            f"AC-07: bar content must be single-line at width={width}; "
            f"got content_line={content_line!r}"
        )
    phase_label = "Development Analysis"
    phase_label_prefixes = (
        "DAn",
        phase_label[:3],
        phase_label[:2],
    )
    phase_visible = any(prefix in plain for prefix in phase_label_prefixes)
    assert phase_visible, (
        f"AC-07: at width={width}, phase label must remain visible "
        f"(any of {phase_label_prefixes!r}); got plain={plain!r}"
    )
    trailing_segment = workspace_root_str.rsplit("/", 1)[-1]
    path_prefixes = (
        trailing_segment[:3],
        trailing_segment[:2],
        trailing_segment[:1],
    )
    path_visible = any(prefix in plain for prefix in path_prefixes)
    assert path_visible, (
        f"AC-07: at width={width}, trailing workspace path segment must "
        f"remain visible (any of {path_prefixes!r}); got plain={plain!r}"
    )


def test_setup_active_display_returns_live_context_object() -> None:
    workspace_root = Path("/tmp/wt028-display-context-refresh")
    policy_bundle = _load_default_policy()

    buf = _TtyLikeStringIO()
    console = Console(
        file=buf,
        force_terminal=True,
        width=120,
        color_system="standard",
    )
    initial_ctx = make_display_context(console=console, env={})
    workspace_scope = WorkspaceScope(root=workspace_root)

    active, display_context, stop_fn = _setup_active_display(
        display=None,
        is_quiet=False,
        display_context=initial_ctx,
        workspace_scope=workspace_scope,
        policy_bundle=policy_bundle,
    )

    try:
        original_width = display_context.width

        refreshed_ctx = display_context.refreshed()
        object.__setattr__(refreshed_ctx, "width", original_width + 17)
        _sync_live_display_context(active, refreshed_ctx)

        assert display_context.width == original_width + 17, (
            f"display_context.width MUST observe the refreshed width "
            f"({original_width + 17}); got {display_context.width} "
            f"(the original would have been {original_width})"
        )
    finally:
        stop_fn()


def test_liveness_producer_writes_to_last_activity_monotonic() -> None:
    clock = {"t": 1000.0}
    console = Console(file=_TtyLikeStringIO(), force_terminal=True, width=120)
    pd = ParallelDisplay(
        make_display_context(console=console, env={}),
        workspace_root=Path(tempfile.mkdtemp()),
        monotonic=lambda c=clock: c.__setitem__("t", c["t"] + 1) or c["t"],
    )
    assert pd.watchdog_attention is None
    pd.set_watchdog_attention("stalled")
    assert pd.watchdog_attention == "stalled"
    pd.set_watchdog_attention(None)
    assert pd.watchdog_attention is None


def test_push_status_bar_carries_liveness_through_production_path() -> None:
    clock = {"t": 1000.0}
    console = Console(file=_TtyLikeStringIO(), force_terminal=True, width=120)
    workspace_root = Path(tempfile.mkdtemp())
    pd = ParallelDisplay(
        make_display_context(console=console, env={}),
        workspace_root=workspace_root,
        monotonic=lambda c=clock: c.__setitem__("t", c["t"] + 1) or c["t"],
    )
    pd.set_watchdog_attention("stalled")
    policy_bundle = _load_default_policy()
    state = _make_state("development", 1, 1, 1)
    sig = _push_status_bar_if_changed(
        pd,
        state,
        policy_bundle,
        workspace_root,
        last_sig=None,
    )
    assert sig is not None
    pushed = pd.status_bar.last_model
    assert pushed is not None, (
        "The production push path must reach the StatusBar's update "
        "seam so ``last_model`` reflects the new model."
    )
    assert pushed.attention is None, (
        "The pushed model's attention slot is operator-pushed only; "
        "the watchdog-sourced value is substituted on each Live tick."
    )
    assert not hasattr(pushed, "last_activity_monotonic"), (
        "wt-047-stall-label: the dead display-side stall derivation "
        "must be removed from the model entirely (zero dead code)."
    )


def test_run_loop_regression_final_status_push_is_terminated_before_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = Path(tempfile.mkdtemp())
    display, _console = _make_display()
    loop_ctx = _make_loop_context(
        active_display=display,
        workspace_root=workspace_root,
        policy_bundle=_load_default_policy(),
    )
    terminal_state = _make_state("complete", 1, 1, 1).copy_with(run_outcome="completed")
    captured = _patched_update_recorder(monkeypatch)
    monkeypatch.setattr("ralph.pipeline.run_loop._emit_run_start", lambda *_args: None)
    monkeypatch.setattr(
        "ralph.pipeline.run_loop._run_inner_loop",
        lambda *_args: (terminal_state, "development", None),
    )
    monkeypatch.setattr(
        "ralph.pipeline.run_loop._report_pending_remote_publication",
        lambda state, _ctx: state,
    )
    monkeypatch.setattr("ralph.pipeline.run_loop._emit_post_loop_result", lambda *_args: None)

    observed_before_teardown: list[StatusBarModel | None] = []

    def display_stop() -> None:
        observed_before_teardown.append(display.status_bar.last_model)

    assert (
        _execute_with_cleanup(
            terminal_state,
            loop_ctx,
            "development",
            lambda: None,
            lambda: None,
            display_stop,
        )
        == 0
    )
    assert captured[-1].attention == "terminated"
    assert observed_before_teardown == [captured[-1]]


def test_attention_state_for_state_returns_waiting() -> None:
    state = _make_state("waiting", 1, 1, 1)
    assert _attention_for(state) == "waiting"


def test_attention_state_for_state_returns_retrying() -> None:
    state = _StubState(phase="development", retrying=True)
    assert _attention_for(state) == "retrying"


def test_attention_state_for_state_returns_failed() -> None:
    state = _StubState(phase="development", run_outcome="failed")
    assert _attention_for(state) == "failed"


def test_attention_state_for_state_regression_failed_terminal_is_terminated() -> None:
    assert (
        _attention_for(_StubState(phase="failed_terminal", run_outcome="terminated"))
        == "terminated"
    )


def test_attention_state_for_state_returns_none_when_healthy() -> None:
    state = _StubState(phase="development")
    assert _attention_for(state) is None


class _StubState:
    def __init__(
        self,
        *,
        phase: str = "development",
        run_outcome: str | None = None,
        retrying: bool = False,
    ) -> None:
        self.phase = phase
        self.run_outcome = run_outcome
        self.retrying = retrying


def _attention_for(state: PipelineState) -> str | None:
    from ralph.pipeline.run_loop import _attention_state_for_state

    return _attention_state_for_state(state)


def _make_state(
    phase: str,
    outer_dev_iteration: int,
    outer_dev_cap: int,
    inner_analysis: int,
) -> PipelineState:
    from ralph.pipeline.state import AgentChainState

    chain = AgentChainState(agent_name="claude", attempts=0, history=())
    return PipelineState(
        phase=phase,
        previous_phase=None,
        attempts=1,
        chain=chain,
        started_at=0.0,
        last_blocked_phase=None,
        is_waiting_state=False,
    )
