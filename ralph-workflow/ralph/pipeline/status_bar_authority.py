from __future__ import annotations

import dataclasses
import threading
from collections import deque
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.pipeline.status_bar_operation import OperationStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.display.status_bar import StatusBarModel


class StatusBarAuthority:
    def __init__(self, display: object) -> None:
        self._display = display
        self._lock = threading.RLock()
        self._phase: StatusBarModel | None = None
        self._phase_name: str | None = None
        self._operations: deque[OperationStatus] = deque(maxlen=64)

    @property
    def phase_model(self) -> StatusBarModel | None:
        return self._phase

    def serves(self, display: object) -> bool:
        return self._display is display

    @property
    def phase_name(self) -> str | None:
        return self._phase_name

    def show_phase(self, model: StatusBarModel, phase_name: str | None = None) -> None:
        with self._lock:
            self._phase = model
            if phase_name is not None:
                self._phase_name = phase_name
            self.render()

    def top_operation_model(self) -> StatusBarModel | None:
        with self._lock:
            for operation in reversed(self._operations):
                if operation.model is not None and not operation.is_agent:
                    return operation.model
            return None

    @contextmanager
    def operation(self, idle: StatusBarModel | None = None) -> Iterator[OperationStatus]:
        handle = OperationStatus(self)
        with self._lock:
            self._operations.append(handle)
        try:
            yield handle
        finally:
            with self._lock:
                self._operations = deque(
                    (op for op in self._operations if op is not handle), maxlen=64
                )
                if self.shown_model() is None and idle is not None and handle.model is not None:
                    self._push(idle)
                else:
                    self.render()

    def current_operation(self) -> OperationStatus | None:
        with self._lock:
            return self._operations[-1] if self._operations else None

    def shown_model(self) -> StatusBarModel | None:
        with self._lock:
            for operation in reversed(self._operations):
                if operation.model is not None:
                    return operation.model
            return self._phase

    def render(self) -> None:
        with self._lock:
            model = self.shown_model()
            if model is not None:
                self._push(model)

    def _push(self, model: StatusBarModel) -> None:
        with self._lock:
            update = cast(
                "Callable[[object], None] | None",
                getattr(self._display, "update_status_bar", None),
            )
            if update is None:
                return
            try:
                update(model)
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                logger.debug("status bar render failed: {}", exc)


_AUTHORITIES: deque[StatusBarAuthority] = deque(maxlen=8)
_AUTHORITIES_LOCK = threading.Lock()
_ACTIVE: list[StatusBarAuthority | None] = [None]


def status_bar_authority(display: object) -> StatusBarAuthority | None:
    if display is None or not hasattr(display, "update_status_bar"):
        return None
    with _AUTHORITIES_LOCK:
        for authority in _AUTHORITIES:
            if authority.serves(display):
                return authority
        authority = StatusBarAuthority(display)
        _AUTHORITIES.append(authority)
        return authority


def phase_signature(model: StatusBarModel | None) -> tuple[object, ...] | None:
    if model is None:
        return None
    return (
        model.phase_label,
        model.outer_dev_iteration,
        model.inner_analysis,
        model.integration_alert,
        model.outer_label,
        model.agent_name,
        model.attention,
        model.operation_display_state,
    )


def show_phase_status(
    display: object, model: StatusBarModel, *, phase_name: str | None = None
) -> None:
    authority = status_bar_authority(display)
    if authority is not None:
        _ACTIVE[0] = authority
        authority.show_phase(model, phase_name)


def phase_label_for(phase: str) -> str:
    return phase.replace("_", " ").title()


@contextmanager
def running_agent_status(phase: str | None, agent_name: str | None) -> Iterator[None]:
    authority = _ACTIVE[0]
    base = None if authority is None else authority.phase_model
    if authority is None or base is None:
        yield
        return
    operation_model = authority.top_operation_model()
    if agent_name is None:
        agent_name = (operation_model or base).agent_name
    if operation_model is not None:
        model = dataclasses.replace(operation_model, agent_name=agent_name)
    elif phase is not None and phase != authority.phase_name:
        model = dataclasses.replace(base, phase_label=phase_label_for(phase), agent_name=agent_name)
    else:
        model = dataclasses.replace(base, agent_name=agent_name)
    with authority.operation() as handle:
        handle.is_agent = True
        handle.show(model)
        yield


@contextmanager
def operation_status(
    display: object, *, idle: StatusBarModel | None = None
) -> Iterator[OperationStatus | None]:
    authority = status_bar_authority(display)
    if authority is None:
        yield None
        return
    with authority.operation(idle) as handle:
        yield handle


def show_operation_status(display: object, model: StatusBarModel) -> None:
    authority = status_bar_authority(display)
    if authority is None:
        return
    operation = authority.current_operation()
    if operation is None:
        logger.debug("operation status ignored outside an operation scope: {}", model.phase_label)
        return
    operation.show(model)


__all__ = [
    "OperationStatus",
    "StatusBarAuthority",
    "operation_status",
    "phase_label_for",
    "phase_signature",
    "running_agent_status",
    "show_operation_status",
    "show_phase_status",
    "status_bar_authority",
]
