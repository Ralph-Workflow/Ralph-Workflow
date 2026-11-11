from __future__ import annotations

from datetime import UTC, datetime
from io import StringIO

import pytest
from rich.console import Console

from ralph.display.completion_summary import (
    CompletionSummaryOptions,
    render_completion_summary,
    render_completion_summary_group,
)
from ralph.display.context import make_display_context
from ralph.display.snapshot import PipelineSnapshot

_TARGET_DIRTY_REASON = "target worktree dirty"
_CAS_REFUSAL_REASON = "target advanced concurrently (CAS mismatch)"
_CONFLICT_REASON = "rebase and endpoint merge both conflicted"
_NO_COMMITS_BEYOND_REASON = "no commits beyond target"
_ALREADY_FF_REASON = "already fast-forwarded"


def _make_snapshot(
    *,
    auto_integrate_action: str | None = None,
    auto_integrate_reason: str | None = None,
    auto_integrate_target: str | None = None,
    auto_integrate_fast_forwarded: bool = False,
    auto_integrate_remote: str | None = None,
    auto_integrate_remote_sync: str | None = None,
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
        auto_integrate_remote=auto_integrate_remote,
        auto_integrate_remote_sync=auto_integrate_remote_sync,
    )


def _render_plain(snapshot: PipelineSnapshot) -> str:
    console = Console(record=True, width=160, force_terminal=False, color_system=None)
    console.print(render_completion_summary(snapshot))
    return console.export_text()


def _render_group(snapshot: PipelineSnapshot) -> str:
    buf = StringIO()
    console = Console(file=buf, force_terminal=False, width=160, color_system=None)
    ctx = make_display_context(console=console, env={})
    group = render_completion_summary_group(
        snapshot,
        display_context=ctx,
        options=CompletionSummaryOptions(),
    )
    console.print(group, markup=False, highlight=False)
    return buf.getvalue()


def test_format_successful_land_says_fast_forwarded_target() -> None:
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message("rebased", "main", None, fast_forwarded=True)
    assert "fast-forwarded main" in msg
    assert "rebased" in msg.lower()


def test_format_refused_land_exposes_target_worktree_dirty() -> None:
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "merged", "main", _TARGET_DIRTY_REASON, fast_forwarded=False
    )
    assert _TARGET_DIRTY_REASON in msg
    assert "fast-forwarded main" not in msg


def test_format_cas_refusal_exposes_concurrent_target_advance() -> None:
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "rebased", "main", _CAS_REFUSAL_REASON, fast_forwarded=False
    )
    assert _CAS_REFUSAL_REASON in msg
    assert "fast-forwarded main" not in msg


def test_format_successful_land_with_stale_rebase_reason_does_not_claim_skip() -> None:
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "rebased", "main", "already up to date", fast_forwarded=True
    )
    assert "fast-forwarded main" in msg
    assert "fast-forward skipped" not in msg


def test_format_unresolved_conflict_states_reason() -> None:
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message("conflict", "main", _CONFLICT_REASON, fast_forwarded=False)
    assert "conflict" in msg.lower()
    assert _CONFLICT_REASON in msg


def test_format_skip_states_reason() -> None:
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "skipped", "main", _NO_COMMITS_BEYOND_REASON, fast_forwarded=False
    )
    assert _NO_COMMITS_BEYOND_REASON in msg


def test_format_recovered_states_reason() -> None:
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message(
        "recovered", "main", _ALREADY_FF_REASON, fast_forwarded=True
    )
    assert "recovered" in msg.lower()
    assert _ALREADY_FF_REASON in msg


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        ("verified", "[base: verified-fresh]"),
        ("degraded", "[base: degraded]"),
        ("unverified", "[base: unverified]"),
    ],
)
def test_freshness_rendering_distinguishes_the_three_operator_classes(
    verdict: str, expected: str
) -> None:
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    message = format_auto_integrate_message(
        "rebased", "main", None, fast_forwarded=True, freshness_verdict=verdict
    )

    assert expected in message


def test_format_unknown_verb_returns_bare_phrase_without_double_prefix() -> None:
    from ralph.display.auto_integrate_message import format_auto_integrate_message

    msg = format_auto_integrate_message("noop", None, None, fast_forwarded=False)
    assert "auto-integrate:" not in msg
    assert msg.strip() == "noop"


def test_configured_remote_is_rendered_by_both_completion_surfaces() -> None:
    snapshot = _make_snapshot(
        auto_integrate_action="rebased",
        auto_integrate_target="release",
        auto_integrate_fast_forwarded=True,
        auto_integrate_remote="upstream",
        auto_integrate_remote_sync="reconciled",
    )
    for text in (_render_plain(snapshot), _render_group(snapshot)):
        assert "upstream/release" in text
        assert "origin" not in text


def test_plain_renderer_surfaces_conflict_with_reason() -> None:
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
    snap = _make_snapshot(
        auto_integrate_action=None,
        auto_integrate_reason=None,
        auto_integrate_target=None,
        auto_integrate_fast_forwarded=False,
    )
    text = _render_plain(snap)
    assert "Auto-integrate:" not in text


def test_group_renderer_surfaces_conflict_with_reason() -> None:
    snap = _make_snapshot(
        auto_integrate_action="conflict",
        auto_integrate_reason=_CONFLICT_REASON,
        auto_integrate_target="main",
        auto_integrate_fast_forwarded=False,
    )
    text = _render_group(snap)
    assert _CONFLICT_REASON in text


def test_group_renderer_exposes_target_worktree_dirty_on_refusal() -> None:
    snap = _make_snapshot(
        auto_integrate_action="merged",
        auto_integrate_reason=_TARGET_DIRTY_REASON,
        auto_integrate_target="main",
        auto_integrate_fast_forwarded=False,
    )
    text = _render_group(snap)
    assert _TARGET_DIRTY_REASON in text
    assert "fast-forwarded main" not in text


def test_group_renderer_renders_nothing_when_auto_integrate_was_disabled() -> None:
    snap = _make_snapshot(
        auto_integrate_action=None,
        auto_integrate_reason=None,
        auto_integrate_target=None,
        auto_integrate_fast_forwarded=False,
    )
    text = _render_group(snap)
    assert _CONFLICT_REASON not in text
    assert "auto-integrate" not in text.lower()
