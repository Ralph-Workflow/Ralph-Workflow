"""Regression tests for surfacing auto-integrate outcomes in the completion receipt.

These tests pin the display contract for the four auto-integrate outcome
fields projected from :class:`ralph.pipeline.rebase_state.RebaseState` onto
:data:`ralph.display.snapshot.PipelineSnapshot`. The producer
(:mod:`ralph.pipeline.auto_integrate`) keeps ``last_action`` as one of
``rebased|merged|skipped|conflict|recovered`` and records the landing
result on the ``fast_forwarded`` boolean. The action vocabulary contains
no ``fast_forwarded`` verb at all, so the display layer must read
``fast_forwarded`` rather than keying on a nonexistent action.

Every case below is a tuple actually emitted by the producer; nothing here
is invented. Both the plain renderer and the on-screen group renderer must
agree, and the disabled path (no integration ran) must emit nothing.

These are pure in-memory unit tests; they never touch the filesystem,
spawn subprocesses, or call :func:`time.sleep`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

from rich.console import Console

from ralph.display.completion_summary import (
    CompletionSummaryOptions,
    render_completion_summary,
    render_completion_summary_group,
)
from ralph.display.context import make_display_context
from ralph.display.snapshot import PipelineSnapshot

# ---------- Fixture ----------

#: Verbatim reasons pulled from the producer so the tests assert on strings
#: the operator will actually see in a real run.
_TARGET_DIRTY_REASON = "target worktree dirty"  # auto_integrate_ff.py:108 (AC-09)
_CAS_REFUSAL_REASON = "target advanced concurrently (CAS mismatch)"  # auto_integrate_ff.py:122 (AC-08)
_CONFLICT_REASON = "rebase and endpoint merge both conflicted"  # auto_integrate.py:230 (AC-07)
_NO_COMMITS_BEYOND_REASON = "no commits beyond target"  # auto_integrate.py:557 (AC-03)
_ALREADY_FF_REASON = "already fast-forwarded"  # auto_integrate.py:747-750 (recovery)


def _make_snapshot(
    *,
    auto_integrate_action: str | None = None,
    auto_integrate_reason: str | None = None,
    auto_integrate_target: str | None = None,
    auto_integrate_fast_forwarded: bool = False,
    phase: str = "complete",
    previous_phase: str | None = "development_commit",
    review_issues_found: bool = False,
    interrupted_by_user: bool = False,
    last_error: str | None = None,
    pr_url: str | None = "https://example.com/pr/42",
    push_count: int = 0,
    total_agent_calls: int = 1,
    total_continuations: int = 0,
    total_fallbacks: int = 0,
    total_retries: int = 0,
    workers: tuple = (),
    prompt_path: str | None = "PROMPT.md",
    prompt_preview: tuple[str, ...] = (),
    run_id: str | None = "run-ai",
) -> PipelineSnapshot:
    """Construct a PipelineSnapshot with the four auto-integrate fields set.

    Mirrors the construction precedent in
    ``tests/test_completion_summary.py``:48-75, with ``created_at`` pinned
    to a deterministic value so every test run renders identically.
    """
    return PipelineSnapshot(
        phase=phase,
        previous_phase=previous_phase,
        review_issues_found=review_issues_found,
        interrupted_by_user=interrupted_by_user,
        last_error=last_error,
        pr_url=pr_url,
        push_count=push_count,
        total_agent_calls=total_agent_calls,
        total_continuations=total_continuations,
        total_fallbacks=total_fallbacks,
        total_retries=total_retries,
        workers=workers,
        prompt_path=prompt_path,
        prompt_preview=prompt_preview,
        run_id=run_id,
        created_at=datetime(2026, 4, 18, 12, 10, tzinfo=UTC),
        auto_integrate_action=auto_integrate_action,
        auto_integrate_reason=auto_integrate_reason,
        auto_integrate_target=auto_integrate_target,
        auto_integrate_fast_forwarded=auto_integrate_fast_forwarded,
    )


def _render_plain(snapshot: PipelineSnapshot) -> str:
    console = Console(record=True, width=160, force_terminal=False, color_system=None)
    console.print(render_completion_summary(snapshot))
    return console.export_text()


def _render_group(snapshot: PipelineSnapshot) -> str:
    buf = StringIO()
    console = Console(
        file=buf, force_terminal=False, width=160, color_system=None
    )
    ctx = make_display_context(console=console, env={})
    group = render_completion_summary_group(
        snapshot,
        display_context=ctx,
        options=CompletionSummaryOptions(),
    )
    console.print(group, markup=False, highlight=False)
    return buf.getvalue()


# ---------- Step 3: unit tests for the shared formatter ----------


def test_format_successful_land_says_fast_forwarded_target() -> None:
    """Normal successful land ('rebased', target, fast_forwarded=True) renders
    'fast-forwarded <target>'. This is the case the unreachable
    ``action == 'fast_forwarded'`` branch at runner.py:589 used to break,
    because the producer never emits a 'fast_forwarded' action.
    """
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "rebased", "main", None, fast_forwarded=True
    )
    assert "fast-forwarded main" in msg
    assert "rebased" in msg.lower()


def test_format_refused_land_exposes_target_worktree_dirty() -> None:
    """The AC-09 refusal reason ('target worktree dirty') must be visible, and
    a refused land must NOT claim a fast-forward.
    """
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "merged", "main", _TARGET_DIRTY_REASON, fast_forwarded=False
    )
    assert _TARGET_DIRTY_REASON in msg
    assert "fast-forwarded main" not in msg


def test_format_cas_refusal_exposes_concurrent_target_advance() -> None:
    """The AC-08 refusal reason ('target advanced concurrently (CAS mismatch)')
    must be visible verbatim.
    """
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "rebased", "main", _CAS_REFUSAL_REASON, fast_forwarded=False
    )
    assert _CAS_REFUSAL_REASON in msg
    assert "fast-forwarded main" not in msg


def test_format_successful_land_with_stale_rebase_reason_does_not_claim_skip() -> None:
    """On the RebaseNoOp path the producer records ('rebased', <reason>)
    and a successful fast-forward retains that reason. The reason is a
    stale rebase reason when ``fast_forwarded`` is True; rendering it
    as a fast-forward skip would emit a phantom skip on a healthy run.
    """
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "rebased", "main", "already up to date", fast_forwarded=True
    )
    assert "fast-forwarded main" in msg
    assert "fast-forward skipped" not in msg


def test_format_unresolved_conflict_states_reason() -> None:
    """AC-07: an unresolved conflict surfaces its reason in the receipt."""
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "conflict", "main", _CONFLICT_REASON, fast_forwarded=False
    )
    assert "conflict" in msg.lower()
    assert _CONFLICT_REASON in msg


def test_format_skip_states_reason() -> None:
    """A recorded skip ('skipped', reason) must surface its reason."""
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "skipped", "main", _NO_COMMITS_BEYOND_REASON, fast_forwarded=False
    )
    assert _NO_COMMITS_BEYOND_REASON in msg


def test_format_recovered_states_reason() -> None:
    """Recovery path ('recovered', reason) surfaces the reason and (when
    applicable) the successful land.
    """
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "recovered", "main", _ALREADY_FF_REASON, fast_forwarded=True
    )
    assert "recovered" in msg.lower()
    assert _ALREADY_FF_REASON in msg


def test_format_unknown_verb_returns_bare_phrase_without_double_prefix() -> None:
    """Defensive: any unknown ``last_action`` falls back to a bare phrase;
    the single ``auto-integrate:`` prefix is added by the emit site, so the
    formatter must NOT prepend one of its own (the pre-existing bug had a
    doubled prefix ``auto-integrate: auto-integrate: <action>``).
    """
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message("noop", None, None, fast_forwarded=False)
    assert "auto-integrate:" not in msg
    assert msg.strip() == "noop"


# ---------- Step 5: render_completion_summary integration ----------


def test_plain_renderer_surfaces_conflict_with_reason() -> None:
    """The prompt's 'unresolved conflicts are surfaced to the operator in
    the run's final receipt' contract: a conflicted run must render
    ``Auto-integrate:`` plus the conflict reason text.
    """
    snap = _make_snapshot(
        auto_integrate_action="conflict",
        auto_integrate_reason=_CONFLICT_REASON,
        auto_integrate_target="main",
        auto_integrate_fast_forwarded=False,
    )
    text = _render_plain(snap)
    assert "Auto-integrate:" in text
    assert _CONFLICT_REASON in text


def test_plain_renderer_says_fast_forwarded_target_on_successful_land() -> None:
    """Successful land renders ``fast-forwarded <target>``."""
    snap = _make_snapshot(
        auto_integrate_action="rebased",
        auto_integrate_reason=None,
        auto_integrate_target="main",
        auto_integrate_fast_forwarded=True,
    )
    text = _render_plain(snap)
    assert "Auto-integrate:" in text
    assert "fast-forwarded main" in text


def test_plain_renderer_exposes_target_worktree_dirty_on_refusal() -> None:
    """A refused fast-forward surfaces its recorded reason."""
    snap = _make_snapshot(
        auto_integrate_action="merged",
        auto_integrate_reason=_TARGET_DIRTY_REASON,
        auto_integrate_target="main",
        auto_integrate_fast_forwarded=False,
    )
    text = _render_plain(snap)
    assert "Auto-integrate:" in text
    assert _TARGET_DIRTY_REASON in text
    assert "fast-forwarded main" not in text


def test_plain_renderer_renders_nothing_when_auto_integrate_was_disabled() -> None:
    """AC-01: when no integration ran (action is None), no
    ``Auto-integrate:`` line renders -- the receipt stays byte-identical to
    runs without auto-integration.
    """
    snap = _make_snapshot(
        auto_integrate_action=None,
        auto_integrate_reason=None,
        auto_integrate_target=None,
        auto_integrate_fast_forwarded=False,
    )
    text = _render_plain(snap)
    assert "Auto-integrate:" not in text


# ---------- Group renderer (the one actually shown to the operator) ----------


def test_group_renderer_surfaces_conflict_with_reason() -> None:
    """The on-screen group renderer mirrors the plain receipt."""
    snap = _make_snapshot(
        auto_integrate_action="conflict",
        auto_integrate_reason=_CONFLICT_REASON,
        auto_integrate_target="main",
        auto_integrate_fast_forwarded=False,
    )
    text = _render_group(snap)
    assert _CONFLICT_REASON in text


def test_group_renderer_exposes_target_worktree_dirty_on_refusal() -> None:
    """The group renderer surfaces the refused-fast-forward reason."""
    snap = _make_snapshot(
        auto_integrate_action="merged",
        auto_integrate_reason=_TARGET_DIRTY_REASON,
        auto_integrate_target="main",
        auto_integrate_fast_forwarded=False,
    )
    text = _render_group(snap)
    assert _TARGET_DIRTY_REASON in text
    # Confirm the success phrase does NOT leak onto a refused render.
    # (Group renderer produces "  auto-integrate: ..." with internal spacing.)
    assert "fast-forwarded main" not in text


def test_group_renderer_renders_nothing_when_auto_integrate_was_disabled() -> None:
    """Disabled-path receipt stays byte-identical to runs without
    auto-integration.
    """
    snap = _make_snapshot(
        auto_integrate_action=None,
        auto_integrate_reason=None,
        auto_integrate_target=None,
        auto_integrate_fast_forwarded=False,
    )
    text = _render_group(snap)
    assert _CONFLICT_REASON not in text
    assert "auto-integrate" not in text.lower()
