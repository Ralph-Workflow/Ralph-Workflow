from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ._process_monitor import ProcessRole

if TYPE_CHECKING:
    from ralph.config.enums import AgentTransport

RoleClassifier = Callable[[int, list[str] | None], ProcessRole]


def _conservative_role_classifier(_pid: int, _cmdline: list[str] | None) -> ProcessRole:
    return ProcessRole.INCIDENTAL_HELPER


def role_classifier_for_transport(transport: AgentTransport | None) -> RoleClassifier:
    return _conservative_role_classifier
