"""Public liveness-verification contract for the process manager.

The protocol lives in its own public module because the repository's
repo-structure policy allows at most one public top-level class per module.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ralph.process.manager._process_liveness import LivenessResult
    from ralph.process.manager._process_manager_types import _PsutilModuleLike


class LivenessVerifier(Protocol):
    """Reports whether a PID is still alive.

    The default implementation is
    :func:`ralph.process.manager.verify_process_liveness`.  A caller may
    supply its own verifier to ``ProcessManager`` so a liveness policy
    that differs from the built-in one (a custom probe, a recorded
    answer, a stricter rule) can be used for every termination path.
    """

    def __call__(
        self,
        pid: int,
        *,
        psutil_mod: _PsutilModuleLike | None = None,
    ) -> LivenessResult: ...
