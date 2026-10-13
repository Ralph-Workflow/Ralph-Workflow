"""Durable completion-evidence helpers."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from ralph.agents.completion_signals import _check_completion_sentinel
from ralph.pipeline.plumbing.smoke_evidence import Evidence, Provenance

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.completion_signals import CompletionSignals


class _CompletionEvidenceOptions(Protocol):
    @property
    def workspace_path(self) -> Path | None: ...

    @property
    def _sentinel_check_fn(self) -> Callable[[Path, str | None], bool] | None: ...

    @property
    def sentinel_secret(self) -> str | None: ...


def apply_sentinel_signal(
    signals: CompletionSignals,
    opts: _CompletionEvidenceOptions,
    *,
    sentinel_run_id: str | None,
) -> CompletionSignals:
    workspace = opts.workspace_path
    if workspace is None:
        raise ValueError("completion evidence requires a workspace path")
    if opts._sentinel_check_fn is not None:
        sentinel_found = opts._sentinel_check_fn(workspace, sentinel_run_id)
    else:
        sentinel_found = _check_completion_sentinel(
            workspace,
            sentinel_run_id,
            sentinel_secret=opts.sentinel_secret,
        )
    if sentinel_found:
        sentinel_evidence = Evidence(
            holds=True,
            provenance=Provenance.WORKSPACE_EFFECT,
            detail=(
                "completion sentinel present (graded at "
                "WORKSPACE_EFFECT; upgrade to WIRE via smoke_evidence "
                "grading when wire ledger is in scope)"
            ),
        )
        return replace(
            signals,
            explicit_complete=True,
            completion_sentinel_present=sentinel_evidence,
            completion_sentinel_evidence=sentinel_evidence,
        )
    if not signals.explicit_complete:
        absent_evidence = Evidence(
            holds=False,
            provenance=Provenance.ABSENT,
            detail="completion sentinel not present",
        )
        return replace(
            signals,
            explicit_complete=False,
            completion_sentinel_present=absent_evidence,
            completion_sentinel_evidence=absent_evidence,
        )
    return signals


__all__ = ["apply_sentinel_signal"]
