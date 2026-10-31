"""In-process record of the checkouts an agent phase is working in.

The catch-up ticker (:class:`ralph.pipeline.auto_integrate_catchup.AutoIntegrateCatchupWorker`)
is a daemon thread in the same process as the run loop. Its repository
mutation lease (``git_command_queue``) is per common git dir and nothing
takes it for an agent phase, so it cannot tell the ticker that an agent is
reading or editing a checkout. Without another signal only "the worktree is
dirty" protected a running agent: a clean checkout (planning, reading,
between edits) could be fast-forwarded underneath it.

:class:`AgentPhaseActivity` is that signal. The runner enters
:meth:`AgentPhaseActivity.agent_phase` for the checkout an agent effect runs
in; the ticker asks :meth:`AgentPhaseActivity.is_active` before it moves a
checkout and defers instead. The mark is per checkout, not per repository,
so the ticker keeps fetching and keeps fast-forwarding a target that is
checked out somewhere else (or nowhere) while the agent works.

The ticker performs each checkout mutation inside
:meth:`AgentPhaseActivity.mutation_window`. Entering an agent phase waits for
an open window to close, so a phase never starts halfway through a
fast-forward of its own checkout. Ending a phase never waits.

The process-wide instance (:func:`process_agent_phases`) is what production
uses; tests construct their own instance and inject it.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


def _checkout_key(root: Path | str) -> str:
    """Return a stable identity for a checkout path (symlinks resolved)."""
    path = Path(root)
    try:
        return str(path.resolve())
    except (OSError, RuntimeError):
        return str(path.absolute())


def same_checkout(first: Path | str, second: Path | str) -> bool:
    """Whether two paths name the same checkout once symlinks are resolved."""
    return _checkout_key(first) == _checkout_key(second)


class AgentPhaseActivity:
    """Thread-safe count of agent phases per checkout, plus the mutation window."""

    def __init__(self) -> None:
        self._state = threading.Lock()
        self._mutations = threading.RLock()
        self._active: dict[str, int] = {}  # bounded-accumulator-ok: live-phase checkouts only

    @contextmanager
    def agent_phase(self, root: Path | str) -> Iterator[None]:
        """Mark ``root`` as in use by an agent for the duration of the block.

        The mark is released on every exit path, including exceptions and
        ``KeyboardInterrupt``. Phases nest: the checkout stays marked until
        the last one ends.
        """
        key = _checkout_key(root)
        entered = False
        try:
            with self._mutations, self._state:
                self._active[key] = self._active.get(key, 0) + 1
                entered = True
            yield
        finally:
            if entered:
                with self._state:
                    remaining = self._active.get(key, 0) - 1
                    if remaining > 0:
                        self._active[key] = remaining
                    else:
                        self._active.pop(key, None)

    def is_active(self, root: Path | str) -> bool:
        """Whether an agent phase is currently running in ``root``."""
        key = _checkout_key(root)
        with self._state:
            return key in self._active

    @contextmanager
    def mutation_window(self) -> Iterator[None]:
        """Hold off new agent phases while one checkout mutation runs.

        A caller checks :meth:`is_active` INSIDE the window: the answer
        cannot change from inactive to active until the window closes.
        """
        with self._mutations:
            yield


_PROCESS_AGENT_PHASES = AgentPhaseActivity()


def process_agent_phases() -> AgentPhaseActivity:
    """Return the process-wide activity record the runner and the ticker share."""
    return _PROCESS_AGENT_PHASES


__all__ = ["AgentPhaseActivity", "process_agent_phases", "same_checkout"]
