from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.process.manager._process_liveness import LivenessResult
    from ralph.process.manager._process_manager_types import _PsutilModuleLike


class LivenessVerifier(Protocol):
    def __call__(
        self,
        pid: int,
        *,
        psutil_mod: _PsutilModuleLike | None = None,
    ) -> LivenessResult: ...
