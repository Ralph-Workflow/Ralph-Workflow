"""Dependency-injected interrupt orchestration helpers.

This module centralizes what should happen when Ralph receives a user interrupt:
record it, stop optional connectivity waits, try a graceful shutdown first, and
escalate to a forced kill plus hard exit on a second interrupt. Keeping these
actions behind an injectable controller makes the behavior testable without
real signals.
"""

from __future__ import annotations

import os
import signal
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.interrupt.state import request_user_interrupt
from ralph.process.manager import get_process_manager

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from types import FrameType

    from ralph.interrupt.signal_getter import SignalGetter
    from ralph.interrupt.signal_setter import SignalSetter
    from ralph.process.manager import ProcessManager


_DEFAULT_SIGNAL_GETTER = cast("SignalGetter", signal.getsignal)
_DEFAULT_SIGNAL_SETTER = cast("SignalSetter", signal.signal)

INTERRUPT_EXIT_CODE = 130
_INTERRUPT_EXIT_CODE_REQUIRED: int = 130
if INTERRUPT_EXIT_CODE != _INTERRUPT_EXIT_CODE_REQUIRED:
    raise RuntimeError(
        f"INTERRUPT_EXIT_CODE must be {_INTERRUPT_EXIT_CODE_REQUIRED} (got {INTERRUPT_EXIT_CODE})"
    )


@dataclass(frozen=True)
class InterruptController:
    """Coordinate graceful and forced interrupt handling through injected seams."""

    shutdown_all: Callable[[float], None]
    record_interrupt: Callable[[], None] = request_user_interrupt
    stop_connectivity: Callable[[], None] | None = None
    kill_process_group: Callable[[int, int], None] | None = None
    hard_exit: Callable[[int], None] | None = None
    # Optional label-targeted shutdown. When set, ``begin_interrupt`` with
    # a non-empty ``kill_label`` calls this closure INSTEAD of the
    # generic ``shutdown_all``. The closure is built by
    # ``controller_from_process_manager`` to wrap
    # ``manager.shutdown_all_for_label(label_prefix, grace_period_s=...)``
    # so the FIRST SIGINT can target the agent's process group
    # directly instead of the generic tracked-process shutdown. The
    # label is the agent's process label (e.g. ``"invoke:claude"``).
    shutdown_all_for_label: Callable[[str, float], None] | None = None

    def begin_interrupt(
        self,
        *,
        grace_period_s: float,
        kill_label: str = "",
    ) -> None:
        """Record the interrupt and attempt graceful tracked-process shutdown.

        When ``kill_label`` is non-empty AND ``shutdown_all_for_label`` is
        set, the controller calls the label-targeted closure INSTEAD of
        the generic ``shutdown_all``. This lets the FIRST SIGINT route
        through a path that targets a specific agent process group
        rather than the generic tracked-process shutdown.

        The empty-label fallback preserves the existing behavior for
        callers that don't pass a label: ``self.shutdown_all(grace_period_s)``
        is called exactly as before. This is the backward-compatible
        path; the new kill_label kwarg is optional and defaults to "".
        """
        self.record_interrupt()
        if self.stop_connectivity is not None:
            with suppress(Exception):
                self.stop_connectivity()
        if kill_label and self.shutdown_all_for_label is not None:
            self.shutdown_all_for_label(kill_label, grace_period_s)
        else:
            self.shutdown_all(grace_period_s)

    def force_interrupt(
        self,
        *,
        bridge_pgids: Iterable[int] = (),
        **kwargs: object,
    ) -> None:
        """Escalate to immediate tracked-process termination.

        The ``bridge_pgids`` parameter is the new canonical name; it
        is forwarded to ``kill_process_group`` as PGIDs. The legacy
        ``bridge_pids`` keyword is accepted via ``**kwargs`` for
        backward compatibility and emits a single loguru warning
        when used. The per-pgid kill loop has been dropped: the
        real :class:`ProcessManager`'s ``shutdown_all(0)`` already
        escalates to SIGKILL every active record, so the
        per-pgid loop was redundant. Callers MUST pass
        ``bridge_pgids`` in new code.
        """
        bridge_pids_legacy = cast("Iterable[int]", kwargs.pop("bridge_pids", ()))
        if bridge_pids_legacy:
            logger.warning("bridge_pids is deprecated; pass bridge_pgids instead")
        del bridge_pgids, bridge_pids_legacy
        self.record_interrupt()
        if self.stop_connectivity is not None:
            with suppress(Exception):
                self.stop_connectivity()
        self.shutdown_all(0)

    def force_exit(
        self,
        *,
        bridge_pgids: Iterable[int] = (),
        **kwargs: object,
    ) -> None:
        """Force-kill tracked work and exit with the canonical interrupt code.

        The ``bridge_pgids`` parameter is the new canonical name;
        the legacy ``bridge_pids`` keyword is accepted via
        ``**kwargs`` for backward compatibility and emits a single
        loguru warning when used.
        """
        bridge_pids_legacy = cast("Iterable[int]", kwargs.pop("bridge_pids", ()))
        if bridge_pids_legacy:
            logger.warning("bridge_pids is deprecated; pass bridge_pgids instead")
        pgids: Iterable[int] = list(bridge_pgids) if bridge_pgids else list(bridge_pids_legacy)
        self.force_interrupt(bridge_pgids=pgids)
        hard_exit = self.hard_exit or os._exit
        hard_exit(INTERRUPT_EXIT_CODE)


def install_force_kill_handler(
    on_force_interrupt: Callable[[], None],
    *,
    signal_getter: SignalGetter = _DEFAULT_SIGNAL_GETTER,
    signal_setter: SignalSetter = _DEFAULT_SIGNAL_SETTER,
) -> Callable[[], None]:
    """Install a temporary SIGINT handler that escalates to forced termination."""
    previous = signal_getter(signal.SIGINT)

    def _handler(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        on_force_interrupt()

    signal_setter(signal.SIGINT, _handler)

    def _restore() -> None:
        signal_setter(signal.SIGINT, previous)

    return _restore


def controller_from_process_manager(
    *,
    process_manager: ProcessManager | None = None,
    stop_connectivity: Callable[[], None] | None = None,
    record_interrupt: Callable[[], None] = request_user_interrupt,
    kill_process_group: Callable[[int, int], None] | None = None,
    hard_exit: Callable[[int], None] | None = None,
) -> InterruptController:
    """Build an :class:`InterruptController` from a ProcessManager instance.

    Wires both ``shutdown_all`` and ``shutdown_all_for_label`` closures
    so ``begin_interrupt(kill_label=...)`` can target a specific agent
    process group instead of the generic tracked-process shutdown.
    The label is the agent's process label (e.g. ``"invoke:claude"``).
    """
    manager = process_manager or get_process_manager()

    def _shutdown_all(grace_period_s: float) -> None:
        manager.shutdown_all(grace_period_s=grace_period_s)

    def _shutdown_all_for_label(label_prefix: str, grace_period_s: float) -> None:
        manager.shutdown_all_for_label(label_prefix, grace_period_s=grace_period_s)

    return InterruptController(
        shutdown_all=_shutdown_all,
        shutdown_all_for_label=_shutdown_all_for_label,
        record_interrupt=record_interrupt,
        stop_connectivity=stop_connectivity,
        kill_process_group=kill_process_group,
        hard_exit=hard_exit,
    )


__all__ = [
    "INTERRUPT_EXIT_CODE",
    "InterruptController",
    "controller_from_process_manager",
    "install_force_kill_handler",
]
