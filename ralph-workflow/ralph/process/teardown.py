from __future__ import annotations

import contextlib
import logging
import os
import signal
import threading
import time
from collections import OrderedDict
from importlib import import_module
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

import psutil

from ralph.checked_accessors import optional_int
from ralph.process.manager.process_termination_issuer import ProcessTerminationIssuer
from ralph.timeout_defaults import KILL_ESCALATION_CEILING_MS

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.process.manager import ProcessManager
    from ralph.process.manager._process_record import ProcessRecord


@runtime_checkable
class _ProcessManagerModule(Protocol):
    def get_process_manager(self) -> ProcessManager: ...


_LOG = logging.getLogger(__name__)

_MAX_ANCESTOR_WALK: int = 32

_SESSION_REGISTRY_MAX: int = 256
_SESSION_REGISTRY: OrderedDict[int, int] = OrderedDict()
_SESSION_REGISTRY_LOCK = threading.Lock()


def _own_pgid() -> int | None:
    if not hasattr(os, "getpgid"):
        return None
    try:
        return os.getpgid(0)
    except OSError:
        return None


def _is_own_descendant(proc: psutil.Process) -> bool:
    self_pid = os.getpid()
    current = proc
    for _ in range(_MAX_ANCESTOR_WALK):
        try:
            parent = current.parent()
        except psutil.Error:
            return False
        if parent is None or parent.pid <= 1:
            return False
        if parent.pid == self_pid:
            return True
        current = parent
    return False


def _group_is_safe(pgid: int) -> bool:
    if pgid <= 1:
        return False
    own = _own_pgid()
    return own is None or pgid != own


def verified_child_session_pgid(pid: int) -> int | None:
    if pid <= 1 or not hasattr(os, "getpgid"):
        return None
    try:
        proc = psutil.Process(pid)
    except psutil.Error:
        return None
    if not _is_own_descendant(proc):
        return None
    try:
        pgid = os.getpgid(pid)
    except OSError:
        return None
    if pgid != pid or not _group_is_safe(pgid):
        return None
    return pgid


def register_child_session(pid: int) -> int | None:
    pgid = verified_child_session_pgid(pid)
    if pgid is None:
        return None
    with _SESSION_REGISTRY_LOCK:
        _SESSION_REGISTRY[pid] = pgid
        _SESSION_REGISTRY.move_to_end(pid)
        while len(_SESSION_REGISTRY) > _SESSION_REGISTRY_MAX:
            _SESSION_REGISTRY.popitem(last=False)
    return pgid


def _registered_session_pgid(pid: int) -> int | None:
    with _SESSION_REGISTRY_LOCK:
        return _SESSION_REGISTRY.get(pid)


def _forget_child_session(pid: int) -> None:
    with _SESSION_REGISTRY_LOCK:
        _SESSION_REGISTRY.pop(pid, None)


def _has_exited(proc: psutil.Process) -> bool:
    try:
        return not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE
    except psutil.Error:
        return True


def _label_family(value: str) -> str:
    return value.split(":", 1)[0]


def _resolve_target(host_pid: int, pgid: int | None) -> tuple[psutil.Process | None, int | None]:
    group = pgid if pgid is not None and _group_is_safe(pgid) else None
    if group is None:
        registered = _registered_session_pgid(host_pid)
        group = registered if registered is not None and _group_is_safe(registered) else None

    if host_pid <= 1:
        return None, None

    try:
        host = psutil.Process(host_pid)
    except psutil.Error:
        return None, group

    if not _is_own_descendant(host):
        return None, None

    return host, group if group is not None else verified_child_session_pgid(host_pid)


@runtime_checkable
class ProcessTeardown(Protocol):
    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None: ...


class DefaultProcessTeardown:
    def __init__(
        self,
        kill_escalation_ms: float = KILL_ESCALATION_CEILING_MS,
        record_lookup: Callable[[int], ProcessRecord | None] | None = None,
    ) -> None:
        self._kill_escalation_ms = kill_escalation_ms
        self._record_lookup = record_lookup

    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
        pgid: int | None = None,
    ) -> None:
        if not isinstance(issuer, ProcessTerminationIssuer):
            raise TypeError(
                "teardown_subtree issuer must be a ProcessTerminationIssuer, "
                f"got {type(issuer).__name__}: {issuer!r}"
            )
        host, group = _resolve_target(host_pid, pgid)
        record = self._record_for(host_pid)
        label = record.label if record is not None else None
        if label is not None and _label_family(label) != _label_family(owner_label):
            self._record_teardown(
                host_pid,
                issuer=issuer,
                outcome="refused_label_mismatch",
            )
            _LOG.warning(
                "refusing process teardown pid=%s issuer=%s label=%s", host_pid, issuer, label
            )
            return
        _forget_child_session(host_pid)

        if host is None:
            self._record_teardown(
                host_pid,
                issuer=issuer,
                outcome="already_exited",
            )
            if group is not None:
                self._signal_process_group(group)
            return

        self._record_teardown(host_pid, issuer=issuer, outcome="signalled")
        procs: list[psutil.Process] = []
        try:
            procs.append(host)
            procs.extend(host.children(recursive=True))
        except psutil.Error:
            pass

        for proc in procs:
            with contextlib.suppress(psutil.Error):
                proc.terminate()

        gone = self._await_exit(procs)

        for proc in procs:
            if proc.pid in gone:
                continue
            try:
                if proc.is_running():
                    proc.kill()
            except psutil.Error:
                pass

        survivors = [proc for proc in procs if proc.pid not in gone]
        if survivors:
            gone.update(self._await_exit(survivors))

        if group is not None:
            if len(gone) >= len(procs):
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(group, signal.SIGKILL)
            else:
                self._signal_process_group(group)

    def _record_for(self, host_pid: int) -> ProcessRecord | None:
        if self._record_lookup is not None:
            return self._record_lookup(host_pid)
        manager_module: object = import_module("ralph.process.manager")
        if not isinstance(manager_module, _ProcessManagerModule):
            raise RuntimeError("process manager module does not expose get_process_manager")
        return manager_module.get_process_manager().get_record(host_pid)

    def _record_teardown(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        outcome: str,
    ) -> None:
        manager_module: object = import_module("ralph.process.manager")
        if not isinstance(manager_module, _ProcessManagerModule):
            raise RuntimeError("process manager module does not expose get_process_manager")
        manager = manager_module.get_process_manager()
        if manager.get_record(host_pid) is None:
            return
        manager.record_teardown(host_pid, issuer=issuer, outcome=outcome)

    def _await_exit(self, procs: list[psutil.Process]) -> set[int]:
        deadline = time.monotonic() + (self._kill_escalation_ms / 1000.0)
        gone: set[int] = set()
        poll_interval = 0.01
        while time.monotonic() < deadline:
            for proc in procs:
                if proc.pid in gone:
                    continue
                if _has_exited(proc):
                    gone.add(proc.pid)
            if len(gone) >= len(procs):
                break
            time.sleep(poll_interval)
            poll_interval = min(poll_interval * 1.5, 0.025)
        return gone

    def _signal_process_group(self, pgid: int) -> None:
        if not _group_is_safe(pgid) or not hasattr(os, "killpg"):
            return

        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pgid, signal.SIGTERM)

        deadline = time.monotonic() + (self._kill_escalation_ms / 1000.0)
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            except (PermissionError, OSError):
                return
            time.sleep(0.05)

        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(pgid, signal.SIGKILL)


def teardown_subtree(
    host_pid: int,
    *,
    issuer: ProcessTerminationIssuer,
    owner_label: str = "invoke",
    kill_escalation_ms: float = KILL_ESCALATION_CEILING_MS,
    pgid: int | None = None,
) -> None:
    if not isinstance(issuer, ProcessTerminationIssuer):
        raise TypeError(
            "teardown_subtree issuer must be a ProcessTerminationIssuer, "
            f"got {type(issuer).__name__}: {issuer!r}"
        )
    DefaultProcessTeardown(kill_escalation_ms=kill_escalation_ms).teardown_subtree(
        host_pid, issuer=issuer, owner_label=owner_label, pgid=pgid
    )


def _teardown_subtree_if_pid_available(
    handle: object,
    *,
    issuer: ProcessTerminationIssuer,
    teardown: ProcessTeardown | None = None,
) -> None:
    pid = optional_int(cast("object", getattr(handle, "pid", None)))
    if pid is None:
        return
    if teardown is None:
        teardown_subtree(pid, issuer=issuer)
        return
    teardown.teardown_subtree(pid, issuer=issuer)


__all__ = [
    "DefaultProcessTeardown",
    "ProcessTeardown",
    "register_child_session",
    "teardown_subtree",
    "verified_child_session_pgid",
]
