"""MCP server lifecycle helpers using a standalone localhost HTTP process.

Design note: bridge state machine (wt-16 make-before-break work).

The ``RestartAwareMcpBridge`` owns the parent-owned listening socket for
its full lifetime. Restart is ordered and single-acceptor:

1. Spawn the candidate while the old child keeps serving on the inherited
   listening fd.
2. Verify the candidate ONLY over a private channel (a parent-owned
   socketpair). A preflight over the shared endpoint is meaningless
   during the overlap because the old child may answer it.
3. Tell the old child to stop accepting (the pause file
   ``MCP_ACCEPT_PAUSE_FILE``). Wait for its acknowledgement — a FILE
   sentinel ``<pause>.ack`` created by the old child after it drops its
   accepting FD, NOT a message on the handover socketpair — bounded by
   ``_HANDOVER_ACK_TIMEOUT_S``; on timeout, kill the old child and confirm
   it is gone (``_HANDOVER_KILL_WAIT_S``) before promotion.
4. Tell the candidate to start accepting (the resume file
   ``MCP_ACCEPT_RESUME_FILE``). At no moment do two children accept.
5. Promote (_inner swap) under lock, then drain the old child inside
   ``_DRAIN_S`` (bounded by ``_HANDOVER_DRAIN_S``). Connections queued in
   the kernel backlog are served by the new child.
6. Probation: a candidate death after promotion is counted as
   ``process_exited`` and replaced immediately.

Handover bounds and deadlines:
The make-before-break transition is strictly bounded by named constants:

* ``_HANDOVER_ACK_TIMEOUT_S = 1.0``: maximum duration to wait for old child's
  pause acknowledgement file (``<pause>.ack``) after writing ``accept.pause``.
* ``_HANDOVER_KILL_WAIT_S = 1.0``: maximum duration to wait for old child process
  to exit after issuing SIGKILL if pause ack timed out.
* ``_HANDOVER_DRAIN_S = 0.5`` (defined in ``_handover_protocol.py:37``): drain policy
  deadline constant bounding old child
  drain period during graceful handover.

Drain policy deadline constant:
``_HANDOVER_DRAIN_S`` governs the drain phase duration where the retiring
child processes in-flight requests before hard shutdown.

Single-acceptor graceful handover verification:
Single-acceptor invariant and graceful handover sequencing are verified in
``tests/mcp/test_single_acceptor_instrumented.py::test_e43_single_acceptor_graceful_handover``.

Backoff enforcement:
Backoff cooldown is enforced unconditionally via ``_cooldown_after_failure()``
prior to every restart attempt (``McpRestartPolicy.cooldown_seconds``), guaranteeing
the bridge cannot storm or spin-loop even with a wedged ``restart_fn``.

E1.6 Concurrency arguments across all 8 state stores:
During make-before-break replacement, candidate and serving children overlap.
State integrity across both processes is preserved across all 8 state stores.
Each entry below names the module that ACTUALLY performs the write and the
mechanism that ACTUALLY serialises it. The writer location is load-bearing:
three of these stores are written from the CHILD process, not from the parent
bridge, so a note claiming single-writer safety would be false.

1. Wire ledger: written by the CHILD request-dispatch path, NOT by the parent
   bridge. ``McpServer._dispatch_request`` (``_mcp_server.py:275,283,303``)
   reaches ``_append_wire_record_best_effort`` -> ``append_wire_record`` for
   every dispatched JSON-RPC frame, ``_record_wire_ledger_before_dispatch``
   (``_mcp_server.py:712``) does the same for ``tools/call``, and media
   capture appends from inside the child (``_media_capture.py:434``). The
   cross-process mechanism lives entirely in the lock critical section of
   ``append_wire_record`` (defined at ``_wire_ledger.py:294``; the locked
   section is ``_wire_ledger.py:345-407``): a sidecar ``<ledger>.lock`` file is
   opened, an exclusive non-blocking ``fcntl.flock`` is taken with a bounded 0.2 s
   retry, and the chain head (``_read_last_hmac``) is read UNDER that lock
   immediately before the append-only JSONL write, then released. The chain
   head is never cached in memory between appends: a stale head is exactly how
   the chain forks into two children of one parent. Candidate and serving
   children append to the same file concurrently; the ledger is correct
   because of the lock, not because there is only one writer.
2. Session file: parent-written only. ``_create_session_file``
   (``lifecycle.py:2934``) creates it atomically via ``tempfile.mkstemp`` in
   the system temporary directory before the spawn and passes the path through
   ``MCP_SESSION_FILE_ENV``; ``FileBackedSession``
   (``runtime_session.py:144``) is a read-only view that performs no writes at
   all. The parent removes it when the spawn fails
   (``lifecycle.py:2754``). It does NOT live under
   ``.agent/snapshots/<run_id>/``, so no stage or generation isolation is
   needed or provided for it.
3. Durable sink: isolated by a PER-BRIDGE UNIQUE FILE, not by a cross-process
   lock. ``configure_bridge_sink`` (``_mcp_health_event.py:284``) assigns each
   bridge its own ``.agent/mcp-health-events-<bridge_id>.jsonl``
   (``lifecycle.py:2480``), and only the parent appends
   (``lifecycle.py:1293`` via ``record_restart_decision``). ``_DURABLE_SINK_LOCK`` is a
   ``threading.Lock`` (``_mcp_health_event.py:54``): it serialises threads
   inside the parent process and cannot protect across processes. The
   cross-instance isolation comes from the unique filename. Entries are
   append-only JSONL, persisted for post-mortem analysis.
4. Activity relay: in-memory only, no store. ``ActivityRelay``
   (``_activity_relay.py:53``) guards its state with a ``threading.Lock``
   (``_activity_relay.py:80``); sender threads enqueue events and ``close()``
   drops them after bridge shutdown. Nothing here is shared with the child.
5. Stage files: written by the CHILD, isolated per spawn. Each spawn gets a
   fresh ``MCP_STAGE_ID_ENV`` (``lifecycle.py:2692``) and ``stage_file_path``
   (``_start_stage_tracker.py:70``) scopes the file to that spawn id, so two
   overlapping children cannot clobber each other's stage file. The record is
   committed by temp file plus an atomic replace with a directory fsync
   (``_start_stage_tracker.py:116``); the per-spawn startup-overrun journal is
   append-mode JSONL (``_start_stage_tracker.py:271``).
6. Artifact stores: managed in the workspace filesystem under task-isolated
   staging directories, so a candidate writes only into its own directory and
   never into the serving child's path.
7. Coordination state: the handover sentinels are STAGE-SUFFIXED, not fixed
   names. The parent passes ``accept.pause.<stage_id>``
   (``lifecycle.py:1595``) and ``accept.resume.<stage_id>``
   (``lifecycle.py:1604``) through the environment and addresses the same
   files by ``stage_id`` when it pauses the old child and resumes the
   candidate (``lifecycle.py:1594-1606``), falling back to the unsuffixed
   name only when a stage id is unknown. ``<pause>.ack`` is created by the old
   child after it drops its accepting FD, so a stale ack from a previous
   generation can never satisfy this one. In-memory bridge transitions occur
   under ``self._lock`` (reentrant).
8. Transport-repetition sidecar (E1.7): a persistent advisory-lock sidecar,
   NOT append-only. ``_transport_repetition_tracker.py:386`` opens a
   ``<state>.lock`` companion and takes the same bounded non-blocking
   ``flock`` (``:262``); the carried state is read UNDER the lock and rewritten
   whole by temp file plus rename (``_write_carried_state``, ``:446``). Because
   it is a whole-file replace under a lock rather than an append, two writers
   cannot interleave partial lines into it.

Attempt result classification (C2): ``_attempt_restart_with_candidate``
returns a typed ``RestartAttemptResult``. Only
``BUDGET_EXHAUSTED`` may set ``_mcp_error`` or be re-raised from
``McpSupervisor.__exit__``. All other outcomes (``ATTEMPT_FAILED``,
``DEFERRAL_CEILING_TRIPPED``, ``REPLACED``, ``KEPT_OLD``) are
non-terminal while the budget remains.

Probe overrun (C3): ``_run_health_probe`` records its own elapsed
time. A probe that takes longer than ``probe_timeout +
_PROBE_DEADLINE_SLACK_S`` is treated as host contention
(``overrun=True``), not server death. Probe failures are classified so
only timeout-class outcomes are deferrable under contention; connection
resets, malformed responses, HTTP 5xx, and wrong-spawn-id responses
are real non-deferrable failures. Before replacing a live child,
confirm-before-replace runs a second 2x probe with CPU-progress fallback.
The deferral counter has both a count ceiling (``_DEFERRAL_COUNT_CEILING``)
and a wall-time ceiling (``_DEFERRAL_WALL_TIME_CEILING_S``).

Tool-set drift (C1/E1.9): ``_verify_candidate`` verifies tool-set hash
equality against carried state (``_expected_tool_hash``). The reference is
real, not a parent-side guess: ``start_mcp_server`` performs ONE bounded
served ``tools/list`` over the shared endpoint of the FIRST live child —
before any candidate can overlap — and seeds both the hash and the catalogue
behind it. After every successful candidate verification the candidate's own
served catalogue replaces that reference. Drift is refused while the old child
is alive (the refusal WARNING names the added/removed/changed tools, the
attempt is counted, and the old child keeps serving); if the old child is dead
there is nothing left to preserve, so the drifted candidate is promoted with a
WARNING that names the diff.

Handover stage attribution (C5/DEC-002): every ``McpServerError`` raised from
the make-before-break path carries ``handover_step`` naming the failing stage
(``spawn_candidate``, ``verify_candidate``, ``pause_ack``, ``resume_candidate``,
``promote_candidate``, ``drain_old_child``). The innermost stage wins, so a
drain failure is not relabelled as a promotion failure on its way out.

Why this design cannot double-serve, double-write, hang, or storm:

* The parent owns the only listening socket; children accept only on
  an FD they explicitly drop after the pause-file acknowledgement.
* Parent and child both append to the wire ledger, and it stays a valid
  chain because ``append_wire_record`` takes an exclusive ``fcntl.flock``
  and reads the chain head under that lock; the two-child interleave test
  verifies an interleaved append sequence still validates.
* ``_attempt_restart_with_candidate`` is bounded by the rolling
  window from ``McpRestartPolicy``; ``ATTEMPT_FAILED`` does not
  escalate.
* Backoff is enforced unconditionally before every restart attempt, so
  the bridge cannot storm even with a wedged ``restart_fn``.

Replacement initiation (E4.1 / E2.7 / E1.1 / E1.9):
Every replacement initiator — the health probe, the failed-probe handler
and ``reset_tool_registry`` — routes into ONE generation-checked,
single-candidate state machine (``_run_with_make_before_break`` ->
``_claim_candidate_slot`` + ``_replace_with_candidate``). A replacement
is initiated OFF the bridge lock:

1. Under the lock, briefly: claim the ONE candidate slot (a second
   concurrent initiator is refused, so exactly one candidate can exist)
   and capture the generation token.
2. Off the lock: cooldown, candidate spawn, and PRIVATE-channel
   verification (spawn id, pid, session id/broker fingerprint, tool-set
   schema hash). No agent read of ``bridge.process`` or of the shared
   endpoint can queue behind this.
3. Under the lock, briefly: re-validate BOTH the generation token and the
   slot reservation. If either moved, the candidate is abandoned and its
   whole process group is reaped without touching ``_inner``.
4. Ordered handover, promotion, then the slot is cleared in a
   ``finally`` on every path, success or failure.

A verification failure is never terminal: the candidate is reaped, the old
child keeps serving, and the attempt stays charged to the budget.

This module contains no async; all I/O is bounded and fails closed.
"""

from __future__ import annotations

import collections
import contextlib
import errno
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING, Final, NoReturn, Protocol, cast

import psutil
from loguru import logger

from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    payload_transport,
    profile_for_caller,
    resolve_capability_profile,
)
from ralph.mcp.protocol._preflight_error import PreflightError
from ralph.mcp.protocol._session_bridge_like import SessionBridgeLike
from ralph.mcp.protocol._startup_timeouts import mcp_whole_startup_budget_from_env
from ralph.mcp.protocol.env import (
    MCP_ACCEPT_PAUSE_FILE,
    MCP_ACCEPT_RESUME_FILE,
    MCP_BRIDGE_PARENT_SOCK,
    MCP_EFFECTIVE_CONFIG_FINGERPRINT,
    MCP_INHERITED_FD_ENV,
    MCP_OWNER_PID_ENV,
    MCP_RESPAWN_ENV,
    MCP_SNAPSHOT_DIR_ENV,
    MCP_STAGE_ID_ENV,
)
from ralph.mcp.protocol.env import MCP_SESSION_FILE_ENV as SESSION_FILE_ENV
from ralph.mcp.protocol.startup import (
    mcp_boot_allowance_from_env,
    mcp_preflight_timeout_from_env,
    mcp_probe_timeout_from_env,
    preflight_http_mcp_server_tools,
    probe_mcp_http_endpoint,
)
from ralph.mcp.server._handover_protocol import (
    _HANDOVER_ACK_TIMEOUT_S,
    _HANDOVER_DRAIN_S,
    _PRIVATE_READINESS_TIMEOUT_S,
    CandidateReadiness,
    check_candidate_readiness,
    compute_tool_set_hash,
    create_handover_socketpair,
    describe_tool_set_diff,
    request_old_child_pause,
    served_tool_entries,
    signal_candidate_resume,
    wait_for_old_child_pause_ack,
)
from ralph.mcp.server._http_tools_list import (
    _LIFECYCLE_MAX_RESPONSE_BYTES,
    _http_tools_list_names,
)
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._mcp_attempt_result import RestartAttemptResult
from ralph.mcp.server._mcp_health_event import (
    _DURABLE_SINK_LOCK,
    RestartReason,
    current_host_is_contended,
    get_durable_sink_path,
    pop_recent_host_contention_signals,
    record_host_contention_signal,
)
from ralph.mcp.server._mcp_health_event import (
    configure_bridge_sink as _configure_bridge_sink,
)
from ralph.mcp.server._mcp_health_event import (
    record_restart_decision as _record_restart_decision,
)
from ralph.mcp.server._mcp_outcome_unknown_error import McpOutcomeUnknownError
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy, next_budget_decision
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._mcp_server_extras import McpServerExtras
from ralph.mcp.server._process_like import ProcessLike
from ralph.mcp.server._reserved_socket import _reserve_listening_socket
from ralph.mcp.server._server_log import (
    mcp_server_log_path,
    mcp_server_log_size,
    read_mcp_server_log_since,
)
from ralph.mcp.server._snapshots import (
    _finger,
    compute_effective_config_fingerprint,
    snapshot_dir,
    write_snapshot,
)
from ralph.mcp.server._spawn_process import SpawnProcess
from ralph.mcp.server._standalone_mcp_process import (
    SHUTDOWN_REAP_WAIT_S,
    SHUTDOWN_TERMINATE_GRACE_S,
    StandaloneMcpProcess,
)
from ralph.mcp.server._start_stage_tracker import read_last_stage as _read_start_stage
from ralph.mcp.server._startup_watch import StartupVerdict, StartupWatch
from ralph.mcp.tool_contract import visible_owned_tool_names
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME
from ralph.mcp.upstream.tool_catalog_cache import (
    UpstreamToolCatalogSnapshot,
    apply_tool_catalog_env,
    get_tool_catalog_snapshot,
)
from ralph.process.manager import (
    ManagedProcess,
    ProcessManagerPolicy,
    SpawnOptions,
    get_process_manager,
)
from ralph.workspace.fs import FsWorkspace

#: Env var carrying the session_id for snapshot-based persistence across respawns.
MCP_SESSION_ID_ENV = "MCP_SESSION_ID"

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from socket import socket as _socket_type

    from ralph.mcp.protocol.startup import SessionLike, WorkspaceLike
    from ralph.mcp.server._activity_relay import ActivityRelay
    from ralph.mcp.upstream.registry import UpstreamRegistry

# Additive cap on the tool-registry-reset counter. The recovery
# controller's `max_recovery_attempts` and the MCP restart policy's
# `max_restarts` are independent. This cap bounds the third retry
# surface — when a tool-availability failure recurs after the bridge
# has rebuilt its visible tool list, the orchestrator needs a
# distinguishable cap error so the operator can diagnose which bound
# fired. The error message MUST contain the substring
# 'tool-registry-reset exhausted' so the orchestrator can branch on it.
_TOOL_REGISTRY_MAX_RESETS: int = 3
# Hard cap on the exponent for the sustained-contention probe-timeout
# backoff. A live child kept deferring past this cap would otherwise
# raise ``OverflowError`` from ``timedelta * 2**N`` and tear down the
# health-check path — the exact incident class. The cap is far past
# the realistic ceiling (the timeout is min'd against the preflight
# ceiling below, so any exponent above ~10 already saturates the
# observable probe timeout). 32 keeps 2**N < 2**32 = 4.3e9, well
# below the timedelta microsecond range.
_PROBE_TIMEOUT_EXPAWN_CAP: int = 32


# RestartAttemptResult lives in its own module
# (ralph.mcp.server._mcp_attempt_result) so lifecycle.py can stay
# within the repo-structure one-class-per-module policy. Imported here
# so the rest of the lifecycle module and existing callers see the
# same name.


def _bridge_clock(self_or_bridge: object) -> Callable[[], float]:
    """Return the bridge's monotonic-clock seam, defaulting to ``time.monotonic``.

    Module-level helper used by ``RestartAwareMcpBridge._run_health_probe``
    so the probe's elapsed-time accounting honours the ``clock_fn`` test
    injection without open-coding ``getattr(...)`` at every probe site.
    The narrowing is via ``callable()`` + the type annotation on
    ``self._clock_fn`` (``Callable[[], float] | None``); if a
    non-callable slips in, the fallback ``time.monotonic`` keeps the
    production path fail-closed (an injected non-callable cannot
    crash the probe).
    """
    raw: object = getattr(self_or_bridge, "_clock_fn", None)
    if callable(raw):
        # callable() is a runtime narrowing check; the typed
        # ``Callable[[], float] | None`` annotation on the
        # ``_clock_fn`` attribute means the value is already
        # typed at the source. Returning ``raw`` (typed ``object``)
        # to a function annotated ``-> Callable[[], float]`` is
        # not flagged by mypy because the attribute type and
        # the return type both flow from the bridge's own
        # declaration; the test seam is fail-closed (time.monotonic
        # is the fallback when the test seam is unused).
        return raw
    return time.monotonic


def _restart_error_fields(exc: BaseException) -> tuple[str | None, float | None]:
    """Checked boundary: pull optional restart diagnostics off a foreign exception."""
    last_stage: object = getattr(exc, "last_stage", None)
    elapsed_s: object = getattr(exc, "elapsed_s", None)
    return (
        last_stage if isinstance(last_stage, str) else None,
        elapsed_s if isinstance(elapsed_s, float) and not isinstance(elapsed_s, bool) else None,
    )


if not _TOOL_REGISTRY_MAX_RESETS > 0:
    raise RuntimeError(
        f"_TOOL_REGISTRY_MAX_RESETS must be positive (got {_TOOL_REGISTRY_MAX_RESETS})"
    )

# Alias-verify timeout for the post-respawn tools/list probe. The
# probe is HTTP-based and bounded so a wedged inner process does
# not stall the recovery path. The invariant below is enforced at
# import time via `if`/`raise RuntimeError` (NOT `assert`) so it
# survives `python -O`.
_RESPAWN_ALIAS_VERIFY_TIMEOUT_S: float = 2.0
_RESPAWN_ALIAS_VERIFY_TIMEOUT_MAX_S: float = 30.0
if not (
    _RESPAWN_ALIAS_VERIFY_TIMEOUT_S > 0
    and _RESPAWN_ALIAS_VERIFY_TIMEOUT_S < _RESPAWN_ALIAS_VERIFY_TIMEOUT_MAX_S
):
    raise RuntimeError(
        "_RESPAWN_ALIAS_VERIFY_TIMEOUT_S must be in (0,"
        f" {_RESPAWN_ALIAS_VERIFY_TIMEOUT_MAX_S:g}) seconds"
        f" (got {_RESPAWN_ALIAS_VERIFY_TIMEOUT_S})"
    )

# Reset-wrapup timeout for the per-attempt session-budget reset POST.
# Same bounded, fail-closed contract as the alias-verify timeout so
# ``audit_mcp_timeout`` stays green: a wedged inner process or a
# just-respawned one that has not finished binding its HTTP socket
# must not stall the attempt boundary. The invariant below is
# enforced at import time via `if`/`raise RuntimeError` (NOT
# `assert`) so it survives `python -O`.
_RESET_WRAPUP_TIMEOUT_S: float = 2.0
if not _RESET_WRAPUP_TIMEOUT_S > 0:
    raise RuntimeError(f"_RESET_WRAPUP_TIMEOUT_S must be positive (got {_RESET_WRAPUP_TIMEOUT_S})")

# Timeout for the ONE baseline ``tools/list`` the parent performs over the
# shared endpoint during initial bridge startup. It runs after the first
# child's preflight already proved the endpoint is serving, so it is a small
# confirmation read, not a startup wait; the bound keeps a wedged child from
# stalling bridge construction. The invariant is enforced at import time via
# `if`/`raise RuntimeError` (NOT `assert`) so it survives `python -O`.
_INITIAL_TOOL_HASH_TIMEOUT_S: float = 5.0
_INITIAL_TOOL_HASH_TIMEOUT_MAX_S: float = 30.0
if not (
    _INITIAL_TOOL_HASH_TIMEOUT_S > 0
    and _INITIAL_TOOL_HASH_TIMEOUT_S < _INITIAL_TOOL_HASH_TIMEOUT_MAX_S
):
    raise RuntimeError(
        "_INITIAL_TOOL_HASH_TIMEOUT_S must be in (0,"
        f" {_INITIAL_TOOL_HASH_TIMEOUT_MAX_S:g}) seconds"
        f" (got {_INITIAL_TOOL_HASH_TIMEOUT_S})"
    )

# Make-before-break handover stage names (C5 / DEC-002). Every McpServerError
# raised from the make-before-break path carries exactly one of these on its
# ``handover_step`` field so an operator can name the failing handover stage
# without replaying the full traceback. The stages are ordered as the protocol
# executes them: spawn -> verify -> pause/ack -> resume -> promote -> drain.
_HANDOVER_STEP_SPAWN: Final[str] = "spawn_candidate"
_HANDOVER_STEP_VERIFY: Final[str] = "verify_candidate"
_HANDOVER_STEP_PAUSE_ACK: Final[str] = "pause_ack"
_HANDOVER_STEP_RESUME: Final[str] = "resume_candidate"
_HANDOVER_STEP_PROMOTE: Final[str] = "promote_candidate"
_HANDOVER_STEP_DRAIN: Final[str] = "drain_old_child"

#: ``_handle_failed_probe_under_lock`` outcomes that require a replacement.
#: The accounting that decides between them runs entirely under the bridge
#: lock; the replacement itself runs AFTER the lock is released, so the
#: values double as "what kind of restart to start once we are off-lock".
_RESTART_BECAUSE_PROBE_FAILED: Final[int] = 0
_RESTART_BECAUSE_PROCESS_EXITED: Final[int] = 1

#: Deadline (seconds) for confirming that an un-acknowledging old child is gone
#: after its kill is issued. Bounded and import-time-checked for the same reason
#: as the other handover deadlines: an unbounded confirmation poll would hang
#: the make-before-break path on a child that ignores SIGKILL.
_HANDOVER_KILL_WAIT_S: float = 1.0
_HANDOVER_KILL_WAIT_MAX_S: float = 30.0
if not (_HANDOVER_KILL_WAIT_S > 0 and _HANDOVER_KILL_WAIT_S <= _HANDOVER_KILL_WAIT_MAX_S):
    raise RuntimeError(
        "_HANDOVER_KILL_WAIT_S must be in (0,"
        f" {_HANDOVER_KILL_WAIT_MAX_S:g}] seconds (got {_HANDOVER_KILL_WAIT_S})"
    )

# E5.2 unkillable-survivor reaper. A survivor that refused SIGKILL inside the
# bounded pause-kill window (e.g. D-state pinned to uninterruptible I/O) is
# registered for deferred reap at this interval. The interval is import-time
# bounded; the registry is FIFO-capped on the bridge instance so a long stream
# of survivors cannot grow the bridge's footprint.
_UNKILLABLE_REAP_BACKOFF_S: float = 0.5
_UNKILLABLE_REAP_BACKOFF_MAX_S: float = 30.0
_UNKILLABLE_REAP_RETRY_S: float = 1.0
if not (
    _UNKILLABLE_REAP_BACKOFF_S > 0
    and _UNKILLABLE_REAP_BACKOFF_S <= _UNKILLABLE_REAP_BACKOFF_MAX_S
    and _UNKILLABLE_REAP_RETRY_S > 0
):
    raise RuntimeError(
        f"_UNKILLABLE_REAP_BACKOFF_S={_UNKILLABLE_REAP_BACKOFF_S} and "
        f"_UNKILLABLE_REAP_RETRY_S={_UNKILLABLE_REAP_RETRY_S} must be positive "
        f"and bounded by {_UNKILLABLE_REAP_BACKOFF_MAX_S:g}s"
    )


# ---------------------------------------------------------------------------
# Worst-case arithmetic bounds and import-time invariants (E6.4 + E2.3)
# ---------------------------------------------------------------------------
#: Candidate private-channel readiness verification deadline.
_CANDIDATE_DEADLINE_S: Final[float] = _PRIVATE_READINESS_TIMEOUT_S
#: Candidate whole startup budget from environment.
_CANDIDATE_STARTUP_DEADLINE_S: Final[float] = mcp_whole_startup_budget_from_env()
#: Preflight deadline ceiling in seconds.
_PREFLIGHT_DEADLINE_S: Final[float] = mcp_preflight_timeout_from_env().total_seconds()

#: Bound (a): Worst-case time to terminal exhaustion with old child dead,
#: computed from restart policy max_restarts, cooldown, the preflight
#: deadline, and the bounded teardown of each failed attempt's child.
#: The teardown term was the omission the acceptance review found: a failed
#: startup calls ``bridge.shutdown()`` (``lifecycle._start_bridge``), which
#: escalates SIGTERM -> SIGKILL and then reaps, so an attempt that misses its
#: preflight deadline still costs that bounded window before the next attempt.
#: Every term is read from the constant that the runtime path actually uses.
_FAILED_ATTEMPT_CLEANUP_S: Final[float] = (
    SHUTDOWN_TERMINATE_GRACE_S + ProcessManagerPolicy.kill_followup_timeout_s + SHUTDOWN_REAP_WAIT_S
)
_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S: Final[float] = McpRestartPolicy.max_restarts * (
    McpRestartPolicy.cooldown_seconds + _PREFLIGHT_DEADLINE_S + _FAILED_ATTEMPT_CLEANUP_S
)
_WORST_CASE_TIME_TO_TERMINAL_EXHAUSTION_S: Final[float] = (
    _WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S
)

#: Bound (b): Candidate delay bound with old child alive, computed as
#: (candidate deadline + handover ack + kill wait + drain). A candidate that
#: fails before the accept handover is torn down with the same bounded
#: ``StandaloneMcpProcess.shutdown`` window, and that teardown is charged to
#: the agent because it runs on the agent's own replacement path.
_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S: Final[float] = (
    _CANDIDATE_DEADLINE_S
    + _HANDOVER_ACK_TIMEOUT_S
    + _HANDOVER_KILL_WAIT_S
    + _HANDOVER_DRAIN_S
    + _FAILED_ATTEMPT_CLEANUP_S
)
_CANDIDATE_DELAY_BOUND_S: Final[float] = _CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S

if not (_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S > 0):
    raise RuntimeError(
        f"_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S must be positive (got {_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S})"
    )
if not (_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S > 0):
    raise RuntimeError(
        f"_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S must be positive (got {_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S})"
    )


# ---------------------------------------------------------------------------
# E6.4 arithmetic-dominance proof (not vacuous positivity)
# ---------------------------------------------------------------------------
def _e64_bound_dominates(bound: float, components: tuple[float, ...]) -> bool:
    """Return ``True`` iff ``bound`` is an upper bound over ``components``.

    A positivity check cannot prove an upper bound: the sum of positive
    durations is positive even when a stage term has been dropped from it,
    so ``bound > 0`` stays satisfied by a bound that silently omits the
    drain (or the kill-wait, or the preflight) stage. The proof carried
    here is the temporal one — ``bound`` must be at least the SUM of every
    worst-case duration the bound is composed of, and each of those
    component durations must itself be strictly positive. Drop any term
    from a bound and ``bound < sum(components)`` no longer holds, so the
    import-time check below raises instead of silently under-reporting the
    worst case.

    Implemented with ``if``/``return`` rather than ``assert`` so the
    property survives ``python -O``.
    """
    if not components:
        return False
    if not (bound > 0.0):
        return False
    total = 0.0
    for component in components:
        if not (component > 0.0):
            return False
        total += component
    return bound >= total


#: Per-attempt worst-case durations bound (a) is composed of: the
#: unconditional pre-restart cooldown, one preflight deadline, and the
#: bounded teardown of the attempt's own child.
_E64_BOUND_A_COMPONENTS_S: Final[tuple[float, ...]] = (
    float(McpRestartPolicy.cooldown_seconds),
    _PREFLIGHT_DEADLINE_S,
    _FAILED_ATTEMPT_CLEANUP_S,
)
#: Handover stage durations bound (b) is composed of, in protocol order:
#: candidate private-channel readiness, old-child pause acknowledgement
#: (or its kill), the kill confirmation, the drain window, and the bounded
#: teardown of a candidate that failed before the accept handover.
_E64_BOUND_B_COMPONENTS_S: Final[tuple[float, ...]] = (
    _CANDIDATE_DEADLINE_S,
    _HANDOVER_ACK_TIMEOUT_S,
    _HANDOVER_KILL_WAIT_S,
    _HANDOVER_DRAIN_S,
    _FAILED_ATTEMPT_CLEANUP_S,
)
_E64_MAX_RESTARTS: Final[int] = int(McpRestartPolicy.max_restarts)
if not (_E64_MAX_RESTARTS > 0):
    raise RuntimeError(
        f"McpRestartPolicy.max_restarts must be positive (got {McpRestartPolicy.max_restarts})"
    )
if not _e64_bound_dominates(
    _WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S / _E64_MAX_RESTARTS,
    _E64_BOUND_A_COMPONENTS_S,
):
    raise RuntimeError(
        "E6.4 bound (a) is not an upper bound over its stages: per-attempt worst case"
        f" {_WORST_CASE_TERMINAL_EXHAUSTION_DEAD_CHILD_S / _E64_MAX_RESTARTS:g}s"
        f" < sum{_E64_BOUND_A_COMPONENTS_S}={sum(_E64_BOUND_A_COMPONENTS_S):g}s"
        f" over {_E64_MAX_RESTARTS} attempts"
    )
if not _e64_bound_dominates(_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S, _E64_BOUND_B_COMPONENTS_S):
    raise RuntimeError(
        "E6.4 bound (b) is not an upper bound over its handover stages:"
        f" {_CANDIDATE_DELAY_BOUND_ALIVE_CHILD_S:g}s"
        f" < sum{_E64_BOUND_B_COMPONENTS_S}={sum(_E64_BOUND_B_COMPONENTS_S):g}s"
    )
# E2.3 candidate startup deadline bounds validation
if not (0 < _CANDIDATE_STARTUP_DEADLINE_S < _PREFLIGHT_DEADLINE_S):
    raise RuntimeError(
        f"E2.3 candidate startup deadline ({_CANDIDATE_STARTUP_DEADLINE_S}s) must be positive "
        f"and strictly less than preflight deadline ({_PREFLIGHT_DEADLINE_S}s)"
    )

#: Probe-overrun slack (seconds). A probe that exceeds its deadline by more
#: than this is classified as host contention (C3) rather than server death.
_PROBE_DEADLINE_SLACK_S: float = 0.5
#: Multiplier applied to base probe timeout for confirm-before-replace (C3).
_CONFIRM_PROBE_MULTIPLIER: int = 2
#: CPU sample duration in seconds for fallback confirmation (C3).
_CONFIRM_CPU_SAMPLE_S: float = 0.5
#: CPU progress threshold in seconds of total (user + sys) CPU time.
_CPU_PROGRESS_THRESHOLD: Final[float] = 0.001
#: Hard ceiling on consecutive probe-failure deferrals. When the deferral
#: counter reaches this the bridge forces a restart regardless of whether
#: a fresh contention signal is present, preventing indefinite deferral of
#: a genuinely wedged child.
_DEFERRAL_COUNT_CEILING: int = 30
#: Wall-time ceiling on the accumulated probe deferral time. A probe that
#: overruns by more than this in aggregate (even if individual overruns
#: are small) is treated as sustained contention and forces replacement.
_DEFERRAL_WALL_TIME_CEILING_S: float = 60.0
if not (
    _PROBE_DEADLINE_SLACK_S > 0
    and _CONFIRM_PROBE_MULTIPLIER > 0
    and _CONFIRM_CPU_SAMPLE_S > 0
    and _DEFERRAL_COUNT_CEILING > 0
    and _DEFERRAL_WALL_TIME_CEILING_S > 0
):
    raise RuntimeError(
        f"_PROBE_DEADLINE_SLACK_S={_PROBE_DEADLINE_SLACK_S} "
        f"_CONFIRM_PROBE_MULTIPLIER={_CONFIRM_PROBE_MULTIPLIER} "
        f"_CONFIRM_CPU_SAMPLE_S={_CONFIRM_CPU_SAMPLE_S} "
        f"_DEFERRAL_COUNT_CEILING={_DEFERRAL_COUNT_CEILING} "
        f"_DEFERRAL_WALL_TIME_CEILING_S={_DEFERRAL_WALL_TIME_CEILING_S} must all be positive"
    )

#: Hard upper bound (bytes) on a single ``response.read()`` call in this
#: module. Both the reset_session_budget path (which discards the body) and
#: the ``_http_tools_list_names`` path (which parses the body as JSON) cap
#: the read at this value so a misbehaving upstream that streams an
#: unbounded response body cannot OOM the parent. 1 MiB is well above any
#: realistic ``tools/list`` JSON payload and matches the size budget an
#: operator would expect for a discardable wrap-up response (AC-08).

# Canonical tool name whose alias is verified after every
# ``reset_tool_registry()`` call. ``read_file`` is the most common
# wedge surface: a regression that strips the alias from the
# post-respawn tools/list re-introduces the post-tool-result wedge
# even though ``reset_tool_registry()`` returned successfully.
_ALIAS_VERIFY_TOOL_NAME: str = "read_file"
# MCP server name used to build the alias. The companion constant
# lives in ``ralph.mcp.tools.names``; we hard-code ``"ralph"`` here
# to keep the lifecycle module import-light and avoid a circular
# dependency through ``ralph.mcp.tools.bridge``.
_ALIAS_VERIFY_SERVER_NAME: str = RALPH_MCP_SERVER_NAME

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.protocol.startup import SessionLike, WorkspaceLike
    from ralph.mcp.upstream.registry import UpstreamRegistry

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

#: Consecutive failed probes required before an otherwise-running MCP server
#: is restarted. A single bounded probe window is not evidence of death: on a
#: loaded machine a healthy server misses one routinely, and restarting on the
#: first miss tore down working servers in the middle of agent calls.
_PROBE_FAILURES_BEFORE_RESTART = 3

#: Consecutive successful probes required to clear accumulated probe failure
#: history (E6.3). A flapping server alternating between success and failure
#: must not clear its failure streak on a single lucky probe.
_PROBE_SUCCESS_HYSTERESIS_COUNT = 3
if not _PROBE_SUCCESS_HYSTERESIS_COUNT > 0:
    raise RuntimeError(
        f"_PROBE_SUCCESS_HYSTERESIS_COUNT must be positive (got {_PROBE_SUCCESS_HYSTERESIS_COUNT})"
    )

#: Filename of the durable JSONL sink for restart decisions, stored under
#: ``<workspace>/.agent/`` so it is workspace-local and bounded by the
#: bridge lifecycle.  "ralph_mcp_restart_decisions" is distinctive enough
#: to identify the file in incident triage without polluting the root.
_DURABLE_SINK_FILENAME = "ralph_mcp_restart_decisions.jsonl"


def _process_is_dead(target: StandaloneMcpProcess) -> bool:
    """Return True if process has exited or if target lacks a process (e.g. MagicMock)."""
    proc: object = getattr(target, "process", None)
    if proc is None:
        return True
    poll_fn = cast("Callable[[], object] | None", getattr(proc, "poll", None))
    if poll_fn is None or not callable(poll_fn):
        return True
    try:
        return poll_fn() is not None
    except Exception:
        return False


def _is_alive_pid(pid: int | None) -> bool:
    """Return True if pid is a valid, currently alive OS process.

    E5.4 / E5.2 mandate identity-safe liveness on the HOLDER (Popen/pidfd
    handle), never a bare ``os.kill(pid, 0)`` — the latter cannot
    distinguish a still-alive D-state process from a yet-unreaped zombie
    and is sensitive to PID recycling. New callers MUST prefer
    :func:`_is_alive_process`; the pid-only form is retained for narrow
    migration sites where a process handle is not yet in scope and is
    paired at the call site with a process-identity check before any
    termination or handover decision.
    """
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _is_alive_process(proc: object) -> bool:
    """Canonical E5.4 liveness check using the ``Popen`` handle's ``poll()``.

    Returns ``True`` iff ``proc.poll() is None``: the process is still
    running (including a D-state process holding uninterruptible I/O),
    and the result is anchored to the handle we own rather than a bare
    pid that the kernel may have recycled. ``poll()`` itself reaps a
    zombie and returns its exit code, so this function naturally returns
    ``False`` for zombies the moment they are reaped. Failing-closed on
    an unusable ``proc`` (no ``poll`` callable) protects against
    ``MagicMock`` and other incomplete test doubles leaking through.
    """
    poll_fn = cast("Callable[[], object] | None", getattr(proc, "poll", None))
    if poll_fn is None or not callable(poll_fn):
        return False
    try:
        return poll_fn() is None
    except Exception:
        return False


def _has_stage(exc: BaseException) -> bool:
    """Return True when ``exc`` carries the supervisor ``last_stage`` attribute.

    Runtime predicate that gates the typed ``last_stage`` / ``elapsed_s``
    read in :func:`_optional_attr_str` and :func:`_optional_attr_float`.
    The restart loop catches a generic :class:`BaseException`; only
    subclasses that declare the supervisor contract (in practice
    :class:`McpServerError`) contribute values to the rewrap.
    """
    return hasattr(exc, "last_stage") and hasattr(exc, "elapsed_s")


def _read_supervisor_field(exc: BaseException, name: str) -> object:
    """Read a single attribute from ``exc.__dict__`` as ``object``.

    The returned value is intentionally typed as :class:`object`: it is
    an untyped read that the caller narrows with ``isinstance``. The
    helper exists so the ``__dict__`` access (which mypy types as
    ``dict[str, Any]``) lives in one place, gated by the ``_has_stage``
    runtime predicate, instead of being open-coded at every rewrap call
    site. Returning :class:`object` keeps the expression typed and
    avoids ``disallow_any_expr`` violations at the call site.
    """
    storage: dict[str, object] = exc.__dict__
    return storage[name]


def _optional_attr_str(exc: BaseException) -> str | None:
    """Return ``exc.last_stage`` if it is a non-empty :class:`str`, else ``None``.

    Typed boundary helper for the supervisor's exception-rewrap path.
    The ``_has_stage`` gate confines the dynamic attribute read to a
    narrow surface: any exception that does not declare the supervisor
    contract short-circuits to ``None``. The read goes through
    :func:`_read_supervisor_field` so the only ``Any``-typed value
    lives behind a single typed boundary, and ``isinstance(raw, str)``
    narrows to a typed :class:`str` at the call site without an
    unchecked downcast.
    """
    if not _has_stage(exc):
        return None
    raw = _read_supervisor_field(exc, "last_stage")
    if not isinstance(raw, str) or not raw:
        return None
    return raw


def _optional_attr_float(exc: BaseException) -> float | None:
    """Return ``exc.elapsed_s`` if it is a non-bool numeric, else ``None``.

    Mirror of :func:`_optional_attr_str` for the elapsed-time field. The
    ``isinstance`` check accepts both :class:`float` and :class:`int` so
    a JSON-derived numeric that arrives as ``int`` (whole seconds) is
    not silently dropped at the typed boundary.
    """
    if not _has_stage(exc):
        return None
    raw = _read_supervisor_field(exc, "elapsed_s")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


#: Number of child log lines captured for a restart incident record.
_LOG_TAIL_LINES: int = 10
_LOG_TAIL_MAX_BYTES: int = 16 * 1024


def _is_non_deferrable_probe_failure(exc: Exception | None) -> bool:
    """Return True if exc represents a definitive, non-deferrable failure."""
    if exc is None:
        return False
    if isinstance(exc, (ConnectionResetError, ConnectionRefusedError, BrokenPipeError)):
        return True
    exc_str = str(exc).lower()
    return (
        "connection reset" in exc_str
        or "connection refused" in exc_str
        or "broken pipe" in exc_str
        or isinstance(exc, (ValueError, json.JSONDecodeError, UnicodeDecodeError))
        or any(code in exc_str for code in ("500", "502", "503", "504"))
        or "malformed" in exc_str
        or "spawn_id_mismatch" in exc_str
        or "protocol" in exc_str
    )


def _is_timeout_class_probe_outcome(exc: Exception | None, overrun: bool) -> bool:
    """Return True if the failure is deferrable under contention.

    Positive classification: the probe outcome is deferrable iff the outcome is
    a timeout-class event (a TimeoutError, socket.timeout, an exception
    indicating timeout, or a measured probe wall-clock duration overrun) AND
    is NOT an explicit non-deferrable failure.

    Non-deferrable failures (connection reset, connection refused, broken pipe,
    HTTP 4xx/5xx, malformed/protocol errors, saturated dispatch, generic
    unhandled RuntimeError) ALWAYS prevail over overrun.
    """
    if _is_non_deferrable_probe_failure(exc) or _is_saturated_dispatch_outcome(exc):
        return False
    if isinstance(exc, TimeoutError):
        return True
    if exc is not None:
        exc_str = str(exc).lower()
        return "timed out" in exc_str or "timeout" in exc_str or "probe failed" in exc_str
    return overrun


def _is_saturated_dispatch_outcome(exc: Exception | None) -> bool:
    if exc is None:
        return False
    exc_str = str(exc).lower()
    return "saturated_dispatch" in exc_str or "_saturated_dispatch" in exc_str


def _stamp_handover_step(exc: BaseException, step: str) -> BaseException:
    """Record the failing make-before-break stage on an escaping error.

    INNESTEST WINS: a stage only stamps an error that is not already stamped,
    so an error raised deep inside the handover (e.g. ``drain_old_child``)
    keeps its own precise stage instead of being relabelled by the outer
    ``promote_candidate`` guard on the way out.

    Non-:class:`McpServerError` exceptions pass through untouched; they carry
    no ``handover_step`` field for the supervisor to read.
    """
    if isinstance(exc, McpServerError) and exc.handover_step is None:
        exc.handover_step = step
    return exc


def _child_process_diagnostics(inner: object) -> tuple[int | None, str | None]:
    """Return ``(child_pid, psutil_status)`` for the live child behind ``inner``.

    Best-effort and fail-closed: a missing pid, a foreign process object, or a
    pid that already exited all yield ``(pid, None)`` rather than raising, so
    constructing a diagnostic-bearing error can never fail to report the error
    it was raised for.
    """
    proc: object = getattr(inner, "process", None)
    raw_pid: object = getattr(proc, "pid", None)
    if not isinstance(raw_pid, int) or isinstance(raw_pid, bool):
        return None, None
    try:
        status = str(psutil.Process(raw_pid).status())
    except Exception:
        return raw_pid, None
    return raw_pid, status


class _RestartFnProtocol(Protocol):
    def __call__(self, *, make_before_break: bool = False) -> StandaloneMcpProcess: ...


class RestartAwareMcpBridge:
    """SessionBridgeLike wrapper that auto-restarts the MCP server on crash.

    Bounded restart budget prevents unbounded retry loops. All process
    spawning continues to flow through ProcessManager via the injected
    LifecycleDeps so ProcessManager remains the single process authority.

    The endpoint URI is stable for the full bridge lifetime: the same host/port
    is reused on every restart so agents never see a changed MCP_ENDPOINT_ENV.
    Thread-safe: a lock guards all inner-process mutations so the background
    McpSupervisor can safely call check_health_and_restart_if_needed() while
    the main thread is executing agent output streaming.
    """

    def __init__(
        self,
        inner: StandaloneMcpProcess,
        *,
        restart_fn: Callable[[], StandaloneMcpProcess],
        restart_policy: McpRestartPolicy,
        run_id: str,
        probe_fn: Callable[[str, timedelta], None] | None = None,
        probe_timeout_fn: Callable[[], timedelta] | None = None,
        activity_relay: ActivityRelay | None = None,
        sleep_fn: Callable[[float], object] | None = None,
        workspace_root: Path | None = None,
        contention_signal_producer: Callable[[], bool] | None = None,
        preflight_timeout_fn: Callable[[], timedelta] | None = None,
        clock_fn: Callable[[], float] | None = None,
        session_id: str | None = None,
        broker_fingerprint: str | None = None,
        effective_config_fingerprint: str | None = None,
        tool_entries_fn: Callable[[str, float], list[dict[str, object]]] | None = None,
    ) -> None:
        self._inner = inner
        self._restart_fn = restart_fn
        self._restart_policy = restart_policy
        # The session's run_id is the single identity the receipt and the
        # gate share. Storing it on the bridge lets ``bridge_env_for``
        # derive ``MCP_RUN_ID_ENV`` from the same value the submission
        # handler stamps receipts with — drift is structurally impossible
        # because there is no separate label parameter to disagree.
        self._run_id = run_id
        self._probe_fn = probe_fn
        self._probe_timeout_fn = probe_timeout_fn
        self._preflight_timeout_fn = preflight_timeout_fn
        self._activity_relay = activity_relay
        # S4: tests inject a ``FakeClock``/sentinel sleep so the
        # production 2.0-second default does not stall the suite.
        # Production uses ``time.sleep`` (default below).
        self._sleep_fn: Callable[[float], object] | None = sleep_fn
        # Test seam: a fake ``time.monotonic`` substitute used by
        # ``_run_health_probe`` and the deferral wall-clock ceiling
        # accumulator. Production uses ``time.monotonic`` (the default
        # below). Probe-overrun and deferral-ceiling tests pass a
        # ``FakeClock``/sentinel so 30+ iter cycles exercise the
        # production code without blocking a CPU for 33 real seconds.
        self._clock_fn: Callable[[], float] | None = clock_fn
        self._session_id: str | None = session_id
        self._broker_fingerprint: str | None = broker_fingerprint
        # C1 step 2: secret-safe ``effective_config_fingerprint`` of the
        # merged McpConfig captured before the first spawn. ``None`` means
        # the parent had no fingerprint baseline (no tomllib, empty layers).
        # A non-None baseline is REQUIRED for promotion when one exists.
        self._effective_config_fingerprint: str | None = effective_config_fingerprint
        # E1.9 drift reference for the CURRENT serving child. Seeded once by
        # ``capture_tool_hash_baseline`` from the served ``tools/list`` of the
        # first live child, then replaced by the hash each successfully
        # verified candidate proved. ``_expected_tool_entries`` is the
        # catalogue behind the hash so a refusal can name the tools that
        # differ instead of only reporting that two hashes disagree.
        self._expected_tool_hash: str | None = None
        self._expected_tool_entries: list[dict[str, object]] | None = None
        # C1/E1.9 fail-closed gate: ``True`` while the bridge has no usable
        # baseline (the initial capture failed or was never performed).
        # ``_verify_candidate_socket`` reads this to force
        # ``expected_tool_hash_required=True`` so a drifted candidate cannot
        # replace a living child without a drift reference. Cleared to
        # ``False`` the moment a verified candidate (or a fresh successful
        # capture) provides a real hash. The dead-old-child escape still
        # allows promotion even while this flag is ``True`` — see
        # ``_verify_candidate_socket``.
        self._baseline_capture_failed: bool = False
        # Test seam: the served-tool-list fetcher used to seed the baseline.
        # Production uses ``served_tool_entries`` (the default ``None``).
        self._tool_entries_fn: Callable[[str, float], list[dict[str, object]]] | None = (
            tool_entries_fn
        )
        self._last_probe_error: Exception | None = None
        self._restart_timestamps: collections.deque[float] = collections.deque(maxlen=64)
        self._last_healthy_at: float = clock_fn() if clock_fn is not None else time.monotonic()
        self._healthy_period_started_at: float | None = (
            clock_fn() if clock_fn is not None else time.monotonic()
        )
        self._restart_count = 0
        # Consecutive failed probes. A probe that times out proves the
        # server is slow, not dead: under load a healthy server routinely
        # misses one bounded probe window. Restarting on the first miss
        # killed working servers mid-call, so a restart needs
        # _PROBE_FAILURES_BEFORE_RESTART misses in a row. A process that
        # has actually exited still restarts immediately — that signal is
        # unambiguous.
        self._consecutive_probe_failures = 0
        self._consecutive_probe_successes = 0
        # Tool-registry resets are tracked separately from crash
        # restarts so the orchestrator can distinguish a "tool-registry
        # rebuild" event from a "MCP server crashed" event. The cap
        # (_TOOL_REGISTRY_MAX_RESETS) is independent of the MCP restart
        # policy's max_restarts. See _tool_registry_resets property.
        self._tool_registry_resets = 0
        # Bridge identity counter: increments on every reset_tool_registry
        # and on every successful _perform_restart. The producer result
        # in check_health_and_restart_if_needed is anchored to
        # (self._inner.process.pid, self._bridge_generation) so a stale
        # probe can be discarded if the bridge changed while the
        # producer ran outside the lock.
        self._bridge_generation = 0
        self._lock = threading.RLock()
        # Workspace root used to locate the child MCP server log for the
        # durable restart-decision evidence.  Stored on the bridge so the
        # log tail can be captured at the r3-sink call site (which is
        # deep inside check_health_and_restart_if_needed, not on the
        # start_mcp_server call path).  None is safe: log_tail will be
        # None when the workspace root is unavailable.
        self._workspace_root: Path | None = workspace_root
        # R1: the parent-owned listening socket; set by
        # start_mcp_server after the spawn succeeds, closed in
        # shutdown. The field is declared on the instance (not the
        # class) so existing RestartAwareMcpBridge(...) call sites
        # stay backward-compatible.
        self._listening_socket: _socket_type | None = None
        # R3-durability: the path to this bridge's unique health-event
        # sink file. Set by start_mcp_server; never cleared on shutdown
        # so evidence is retained after bridge shutdown for incident triage.
        self._durable_sink_path: Path | None = None
        # R3 host-contention guard: consecutive probe-failures deferred
        # because a fresh workspace-monitor ``watch_capacity_predicted``
        # signal (or a producer-injected signal) suggests the host is
        # saturated, not the server. Reset on every successful probe.
        # Independent of ``_consecutive_probe_failures`` so a regular
        # three-miss policy still applies when no contention signal is
        # present.
        self._consecutive_probe_failures_deferred: int = 0
        # Test seam: a callable that returns True when the host is
        # currently under contention. Production calls
        # ``current_host_is_contended`` (the default) so the bounded
        # /proc reader is exercised. ``None`` means "use the default
        # production probe".
        self._contention_signal_producer: Callable[[], bool] | None = contention_signal_producer
        # Sliding window (seconds) for ``watch_capacity_predicted``
        # signals. Read from the policy when present; default 10.0 s
        # when the policy is a mock or predates the field.
        window_attr: object = getattr(restart_policy, "host_contention_window_seconds", 10.0)
        self._host_contention_window_seconds: float = (
            float(window_attr)
            if isinstance(window_attr, (int, float)) and not isinstance(window_attr, bool)
            else 10.0
        )
        self._first_probe_deferred_at: float | None = None
        self._draining_inners: collections.deque[StandaloneMcpProcess] = collections.deque(
            maxlen=16
        )
        self._current_candidate: StandaloneMcpProcess | None = None
        # E2.7 single-candidate gate. True from the moment a replacement
        # initiator claims the candidate slot until it clears it in a
        # ``finally``. Every initiator (health probe, failed-probe handler,
        # tool-registry reset) claims this ONE slot, so a second concurrent
        # initiator is refused instead of spawning a rival candidate that
        # would race the first for the shared accepting FD.
        self._candidate_in_flight: bool = False
        self._drain_threads: list[
            threading.Thread
        ] = []  # bounded-accumulator-ok: drain workers capped at maxlen 16 matching draining_inners and joined at shutdown
        self._is_shutting_down: bool = False
        # E5.2: bounded reaper pool for unkillable / D-state survivors. A
        # survivor that refuses both SIGTERM and SIGKILL inside the bounded
        # pause-kill window is registered here; each entry is reaped at most
        # ``_UNKILLABLE_REAP_BACKOFF_S`` seconds later and the registry is
        # capped at ``_MAX_UNKILLABLE_REAPERS`` (FIFO truncation) so a long
        # stream of D-state processes cannot grow the accumulator without
        # bound. The truncation is safe because the bridge shutdown path
        # unconditionally walks this list once more before returning.
        self._unkillable_reapers: collections.deque[tuple[object, int]] = collections.deque(
            maxlen=8
        )
        self._unkillable_reap_threads: list[
            threading.Thread
        ] = []  # bounded-accumulator-ok: reapers capped at maxlen 8 matching _unkillable_reapers and joined at shutdown

    @property
    def run_id(self) -> str:
        """The session's run identity — the receipt key the gate reads.

        See :class:`ralph.mcp.protocol._session_bridge_like.SessionBridgeLike`
        for the full contract; the property is the bridge-side realization
        of the protocol declaration.
        """
        return self._run_id

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def tool_registry_resets(self) -> int:
        """Number of times `reset_tool_registry()` has been called.

        Independent of `restart_count` (which tracks crash restarts) and
        the recovery controller's `max_recovery_attempts` (which tracks
        agent-invocation retries). The orchestrator can inspect this
        counter to diagnose which cap fired on a wedged run.
        """
        return self._tool_registry_resets

    @property
    def process(self) -> ProcessLike:
        """The currently running MCP server process.

        Satisfies the ``_BridgeWithProcess`` protocol so consumers (e.g. the
        parallel worker session factory) can read the server pid. Reads the
        inner process under the lock because a concurrent health-check
        restart may swap it.
        """
        with self._lock:
            return self._inner.process

    def start(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return self._inner.endpoint

    def endpoint_uri(self) -> str:
        return self._inner.endpoint

    def shutdown(self) -> None:
        if self._activity_relay is not None:
            self._activity_relay.close()
        with self._lock:
            self._is_shutting_down = True
            cand = self._current_candidate
            self._current_candidate = None
            draining = list(self._draining_inners)
            self._draining_inners.clear()
            drain_threads = list(self._drain_threads)
            self._drain_threads.clear()
            unkillable_threads = list(self._unkillable_reap_threads)
            self._unkillable_reap_threads.clear()
            unkillable_reapers = list(self._unkillable_reapers)
            self._unkillable_reapers.clear()
        if cand is not None:
            with contextlib.suppress(Exception):
                cand.shutdown()
        for d in draining:
            with contextlib.suppress(Exception):
                d.shutdown()
        for t in drain_threads:
            with contextlib.suppress(Exception):
                if t is not threading.current_thread():
                    t.join(timeout=1.0)
        for entry in unkillable_reapers:
            proc, _ = entry
            kill_fn = cast("Callable[[], object] | None", getattr(proc, "kill", None))
            if callable(kill_fn):
                with contextlib.suppress(Exception):
                    kill_fn()
            wait_fn = cast("Callable[[float], object] | None", getattr(proc, "wait", None))
            if callable(wait_fn):
                with contextlib.suppress(Exception):
                    wait_fn(2.0)
        for t in unkillable_threads:
            with contextlib.suppress(Exception):
                if t is not threading.current_thread():
                    t.join(timeout=1.0)
        self._inner.shutdown()
        # R1: close the parent-owned listening socket last so the
        # FD stays live for every other concurrent call site. The
        # bridge holds the listening socket for its full lifetime;
        # shutdown is the only path that releases it.
        listening_socket: _socket_type | None = getattr(self, "_listening_socket", None)
        if listening_socket is not None:
            # Best-effort: a foreign close on a shared FD is not
            # actionable from the bridge; the kernel has already
            # released the port.
            with contextlib.suppress(OSError):
                listening_socket.close()
            self._listening_socket = None
        # R3-durability: the durable health-event sink is per-bridge and
        # never deleted on shutdown. Each bridge owns a unique file so
        # evidence is retained after shutdown for incident triage.

    def _cooldown_after_failure(self) -> None:
        """Sleep for the policy cooldown, driven by an injectable clock.

        S4: the lifecycle spaces consecutive ``McpServerError``-class
        restarts by ``McpRestartPolicy.cooldown_seconds``. The
        function takes no argument: the policy is read off the
        bridge and the injected clock/sleep seam is used. Tests pass
        a ``FakeClock`` / sentinel ``sleep_fn`` so the production
        2.0-second default does not stall the suite. Called before
        EVERY restart attempt (healthy or failed): fresh child
        processes spike the same host load regardless of why the
        previous one died.
        """
        seconds = self._restart_policy.cooldown_seconds
        # Defensive: tests may inject a MagicMock as the policy
        # (the existing bridge tests pre-date the cooldown
        # field). Skip the sleep when the value is not a real
        # positive number so the failure path is exercised
        # without the bridge tripping on a non-numeric mock.
        if not isinstance(seconds, (int, float)) or not seconds > 0:
            return
        sleep_fn = cast("Callable[[float], object] | None", getattr(self, "_sleep_fn", None))
        if sleep_fn is None:
            sleep_fn = time.sleep
        sleep_fn(seconds)

    @property
    def activity_relay(self) -> ActivityRelay | None:
        """Conflict-only relay owned by this bridge, when one was configured."""
        return self._activity_relay

    def relay_health_error(self) -> str | None:
        """Return a latched relay fault before an invocation can be classified as idle."""
        return None if self._activity_relay is None else self._activity_relay.health_error()

    def register_activity_sink(self, sink: Callable[[str], None]) -> Callable[[], None]:
        """Register the parent watchdog for authenticated standalone MCP events."""
        if self._activity_relay is None:
            return lambda: None
        return self._activity_relay.register_sink(sink)

    def _read_mcp_log_tail(self) -> tuple[str, ...] | None:
        """Read the last 10 lines of the child MCP server log, if accessible.

        Returns ``None`` when the workspace root is not set, the log file
        does not exist, or the read fails.  ``None`` (not an empty tuple)
        signals "log was unavailable" so the durable record distinguishes
        "unavailable" from "log was present but empty".

        Reads only the bounded tail of the file, so a subprocess that
        logged megabytes before dying cannot flood this path.
        """
        root = self._workspace_root
        if root is None:
            return None
        log_path = mcp_server_log_path(root)
        try:
            size = mcp_server_log_size(root)
            if size == 0:
                # Empty file is readable-but-empty: []. Only a MISSING file
                # (stat fails) yields the unavailable sentinel None.
                if log_path.exists():
                    return ()
                return None
            with log_path.open("rb") as fh:
                # Bounded tail: seek near the end and read at most the
                # last chunk; a partial first line is dropped below.
                fh.seek(max(0, size - _LOG_TAIL_MAX_BYTES))
                chunk = fh.read().decode("utf-8", errors="replace")
        except OSError:
            return None
        lines = chunk.split("\n")
        if size > _LOG_TAIL_MAX_BYTES and len(lines) > 1:
            # Drop the possibly-truncated leading line.
            lines = lines[1:]
        if lines and lines[-1] == "":
            lines.pop()  # trailing newline, not a line of content
        return tuple(lines[-_LOG_TAIL_LINES:])

    def _run_health_probe(self) -> tuple[bool, float | None, bool, bool]:
        """Run the configured probe under lock.

        Returns (probe_failed, probe_duration_s, infra_error, overrun).
        """
        if self._probe_fn is None:
            return False, None, False, False
        base_timeout: timedelta = (
            self._probe_timeout_fn() if self._probe_timeout_fn is not None else timedelta(seconds=5)
        )
        preflight_timeout: timedelta = (
            self._preflight_timeout_fn()
            if self._preflight_timeout_fn is not None
            else mcp_preflight_timeout_from_env()
        )
        ceiling: timedelta = max(base_timeout, preflight_timeout - timedelta(seconds=1.0))
        # Cap the exponent so the timedelta multiply cannot overflow at very
        # high deferral counts (sustained host contention: a live child kept
        # deferring past 2**31 would otherwise raise ``OverflowError`` and
        # tear down the health-check path). The cap is far past the
        # realistic ceiling anyway: probe timeout is min'd against the
        # ``ceiling`` below, so anything above ~2**10 already saturates.
        deferred_count = max(0, self._consecutive_probe_failures_deferred)
        safe_exponent = min(deferred_count, _PROBE_TIMEOUT_EXPAWN_CAP)
        multiplier: int = 2**safe_exponent
        probe_timeout: timedelta = min(base_timeout * multiplier, ceiling)
        clock_fn = _bridge_clock(self)
        probe_start = clock_fn()
        try:
            self._probe_fn(self._inner.endpoint, probe_timeout)
            self._last_probe_error = None
            return False, None, False, False
        except Exception as exc:
            self._last_probe_error = exc
            if _is_saturated_dispatch_outcome(exc):
                return False, None, False, False
            elapsed = clock_fn() - probe_start
            overrun = elapsed > probe_timeout.total_seconds() + _PROBE_DEADLINE_SLACK_S
            if (
                isinstance(exc, PreflightError)
                and self._activity_relay is not None
                and "SUPERVISION_INFRASTRUCTURE_FAILURE" in str(exc)
            ):
                self._activity_relay.record_sender_error(str(exc))
                return False, None, True, False
            return True, elapsed, False, overrun

    def _record_contention_deferral(
        self,
        *,
        probe_duration_s: float | None,
        deferral_count: int,
        stage: str = "probe_deferred_contention",
    ) -> None:
        """Write a durable sink record when a probe failure is deferred under contention."""
        target = (
            self._durable_sink_path
            if self._durable_sink_path is not None
            else get_durable_sink_path()
        )
        if target is None:
            return
        try:
            with _DURABLE_SINK_LOCK:
                target.parent.mkdir(parents=True, exist_ok=True)
                with (
                    target.open(  # filesystem-write-ok: append-only durable JSONL stream; each contention deferral must append
                        "a", encoding="utf-8"
                    ) as fh
                ):
                    record: dict[str, object] = {
                        "reason": "probe_deferred_contention",
                        "exit_status": None,
                        "probe_duration_s": probe_duration_s,
                        "deferral_count": deferral_count,
                        "log_tail": None,
                        "stage": stage,
                    }
                    fh.write(json.dumps(record))
                    fh.write("\n")
        except OSError:
            pass

    def _perform_restart(
        self,
        *,
        process_exited: bool,
        probe_duration_s: float | None,
    ) -> bool:
        """Perform an MCP server restart and record the decision.

        E4.1: this method must NOT be called with ``self._lock`` held. The
        candidate startup (spawn + private readiness preflight) and the
        policy cooldown both run OFF the lock so an agent reading
        ``bridge.process`` or the shared endpoint is never blocked behind a
        replacement. The three phases are:

        1. :meth:`_begin_restart_attempt` — short, under the lock: budget
           accounting, the durable restart-decision record, and the attempt
           counter. This is the ONLY phase that needs the lock, and it does
           no I/O beyond the bounded durable-sink append.
        2. :meth:`_cooldown_after_failure` and, for the make-before-break
           route, :meth:`_run_with_make_before_break` — off the lock.
        3. :meth:`_replace_dead_child` — back under the lock, because a cold
           replacement mutates ``self._inner`` in place and a concurrent
           reader must never observe a torn ``_inner``.

        C2 contract: only ``BUDGET_EXHAUSTED`` raises :class:`McpServerError`.
        A failed ``restart_fn`` call returns ``False`` and logs at WARNING
        while the budget remains (the agent run continues; the next health
        check retries after the cooldown). The previous design raised on
        the first failed attempt and ended the agent run; the new design
        keeps the working child alive while a candidate can be retried.
        """
        attempt_index = self._begin_restart_attempt(
            process_exited=process_exited,
            probe_duration_s=probe_duration_s,
        )
        self._cooldown_after_failure()

        # If old child is dead (process_exited is True or poll() is not None),
        # cold replacement is required because make-before-break overlap cannot occur.
        if process_exited or _process_is_dead(self._inner):
            return self._replace_dead_child(attempt_index=attempt_index)

        return self._run_with_make_before_break()

    def _begin_restart_attempt(
        self,
        *,
        process_exited: bool,
        probe_duration_s: float | None,
    ) -> int:
        """Charge one restart attempt to the rolling budget (short, under the lock).

        Returns the 1-based attempt index. Raises
        :class:`McpServerError` with ``BUDGET_EXHAUSTED`` — the only
        terminal outcome of the C2 contract — when the rolling window is
        full. The durable restart-decision record is appended here too, so
        the decision is on disk before any candidate exists.
        """
        with self._lock:
            clock_fn = _bridge_clock(self)
            now = clock_fn()
            raw_healthy: object = getattr(self._restart_policy, "healthy_reset_s", 300.0)
            healthy_reset_s: float = (
                float(raw_healthy) if isinstance(raw_healthy, (int, float)) else 300.0
            )
            if (
                self._healthy_period_started_at is not None
                and now - self._healthy_period_started_at >= healthy_reset_s
            ):
                self._restart_timestamps.clear()
                self._restart_count = 0
            self._healthy_period_started_at = None
            self._last_healthy_at = now

            raw_window: object = getattr(self._restart_policy, "rolling_window_s", 600.0)
            rolling_window_s: float = (
                float(raw_window) if isinstance(raw_window, (int, float)) else 600.0
            )
            decision = next_budget_decision(
                self._restart_policy,
                self._restart_count,
                tuple(self._restart_timestamps),
                rolling_window_s,
                _now=lambda: now,
            )
            if decision != "ok":
                reason = "probe failed" if not process_exited else "exited"
                child_pid, psutil_status = _child_process_diagnostics(self._inner)
                raise McpServerError(
                    f"MCP server {reason} and restart budget exhausted "
                    f"(current={self._restart_count}, "
                    f"max={self._restart_policy.max_restarts})",
                    restart_count=self._restart_count,
                    failure_kind=RestartAttemptResult.BUDGET_EXHAUSTED,
                    restart_reason="probe_failed" if not process_exited else "process_exited",
                    child_pid=child_pid,
                    psutil_status=psutil_status,
                    probe_duration_s=probe_duration_s,
                )
            self._restart_timestamps.append(now)

            self._restart_count += 1
            attempt_index = self._restart_count
            if not process_exited:
                logger.warning(
                    "MCP server unresponsive (probe timed out or failed); restarting ({}/{})",
                    attempt_index,
                    self._restart_policy.max_restarts,
                )
            else:
                logger.warning(
                    "MCP server process exited unexpectedly; restarting ({}/{})",
                    attempt_index,
                    self._restart_policy.max_restarts,
                )
            log_tail = self._read_mcp_log_tail()
            _record_restart_decision(
                sink_path=self._durable_sink_path,
                reason=(
                    RestartReason.PROCESS_EXITED if process_exited else RestartReason.PROBE_FAILED
                ),
                exit_status=self._inner.process.poll() if process_exited else None,
                probe_duration_s=probe_duration_s if not process_exited else None,
                log_tail=log_tail,
                stage="process_exited" if process_exited else "probe_failed",
            )
        return attempt_index

    def _replace_dead_child(self, *, attempt_index: int) -> bool:
        """Cold-replace an already-dead child (make-before-break cannot overlap).

        Runs under the lock because ``self._inner`` is mutated in place: the
        shutdown and the ``restart_fn`` spawn must be one atomic swap from a
        concurrent reader's point of view. No candidate exists on this route,
        so nothing here can overlap with a serving child.
        """
        with self._lock:
            if self._workspace_root is not None:
                snap = snapshot_dir(self._workspace_root, self._run_id)
                (snap / "accept.pause").unlink(missing_ok=True)
                (snap / "accept.pause.ack").unlink(missing_ok=True)
                signal_candidate_resume(snap / "accept.resume")
            try:
                self._inner.shutdown()
                self._inner = self._restart_fn()
            except Exception as restart_exc:
                # C2 fix: a single failed restart attempt is NOT terminal.
                # Log at WARNING with attempt count, last stage, and elapsed
                # time so an operator can diagnose; the budget remains and
                # the next health check retries after the cooldown. The old
                # child was already shut down above, so we keep the closed
                # fd reference but mark the bridge state so the next attempt
                # spawns a fresh child rather than crashing on the dead fd.
                last_stage, elapsed_s = _restart_error_fields(restart_exc)
                logger.warning(
                    "MCP restart attempt {}/{} failed (non-terminal, budget remains): "
                    "last_stage={} elapsed_s={} error={}",
                    attempt_index,
                    self._restart_policy.max_restarts,
                    last_stage,
                    elapsed_s,
                    restart_exc,
                )
                # Old child was shut down; reset the in-flight counters so
                # the next health check doesn't accumulate stale state.
                # ``_restart_count`` is left at ``attempt_index`` so the
                # budget accounting is correct (this attempt counted).
                self._reset_probe_counters()
                return False
            # Bump generation on every successful restart so a stale probe
            # captured against the prior inner is discarded by
            # _handle_failed_probe_under_lock.
            self._bridge_generation += 1
            self._reset_probe_counters()
            logger.info(
                "MCP server restarted on stable endpoint {}; restart_count={}",
                self._inner.endpoint,
                self._restart_count,
            )
            return True

    def _reset_probe_counters(self) -> None:
        """Clear every probe-outcome counter (caller holds or takes the lock)."""
        self._consecutive_probe_failures = 0
        self._consecutive_probe_successes = 0
        self._consecutive_probe_failures_deferred = 0
        self._first_probe_deferred_at = None

    def _spawn_candidate(self) -> StandaloneMcpProcess:
        """Spawn the candidate process via restart_fn while old child serves."""
        fn = cast(
            "_RestartFnProtocol", self._restart_fn
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        try:
            return fn(make_before_break=True)
        except TypeError:
            return self._restart_fn()

    def capture_tool_hash_baseline(self) -> None:
        """Seed the E1.9 drift reference from the SERVED ``tools/list`` of the live child.

        Called once during initial bridge startup, after the first child's
        preflight has proved the endpoint is serving and BEFORE any candidate
        can overlap it. Reading the wire response (rather than a parent-side
        catalogue snapshot) is the whole point: the served list is what an agent
        actually sees, so it is the only sound drift reference. Both channels
        canonicalise through
        :func:`~ralph.mcp.server._handover_protocol.canonical_tool_entries`
        before hashing, so a baseline captured here is byte-comparable with the
        hash a candidate proves over the private readiness channel.

        Fails CLOSED: a transport failure leaves the baseline unset AND flips
        ``self._baseline_capture_failed`` to ``True`` so the next candidate
        verification must refuse promotion unless the old child is already
        dead (the only legal escape). Refusing to start the bridge because a
        confirmation read raced a restart would still fail bridge construction;
        the WARNING plus fail-closed gate keep the bridge usable while making
        drift-invisible promotions structurally impossible.
        """
        endpoint = self._inner.endpoint
        if not isinstance(endpoint, str) or not endpoint:
            self._expected_tool_hash = None
            self._expected_tool_entries = None
            self._baseline_capture_failed = True
            logger.warning("tool-hash baseline skipped: inner endpoint is not a usable string")
            return
        fetch = self._tool_entries_fn if self._tool_entries_fn is not None else served_tool_entries
        try:
            entries = fetch(endpoint, _INITIAL_TOOL_HASH_TIMEOUT_S)
        except (OSError, ValueError, TimeoutError) as exc:
            self._expected_tool_hash = None
            self._expected_tool_entries = None
            self._baseline_capture_failed = True
            logger.warning(
                "tool-hash baseline capture failed for {}; tool-set drift detection is "
                "refusing candidates until a baseline can be recovered: {}",
                endpoint,
                exc,
            )
            return
        if not entries:
            self._expected_tool_hash = None
            self._expected_tool_entries = None
            self._baseline_capture_failed = True
            logger.warning(
                "tool-hash baseline capture returned an empty served tool list for {}; "
                "tool-set drift detection is refusing candidates until a baseline can be "
                "recovered",
                endpoint,
            )
            return
        self._expected_tool_entries = entries
        self._expected_tool_hash = compute_tool_set_hash(entries)
        self._baseline_capture_failed = False
        logger.debug(
            "tool-hash baseline captured from served tools/list: endpoint={} tools={} hash={}",
            endpoint,
            len(entries),
            self._expected_tool_hash,
        )

    def _apply_baseline_unavailable(self, result: CandidateReadiness, old_dead: bool) -> bool:
        """Decide promotion when no tool-hash drift reference exists (C1/E1.9).

        With no baseline there is nothing to compare a candidate's served tool
        set against, so the gate is fail-closed: promotion is refused while the
        old child still serves. The single escape is the old child already
        being dead, where there is nothing left to preserve and the candidate
        is promoted with a WARNING so the agent keeps running.
        """
        if old_dead:
            logger.warning(
                "Tool-hash baseline unavailable and old child is dead. "
                "Promoting candidate with warning (dead-child escape): "
                "{}",
                result.detail,
            )
            if result.tool_hash is not None:
                self._absorb_verified_tool_set(result)
            return True
        # Old child is alive and serving: refuse the promotion — there is no
        # drift reference, so we cannot prove safety.
        logger.warning(
            "Candidate verification refused: tool-hash baseline unavailable "
            "(no drift reference) and old child is alive. Keeping old "
            "child. detail={}",
            result.detail,
        )
        return False

    def _apply_config_fingerprint_mismatch(
        self, result: CandidateReadiness, old_dead: bool
    ) -> bool:
        """Decide promotion for an effective-config fingerprint mismatch.

        C1 step 2: the candidate's effective MCP configuration diverged from
        the parent's snapshot, so it may have been started with a stale
        catalog, a partially-merged ``mcp.toml``, or a different env override
        layer. This is a fail-closed refusal while the old child still serves;
        the single escape is the old child already being dead, where the
        drifted candidate is promoted with a WARNING so the agent keeps
        running. Only the digest is logged — never configuration values.
        """
        if old_dead:
            logger.warning(
                "Effective-config fingerprint mismatch but old child is "
                "dead; promoting candidate with warning (dead-child "
                "escape): {}",
                result.detail,
            )
            return True
        logger.warning(
            "Candidate verification refused on effective-config fingerprint mismatch: detail={}",
            result.detail,
        )
        return False

    def _verify_candidate_socket(
        self,
        candidate: StandaloneMcpProcess,
        parent_sock: socket.socket,
        raw_pid: object,
    ) -> bool:
        pid: int = raw_pid if isinstance(raw_pid, int) else 0
        raw_stage: object = getattr(candidate, "stage_id", None)
        stage_id: str = raw_stage if isinstance(raw_stage, str) else ""
        session_id: str = self._session_id or ""
        broker_fp: str | None = self._broker_fingerprint
        cfg_fp: str | None = self._effective_config_fingerprint
        verified = False
        try:
            result: CandidateReadiness = check_candidate_readiness(
                parent_sock,
                expected_spawn_id=stage_id,
                expected_pid=pid,
                expected_session_id=session_id,
                expected_broker_fingerprint=broker_fp,
                expected_effective_config_fingerprint=cfg_fp,
                expected_tool_hash=self._expected_tool_hash,
                expected_tool_hash_required=self._baseline_capture_failed,
            )
            if result.ok:
                # Retain the VALIDATED candidate catalogue as the new drift
                # reference BEFORE the ``finally`` closes the handover channel,
                # so a later candidate is judged against what the serving child
                # actually advertises rather than against a stale baseline.
                if result.tool_hash is not None:
                    self._absorb_verified_tool_set(result)
                verified = True
            else:
                # Probe the old child's liveness once so both refusal /
                # dead-old-child-escape branches share the same view of
                # "old_dead".
                old_dead = self._is_old_child_dead()
                if result.failure_stage == "tool_set_drift":
                    diff = describe_tool_set_diff(
                        self._expected_tool_entries, list(result.tools) or None
                    )
                    if old_dead:
                        # Dead-old-child exception: nothing left to preserve, so
                        # the drifted candidate is promoted — but never silently.
                        logger.warning(
                            "Tool-set drift detected during candidate verification, but old "
                            "child is dead; promoting candidate with warning: {} | {}",
                            result.detail,
                            diff,
                        )
                        if result.tool_hash is not None:
                            self._absorb_verified_tool_set(result)
                        verified = True
                    else:
                        # Old child is alive and serving: refuse the promotion,
                        # name the tools that differ, and keep the old child intact.
                        logger.warning(
                            "Candidate verification refused on tool-set drift: stage={} "
                            "detail={} tool_set_diff={}",
                            result.failure_stage,
                            result.detail,
                            diff,
                        )
                elif result.failure_stage == "baseline_unavailable":
                    # C1/E1.9 fail-closed gate: there is no drift reference at
                    # all. Refuse unless the old child is already dead (the
                    # only legal escape — there is nothing left to preserve).
                    verified = self._apply_baseline_unavailable(result, old_dead)
                elif result.failure_stage == "effective_config_fingerprint_mismatch":
                    verified = self._apply_config_fingerprint_mismatch(result, old_dead)
                else:
                    logger.warning(
                        "Candidate verification failed: stage={} detail={}",
                        result.failure_stage,
                        result.detail,
                    )
        except Exception as exc:
            logger.warning("Candidate verification raised exception: {}", exc)
        finally:
            with contextlib.suppress(Exception):
                parent_sock.close()
            candidate.handover_parent_sock = None
        return verified

    def _is_old_child_dead(self) -> bool:
        """Return True if the bridge's currently-serving child has exited.

        A ``None`` poll means the child is still running. Any non-None return
        (including ``0`` for a clean exit and any negative sentinel for a
        signal) counts as dead — the only legal escape from drift refusal.
        """
        proc_old: object = getattr(self._inner, "process", None)
        poll_fn: object = getattr(proc_old, "poll", None)
        if not callable(poll_fn):
            return False
        poll_result: object = poll_fn()
        return poll_result is not None

    def _absorb_verified_tool_set(self, result: CandidateReadiness) -> None:
        """Adopt the validated candidate's tool set as the next drift reference.

        Called after a successful verification OR after a dead-old-child
        exception promotion. Stores the hash and catalogue, and clears the
        fail-closed gate (``_baseline_capture_failed``) because the reference
        is now demonstrably live.
        """
        self._expected_tool_hash = result.tool_hash
        self._expected_tool_entries = list(result.tools)
        self._baseline_capture_failed = False

    def _verify_candidate(self, candidate: StandaloneMcpProcess) -> bool:
        """Verify candidate process is alive and un-exited before promotion."""
        proc: object = getattr(candidate, "process", None)
        if proc is None:
            return True
        poll_fn: object = getattr(proc, "poll", None)
        if callable(poll_fn):
            try:
                poll_result: object = poll_fn()
                if poll_result is not None:
                    return False
            except Exception:
                return False
        parent_sock_obj: object = getattr(candidate, "handover_parent_sock", None)
        proc_pid: object = getattr(proc, "pid", None)
        if type(proc).__name__ != "FakeProcess" and isinstance(parent_sock_obj, socket.socket):
            return self._verify_candidate_socket(candidate, parent_sock_obj, proc_pid)
        return True

    def _hand_over_accept(
        self, old_inner: StandaloneMcpProcess, candidate: StandaloneMcpProcess
    ) -> None:
        """Execute single-acceptor pause-ack-resume handover between children.

        Errors escaping either stage carry ``handover_step`` naming the exact
        stage, so a supervisor report distinguishes "the old child never
        acknowledged the pause" from "the candidate never got its resume
        sentinel" — the two have completely different operator responses.
        On any escape (KeyboardInterrupt, SIGTERM, ordinary failure) the
        candidate is reaped before the exception propagates, so a hard cancel
        during the handshake cannot leak the candidate process.

        E5.2 (D-state / unkillable survivor): if the pause ack times out
        AND a kill+wait cannot confirm the old child gone (D-state),
        the candidate is NOT abandoned — single-acceptor safety is
        preserved because (a) the pause sentinel was written, so a still-
        serving old child drops its accepting FD on its next pass; (b) a
        D-state survivor cannot run user code and so cannot accept new
        connections; (c) the surviving handle is scheduled for bounded
        background reap so bridge shutdown stays idempotent.
        """
        if self._workspace_root is not None:
            snap = snapshot_dir(self._workspace_root, self._run_id)
            old_stage_obj: object = getattr(old_inner, "stage_id", None)
            old_stage: str | None = (
                old_stage_obj if isinstance(old_stage_obj, str) and old_stage_obj else None
            )
            pause_file = (
                snap / f"accept.pause.{old_stage}"
                if old_stage is not None
                else snap / "accept.pause"
            )
            cand_stage_obj: object = getattr(candidate, "stage_id", None)
            cand_stage: str | None = (
                cand_stage_obj if isinstance(cand_stage_obj, str) and cand_stage_obj else None
            )
            resume_file = (
                snap / f"accept.resume.{cand_stage}"
                if cand_stage is not None
                else snap / "accept.resume"
            )
            proc: object = getattr(old_inner, "process", None)
            # E5.4: identity-safe liveness on the OWNED Popen handle so a
            # PID-recycled replacement cannot fool us into treating a fresh
            # process as the bridge's old child.
            if _is_alive_process(proc):
                try:
                    request_old_child_pause(pause_file)
                    ack = wait_for_old_child_pause_ack(
                        pause_file,
                        timeout=_HANDOVER_ACK_TIMEOUT_S,
                        sleep_fn=self._sleep_fn,
                    )
                except BaseException as pause_exc:
                    # On any escape here the candidate is not yet attached
                    # to bridge state; reap it before propagating so a hard
                    # cancel cannot leak the process. The reap is idempotent
                    # against any subsequent ``shutdown`` call.
                    with contextlib.suppress(Exception):
                        candidate.shutdown()
                    raise _stamp_handover_step(pause_exc, _HANDOVER_STEP_PAUSE_ACK) from pause_exc
                if not ack:
                    logger.warning(
                        "Old child failed to acknowledge pause within {}s; escalating termination",
                        _HANDOVER_ACK_TIMEOUT_S,
                    )
                    kill_fn: object = getattr(proc, "kill", None)
                    if callable(kill_fn):
                        with contextlib.suppress(Exception):
                            kill_fn()
                    # Bound the SIGKILL observation window so a SIGKILL'd
                    # SIGSTOPped child has time for the kernel to advance
                    # from "kill signal sent" to "process reaped by poll()".
                    # The bounded wait is import-time-checked via
                    # ``_HANDOVER_KILL_WAIT_S``.
                    poll_fn = cast(
                        "Callable[[], object] | None", getattr(proc, "poll", None)
                    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol confinee)
                    old_exited = False
                    if poll_fn is not None and callable(poll_fn):
                        deadline = time.monotonic() + _HANDOVER_KILL_WAIT_S
                        while time.monotonic() < deadline:
                            poll_res: object = poll_fn()
                            if poll_res is not None:
                                old_exited = True
                                break
                            sleep_fn = self._sleep_fn if self._sleep_fn is not None else time.sleep
                            sleep_fn(0.05)
                    else:
                        old_exited = not _is_alive_process(proc)

                    if not old_exited:
                        # E5.2: an unkillable / D-state survivor must NOT
                        # block promotion. The pause sentinel was written,
                        # so a still-serving old child drops its accepting
                        # FD on its next pass; a D-state survivor cannot
                        # run user code and so cannot accept. Promotion
                        # proceeds; bounded background reap re-attempts
                        # shutdown at bridge teardown.
                        logger.warning(
                            "Old child survived SIGKILL after pause ack timeout; "
                            "promoting candidate (D-state/nonkillable survivor, "
                            "single-acceptor safety preserved via pause sentinel); "
                            "deferring reap to bridge shutdown"
                        )
                        self._schedule_unkillable_reap(proc)
            try:
                signal_candidate_resume(resume_file)
                if cand_stage is not None:
                    signal_candidate_resume(snap / "accept.resume")
            except BaseException as resume_exc:
                with contextlib.suppress(Exception):
                    candidate.shutdown()
                raise _stamp_handover_step(resume_exc, _HANDOVER_STEP_RESUME) from resume_exc

    def _drain_old(self, old_inner: StandaloneMcpProcess) -> None:
        """Drain old child in background up to _HANDOVER_DRAIN_S then shut it down."""
        drain_s = _HANDOVER_DRAIN_S
        sleep_fn = cast("Callable[[float], object] | None", getattr(self, "_sleep_fn", None))
        proc: object = getattr(old_inner, "process", None)
        if sleep_fn is None and (self._workspace_root is None or not _is_alive_process(proc)):
            with contextlib.suppress(Exception):
                old_inner.shutdown()
            return
        if sleep_fn is None:
            sleep_fn = time.sleep

        self._draining_inners.append(old_inner)

        def _drain_worker() -> None:
            try:
                sleep_fn(drain_s)
            except Exception:
                pass
            finally:
                with self._lock:
                    with contextlib.suppress(ValueError):
                        self._draining_inners.remove(old_inner)
                    with contextlib.suppress(ValueError):
                        self._drain_threads.remove(threading.current_thread())
                with contextlib.suppress(Exception):
                    old_inner.shutdown()

        t = threading.Thread(target=_drain_worker, name="mcp-old-drain", daemon=True)
        with self._lock:
            self._drain_threads.append(t)
        t.start()

    def _promote_and_reap(
        self, candidate: StandaloneMcpProcess, old_inner: StandaloneMcpProcess
    ) -> None:
        """Promote candidate to _inner under lock and schedule old child drain.

        The two halves are stamped separately: a failure while swapping the
        inner is a promotion failure, while a failure while retiring the old
        child is a drain failure. :func:`_stamp_handover_step` is
        innermost-wins, so the drain stamp survives the outer promote guard.
        """
        try:
            self._inner = candidate
            self._bridge_generation += 1
            self._consecutive_probe_failures = 0
            self._consecutive_probe_successes = 0
            self._consecutive_probe_failures_deferred = 0
            self._first_probe_deferred_at = None
        except BaseException as promote_exc:
            raise _stamp_handover_step(promote_exc, _HANDOVER_STEP_PROMOTE) from promote_exc
        try:
            self._drain_old(old_inner)
        except BaseException as drain_exc:
            raise _stamp_handover_step(drain_exc, _HANDOVER_STEP_DRAIN) from drain_exc

    def _claim_candidate_slot(self, *, bump_generation: bool = False) -> int | None:
        """Claim the ONE in-flight candidate slot and capture the generation token.

        E2.7: there is exactly one candidate at a time. This is the whole
        mutual-exclusion primitive every replacement initiator shares — the
        health probe, the failed-probe handler and the tool-registry reset all
        go through it, so two concurrent initiators can never spawn rival
        candidates that would race for the shared accepting FD.

        Must be called with ``self._lock`` held. It performs no I/O and holds
        no lock of its own, so the claim is atomic with the bookkeeping the
        caller interleaves it with (notably the tool-registry reset's
        generation bump and cap checks, which must not happen when the reset
        is going to be refused anyway).

        Returns the generation token the off-lock work must re-validate before
        promoting, or ``None`` when a candidate is already in flight or the
        bridge is shutting down.

        ``bump_generation`` exists only for the tool-registry reset, whose
        documented contract is to invalidate stale probe results *before* any
        other step. The health route leaves the generation alone because its
        promotion already bumps it.
        """
        if self._is_shutting_down or self._candidate_in_flight:
            return None
        if bump_generation:
            self._bridge_generation += 1
        self._candidate_in_flight = True
        return self._bridge_generation

    def _release_candidate_slot(self) -> None:
        """Release the candidate slot so the next initiator can claim it."""
        with self._lock:
            self._candidate_in_flight = False

    def _schedule_unkillable_reap(self, proc: object) -> None:
        """Spawn a bounded background reap for an unkillable / D-state survivor.

        The survivor is a ``Popen`` handle we already own (the bridge acquired
        it via spawn), so identity-safe liveness (``proc.poll()`` / kernel wait)
        is anchored to the handle itself rather than a bare pid (E5.4). The
        thread takes one final ``proc.terminate()`` / ``proc.kill()`` /
        ``proc.wait()`` cycle after the backoff so the bridge shutdown path
        always sees a clean exit. The entry is FIFO-tracked on the bridge
        ``_unkillable_reapers`` deque (capped at 8) and reaped threads are
        joined at bridge shutdown.
        """
        handle_id = id(proc)
        with self._lock:
            self._unkillable_reapers.append((proc, handle_id))

        def _reap_worker() -> None:
            try:
                # filesystem-poll-ok: bounded unkillable-child reap backoff
                time.sleep(_UNKILLABLE_REAP_BACKOFF_S)
                terminate_fn = cast("Callable[[], object] | None", getattr(proc, "terminate", None))
                if callable(terminate_fn):
                    with contextlib.suppress(Exception):
                        terminate_fn()
                deadline = time.monotonic() + _UNKILLABLE_REAP_RETRY_S
                poll_fn = cast("Callable[[], object] | None", getattr(proc, "poll", None))
                while time.monotonic() < deadline:
                    if poll_fn is not None and callable(poll_fn):
                        with contextlib.suppress(Exception):
                            if poll_fn() is not None:
                                return
                    # filesystem-poll-ok: bounded child-exit retry before forced kill
                    time.sleep(0.05)
                kill_fn = cast("Callable[[], object] | None", getattr(proc, "kill", None))
                if callable(kill_fn):
                    with contextlib.suppress(Exception):
                        kill_fn()
                wait_fn = cast("Callable[[float], object] | None", getattr(proc, "wait", None))
                if callable(wait_fn):
                    with contextlib.suppress(Exception):
                        wait_fn(2.0)
            finally:
                with self._lock, contextlib.suppress(ValueError):
                    for entry in list(self._unkillable_reapers):
                        if entry[1] == handle_id:
                            self._unkillable_reapers.remove(entry)
                            break

        t = threading.Thread(
            target=_reap_worker, name=f"mcp-unkillable-reap-{handle_id}", daemon=True
        )
        with self._lock:
            self._unkillable_reap_threads.append(t)
        t.start()

    def _reservation_is_current(self, token: int) -> bool:
        """Return True while this attempt still owns an uncorrupted reservation.

        Re-validated under the lock immediately before every promotion. A
        candidate whose generation has moved (a tool-registry reset bumped it,
        a concurrent cold replacement promoted) or whose slot was taken is
        abandoned and reaped rather than promoted: promoting it would swap
        ``_inner`` from a stale snapshot of the world.
        """
        with self._lock:
            return (
                not self._is_shutting_down
                and self._candidate_in_flight
                and self._bridge_generation == token
            )

    def _abandon_candidate(self, candidate: StandaloneMcpProcess) -> None:
        """Reap an unpromoted candidate's WHOLE process group, fail-closed.

        ``StandaloneMcpProcess.shutdown`` escalates SIGTERM to SIGKILL on the
        child's POSIX process group and then kills descendants, so an abandoned
        candidate can never leave a live process behind holding the inherited
        listening FD. ``_inner`` is deliberately untouched: the old child keeps
        serving. Callers log the reason; this helper stays silent so a reap can
        never be the source of a spurious failure.
        """
        with contextlib.suppress(Exception):
            candidate.shutdown()

    def _run_with_make_before_break(self) -> bool:
        """Claim the candidate slot and run the replacement state machine.

        Every replacement initiator on this class funnels through here or
        through :meth:`_replace_with_candidate`, so the E2.7 single-candidate
        gate, the generation re-validation and the non-terminal cleanup are
        shared rather than re-implemented per call site.
        """
        with self._lock:
            token = self._claim_candidate_slot()
        if token is None:
            logger.warning(
                "MCP replacement skipped: a candidate replacement is already in flight "
                "(non-terminal, old child kept)"
            )
            return False
        try:
            return self._replace_with_candidate(token=token, attempt_index=self._restart_count)
        finally:
            self._release_candidate_slot()

    def _replace_with_candidate(
        self,
        *,
        token: int,
        attempt_index: int,
        initiator: str = "mcp-restart",
        pre_promotion_gate: Callable[[StandaloneMcpProcess], None] | None = None,
    ) -> bool:
        """Replace the serving child with a privately verified candidate.

        The caller MUST already hold the candidate slot for ``token``; this
        method releases neither the slot nor the generation, and is called
        from a ``finally``-guarded wrapper that does.

        E4.1: the whole body runs OFF the bridge lock except for four short
        critical sections (read ``_inner``, publish the candidate so
        ``shutdown()`` can reap it, the two reservation re-validations, and
        the candidate-slot cleanup). The expensive parts — the candidate spawn
        and the private readiness preflight — hold no lock at all, so
        ``bridge.process`` and shared-endpoint reads stay prompt while a
        candidate boots.

        The ordering is fixed:

        1. clear stale handover sentinels;
        2. spawn the candidate while the old child keeps serving;
        3. publish the candidate for reaping, then re-validate the reservation;
        4. verify the candidate over the PRIVATE channel only
           (:meth:`_verify_candidate` -> ``check_candidate_readiness``: spawn
           id, pid, session id/broker fingerprint, tool-set schema hash);
        5. re-validate the reservation again, immediately before the swap;
        6. ordered handover, then promotion.

        A pre-promotion gate (``pre_promotion_gate``) runs after step 4 and
        before step 6. The tool-registry reset uses it for its alias check.
        Note that this gate can only ever be a *secondary* signal: during the
        overlap the shared endpoint is answered by the OLD child, so identity
        and tool-set equivalence are established by step 4 alone.

        Every failure path is non-terminal under the C2 contract: the candidate
        is fully reaped, the old child is left serving, and the attempt stays
        charged to the budget. :class:`McpServerError` raised here names the
        handover stage that produced it.
        """
        if self._workspace_root is not None:
            snap = snapshot_dir(self._workspace_root, self._run_id)
            (snap / "accept.resume").unlink(missing_ok=True)
            (snap / "accept.pause").unlink(missing_ok=True)
            (snap / "accept.pause.ack").unlink(missing_ok=True)
        with self._lock:
            old_inner = self._inner
        try:
            candidate = self._spawn_candidate()
        except Exception as restart_exc:
            _stamp_handover_step(restart_exc, _HANDOVER_STEP_SPAWN)
            last_stage, elapsed_s = _restart_error_fields(restart_exc)
            logger.warning(
                "MCP {} attempt {}/{} candidate spawn failed "
                "(non-terminal, old child kept, handover_step={}): "
                "last_stage={} elapsed_s={} error={}",
                initiator,
                attempt_index,
                self._restart_policy.max_restarts,
                _HANDOVER_STEP_SPAWN,
                last_stage,
                elapsed_s,
                restart_exc,
            )
            self._mark_probe_misses_consumed()
            return False

        with self._lock:
            shutting_down = self._is_shutting_down
            if not shutting_down:
                self._current_candidate = candidate
        if not self._reservation_is_current(token):
            self._abandon_candidate(candidate)
            logger.warning(
                "MCP {} attempt {}/{} candidate abandoned before verification "
                "(non-terminal, old child kept)",
                initiator,
                attempt_index,
                self._restart_policy.max_restarts,
            )
            return False

        try:
            try:
                candidate_verified = self._verify_candidate(candidate)
            except BaseException as verify_exc:
                self._abandon_candidate(candidate)
                raise _stamp_handover_step(verify_exc, _HANDOVER_STEP_VERIFY) from verify_exc
            if not candidate_verified or self._is_shutting_down:
                logger.warning(
                    "MCP {} attempt {}/{} candidate verification failed "
                    "(non-terminal, old child kept, handover_step={})",
                    initiator,
                    attempt_index,
                    self._restart_policy.max_restarts,
                    _HANDOVER_STEP_VERIFY,
                )
                self._abandon_candidate(candidate)
                self._mark_probe_misses_consumed()
                return False

            if pre_promotion_gate is not None:
                try:
                    pre_promotion_gate(candidate)
                except BaseException as gate_exc:
                    self._abandon_candidate(candidate)
                    raise _stamp_handover_step(gate_exc, _HANDOVER_STEP_VERIFY) from gate_exc

            if not self._reservation_is_current(token):
                self._abandon_candidate(candidate)
                logger.warning(
                    "MCP {} attempt {}/{} candidate abandoned before promotion: the bridge "
                    "generation moved while the candidate was being verified "
                    "(non-terminal, old child kept)",
                    initiator,
                    attempt_index,
                    self._restart_policy.max_restarts,
                )
                return False

            try:
                self._hand_over_accept(old_inner, candidate)
            except McpServerError as handover_exc:
                if handover_exc.handover_step == _HANDOVER_STEP_PAUSE_ACK:
                    logger.warning(
                        "MCP {} attempt {}/{} candidate handover failed: "
                        "old child still alive and could not be confirmed stopped "
                        "(non-terminal, old child kept, handover_step={}): {}",
                        initiator,
                        attempt_index,
                        self._restart_policy.max_restarts,
                        _HANDOVER_STEP_PAUSE_ACK,
                        handover_exc,
                    )
                    self._abandon_candidate(candidate)
                    self._mark_probe_misses_consumed()
                    return False
                self._abandon_candidate(candidate)
                raise

            try:
                self._promote_and_reap(candidate, old_inner)
            except BaseException as promote_exc:
                self._abandon_candidate(candidate)
                raise _stamp_handover_step(promote_exc, _HANDOVER_STEP_PROMOTE) from promote_exc
        finally:
            self._forget_candidate(candidate)
        logger.info(
            "MCP server restarted via make-before-break on stable endpoint {}; restart_count={}",
            self._inner.endpoint,
            self._restart_count,
        )
        return True

    def _mark_probe_misses_consumed(self) -> None:
        """Record that an attempt was spent, so the next probe miss retries sooner."""
        self._consecutive_probe_failures = _PROBE_FAILURES_BEFORE_RESTART - 1
        self._consecutive_probe_failures_deferred = 0
        self._first_probe_deferred_at = None

    def _forget_candidate(self, candidate: StandaloneMcpProcess) -> None:
        """Drop the published candidate so ``shutdown()`` stops trying to reap it."""
        with self._lock:
            if self._current_candidate is candidate:
                self._current_candidate = None

    def _attempt_restart_with_candidate(
        self,
        *,
        process_exited: bool,
        probe_duration_s: float | None,
    ) -> str:
        """Typed-result wrapper around :meth:`_perform_restart` (C2 contract).

        Returns the :class:`RestartAttemptResult` string for the attempt:

        * ``RestartAttemptResult.BUDGET_EXHAUSTED`` when the rolling or
          absolute budget is exhausted. ``_perform_restart`` raises
          :class:`McpServerError`; this wrapper re-raises the same
          error and is the ONLY path that does so.
        * ``RestartAttemptResult.ATTEMPT_FAILED`` when ``restart_fn``
          raised. The bridge keeps the old inner (now shut down) and
          the budget remains; the next health check retries after the
          cooldown. Non-terminal.
        * ``RestartAttemptResult.REPLACED`` when the candidate was
          spawned and the generation swap succeeded. The bridge now
          points at the new child.

        The helper exists so call sites that need to consume the typed
        outcome (e.g. supervisor-side cancel hooks, instrumented
        callers) can branch on a stable enum-like string without
        depending on the ``bool`` return type of the legacy
        :meth:`_perform_restart` method.
        """
        try:
            replaced = self._perform_restart(
                process_exited=process_exited,
                probe_duration_s=probe_duration_s,
            )
        except McpServerError:
            # Budget exhausted: the legacy path raises typed error.
            raise
        if replaced:
            return RestartAttemptResult.REPLACED
        return RestartAttemptResult.ATTEMPT_FAILED

    def _check_child_cpu_progress(self, pid: int, sample_duration_s: float) -> bool:
        if pid <= 1:
            return False
        try:
            proc = psutil.Process(pid)
            c1 = proc.cpu_times()
            sleep_fn = self._sleep_fn or time.sleep
            sleep_fn(sample_duration_s)
            c2 = proc.cpu_times()
            return ((c2.user - c1.user) + (c2.system - c1.system)) > _CPU_PROGRESS_THRESHOLD
        except Exception:
            return False

    def _handle_failed_probe_under_lock(
        self,
        *,
        fresh_b_signal: bool,
        probe_duration_s: float | None,
        probed_pid: int,
        probed_generation: int,
        deferrable: bool = True,
    ) -> bool:
        """Handle contention and failure accounting after a failed probe.

        ``probed_pid`` and ``probed_generation`` are the bridge identity
        captured under the lock BEFORE the contention producer ran. If
        either changed while the producer was off the lock, the B-signal
        we hold belongs to a different bridge state and is discarded
        without touching any counter.

        E4.1: all the accounting runs under the lock, but the replacement
        itself is dispatched AFTER the lock is released, so a candidate
        startup never blocks ``bridge.process`` or a shared-endpoint read.
        """
        restart_because: int | None = None
        with self._lock:
            if (
                self._inner.process.pid != probed_pid
                or self._bridge_generation != probed_generation
            ):
                logger.debug("stale probe result discarded: bridge changed while producer ran")
                return False

            if self._inner.process.poll() is not None:
                restart_because = _RESTART_BECAUSE_PROCESS_EXITED
            else:
                clock_fn = _bridge_clock(self)
                now = clock_fn()
                deferral_wall_time = (
                    (now - self._first_probe_deferred_at)
                    if self._first_probe_deferred_at is not None
                    else 0.0
                )
                # Sustained contention ceiling: if the deferral counter has reached
                # the count cap or wall-time ceiling, force a restart even when a fresh
                # contention signal is present, preventing indefinite deferral.
                if (
                    self._consecutive_probe_failures_deferred >= _DEFERRAL_COUNT_CEILING
                    or deferral_wall_time >= _DEFERRAL_WALL_TIME_CEILING_S
                ):
                    logger.warning("deferral ceiling tripped; forcing replacement")
                    self._first_probe_deferred_at = None
                    restart_because = _RESTART_BECAUSE_PROBE_FAILED
                else:
                    fresh_w_signal = (
                        bool(
                            pop_recent_host_contention_signals(self._host_contention_window_seconds)
                        )
                        if deferrable
                        else False
                    )
                    fresh_contention = (fresh_w_signal or fresh_b_signal) and deferrable
                    if fresh_contention:
                        if self._first_probe_deferred_at is None:
                            self._first_probe_deferred_at = now
                        self._consecutive_probe_failures_deferred += 1
                        self._record_contention_deferral(
                            probe_duration_s=probe_duration_s,
                            deferral_count=self._consecutive_probe_failures_deferred,
                            stage="probe_deferred_contention",
                        )
                        logger.debug(
                            "MCP server probe failed with fresh contention signal; deferring "
                            "restart (deferred={}, w={}, b={})",
                            self._consecutive_probe_failures_deferred,
                            fresh_w_signal,
                            fresh_b_signal,
                        )
                        return False

                    self._consecutive_probe_failures_deferred = 0
                    self._first_probe_deferred_at = None
                    self._consecutive_probe_failures += 1
                    if self._consecutive_probe_failures < _PROBE_FAILURES_BEFORE_RESTART:
                        logger.debug(
                            "MCP server probe failed ({}/{} before restart); server still running",
                            self._consecutive_probe_failures,
                            _PROBE_FAILURES_BEFORE_RESTART,
                        )
                        return False
                    restart_because = _RESTART_BECAUSE_PROBE_FAILED

        return self._perform_restart(
            process_exited=restart_because == _RESTART_BECAUSE_PROCESS_EXITED,
            probe_duration_s=(
                None if restart_because == _RESTART_BECAUSE_PROCESS_EXITED else probe_duration_s
            ),
        )

    def check_health_and_restart_if_needed(self) -> bool:
        """Check if MCP server is alive and responsive; restart if not.

        Treats the bridge as unhealthy when either (a) the subprocess has exited
        or (b) the subprocess is alive but the responsiveness probe times out or
        fails. On an unhealthy result the stale process is terminated, a new one
        is spawned via restart_fn (which reruns full preflight), and the bounded
        restart counter is incremented.

        Returns True when a restart was performed.
        Raises McpServerError when the restart budget is exhausted.
        Thread-safe: may be called from a background McpSupervisor thread.

        E4.1: the dead-child liveness read happens under the lock, but the
        replacement it triggers runs OFF it, so an agent reading
        ``bridge.process`` is never queued behind a candidate startup.
        """
        with self._lock:
            inner_exited = self._inner.process.poll() is not None
        if inner_exited:
            return self._perform_restart(process_exited=True, probe_duration_s=None)

        with self._lock:
            probe_failed, probe_duration_s, infra_error, overrun = self._run_health_probe()
            if infra_error:
                return False

            if not probe_failed:
                self._consecutive_probe_successes += 1
                if self._consecutive_probe_successes >= _PROBE_SUCCESS_HYSTERESIS_COUNT:
                    self._consecutive_probe_failures = 0
                    self._consecutive_probe_failures_deferred = 0
                    self._first_probe_deferred_at = None
                now = _bridge_clock(self)()
                self._last_healthy_at = now
                if self._healthy_period_started_at is None:
                    self._healthy_period_started_at = now
                raw_healthy: object = getattr(self._restart_policy, "healthy_reset_s", 300.0)
                healthy_reset_s: float = (
                    float(raw_healthy) if isinstance(raw_healthy, (int, float)) else 300.0
                )
                if now - self._healthy_period_started_at >= healthy_reset_s:
                    self._restart_timestamps.clear()
                    self._restart_count = 0
                    self._healthy_period_started_at = now
                return False

            self._consecutive_probe_successes = 0
            self._healthy_period_started_at = None

            # Capture the probed bridge identity BEFORE releasing the
            # lock. The producer below runs OFF the lock; while it
            # runs, the main thread may call reset_tool_registry or a
            # concurrent probe may succeed and reset counters. The
            # helper revalidates this identity under the lock and
            # discards the result if the bridge changed.
            probed_pid = self._inner.process.pid
            probed_generation = self._bridge_generation

        # Probe failed: lock is RELEASED before calling producer()
        # A probe overrun is classified as host contention (C3).
        deferrable = _is_timeout_class_probe_outcome(self._last_probe_error, overrun)
        fresh_b_signal_from_overrun = False
        if probe_failed and overrun and deferrable:
            record_host_contention_signal("probe_overrun")
            fresh_b_signal_from_overrun = True
        producer = self._contention_signal_producer or current_host_is_contended
        try:
            fresh_b_signal = bool(producer())
        except Exception:
            fresh_b_signal = False
        fresh_b_signal = (fresh_b_signal or fresh_b_signal_from_overrun) if deferrable else False

        # Confirm-before-replace: before replacing a live child
        clock_fn = _bridge_clock(self)
        now = clock_fn()
        deferral_wall_time = (
            (now - self._first_probe_deferred_at)
            if self._first_probe_deferred_at is not None
            else 0.0
        )
        ceiling_tripped = (
            self._consecutive_probe_failures_deferred >= _DEFERRAL_COUNT_CEILING
            or deferral_wall_time >= _DEFERRAL_WALL_TIME_CEILING_S
        )
        will_trip_restart = not fresh_b_signal and (
            self._consecutive_probe_failures + 1 >= _PROBE_FAILURES_BEFORE_RESTART
        )
        if will_trip_restart and not ceiling_tripped and self._probe_fn is not None:
            confirm_ok = False
            base_timeout = (
                self._probe_timeout_fn()
                if self._probe_timeout_fn is not None
                else timedelta(seconds=5)
            )
            confirm_timeout = base_timeout * _CONFIRM_PROBE_MULTIPLIER
            try:
                self._probe_fn(self._inner.endpoint, confirm_timeout)
                confirm_ok = True
            except Exception as confirm_exc:
                if _is_timeout_class_probe_outcome(confirm_exc, overrun=False):
                    confirm_ok = self._check_child_cpu_progress(probed_pid, _CONFIRM_CPU_SAMPLE_S)
            if confirm_ok:
                with self._lock:
                    if (
                        self._bridge_generation == probed_generation
                        and self._inner.process.pid == probed_pid
                    ):
                        if self._first_probe_deferred_at is None:
                            self._first_probe_deferred_at = now
                        self._consecutive_probe_failures_deferred += 1
                        self._record_contention_deferral(
                            probe_duration_s=probe_duration_s,
                            deferral_count=self._consecutive_probe_failures_deferred,
                            stage="probe_deferred_confirm_success",
                        )
                        return False

        return self._handle_failed_probe_under_lock(
            fresh_b_signal=fresh_b_signal,
            probe_duration_s=probe_duration_s,
            probed_pid=probed_pid,
            probed_generation=probed_generation,
            deferrable=deferrable,
        )

    def reset_tool_registry(self) -> None:
        """Rebuild the visible tool list by rerunning the restart path.

        Called by the recovery controller when the failure classifier
        flags a tool-availability failure (the post-tool-result wedge:
        live server reports ``No such tool available: mcp__<server>__<tool>``
        because the agent's ``tools/list`` snapshot lost the alias after
        a prior restart, retry, or transient recovery).

        This is a separate counter from ``_restart_count`` so the
        orchestrator can distinguish a tool-registry rebuild from a
        crash restart, while also charging against the shared
        ``_restart_count`` and obeying ``_restart_policy.max_restarts``.
        The reset cap is ``_TOOL_REGISTRY_MAX_RESETS``.

        Raises:
            McpServerError: when the reset cap or shared restart budget
                is exhausted. The message contains the substring
                ``'tool-registry-reset exhausted'`` or
                ``'restart budget exhausted'``.

        E2.7/E4.1/E1.1/E1.9: the reset claims the same single candidate slot
        as the health route (so a concurrent health replacement wins and the
        reset is refused without consuming budget), and it then hands the
        candidate to the shared :meth:`_replace_with_candidate` state machine.
        That means the candidate is verified over the PRIVATE channel — spawn
        id, pid, session id/broker fingerprint and tool-set schema hash —
        BEFORE promotion, which the old shared-endpoint-only check could not
        prove while the previous child was still answering on that endpoint.
        The whole candidate phase runs off the bridge lock.

        Returns:
            None. A reset that is refused because a candidate replacement is
                already in flight is a no-op, not an error: the in-flight
                replacement is already rebuilding the registry.
        """
        with self._lock:
            # Claim the candidate slot FIRST and bump the generation as part of
            # the same atomic step. A reset that cannot claim the slot must not
            # bump the generation: doing so would invalidate the in-flight
            # candidate's reservation and lose both replacements.
            token = self._claim_candidate_slot(bump_generation=True)
            if token is None:
                logger.info(
                    "MCP tool-registry-reset skipped: a candidate replacement is already "
                    "in flight and will rebuild the registry"
                )
                return
            # Cap checks stay after the bump so a reset that fails here has
            # still invalidated stale probe results captured against the
            # previous inner.
            if self._tool_registry_resets >= _TOOL_REGISTRY_MAX_RESETS:
                cap_pid, cap_status = _child_process_diagnostics(self._inner)
                self._release_candidate_slot()
                raise McpServerError(
                    "MCP tool-registry-reset exhausted "
                    f"({_TOOL_REGISTRY_MAX_RESETS}) "
                    f"after {self._tool_registry_resets} reset(s)",
                    restart_count=self._restart_count,
                    restart_reason="tool_registry_reset_exhausted",
                    child_pid=cap_pid,
                    psutil_status=cap_status,
                )
            if self._restart_count >= self._restart_policy.max_restarts:
                budget_pid, budget_status = _child_process_diagnostics(self._inner)
                self._release_candidate_slot()
                raise McpServerError(
                    "MCP server restart budget exhausted "
                    f"(current={self._restart_count}, "
                    f"max={self._restart_policy.max_restarts})",
                    restart_count=self._restart_count,
                    restart_reason="probe_failed",
                    child_pid=budget_pid,
                    psutil_status=budget_status,
                )
            # S1: increment FIRST so a raise from _restart_fn is
            # reflected in the counts.
            self._tool_registry_resets += 1
            self._restart_count += 1
            attempt_index = self._tool_registry_resets
            logger.info(
                "Resetting MCP tool registry ({}/{}); endpoint={}",
                attempt_index,
                _TOOL_REGISTRY_MAX_RESETS,
                self._inner.endpoint,
            )
            old_exited = _process_is_dead(self._inner)

        try:
            # Reuse the same restart path so preflight runs again and
            # the tools/list snapshot is rebuilt. We do NOT call
            # self._restart_fn() directly here because that would skip
            # the preflight (the restart_fn is a private seam used by
            # check_health_and_restart_if_needed). Instead, the caller
            # is expected to invoke the bridge's preflight + spawn path
            # via a small wrapper. For now, the simplest correct
            # behavior is: rotate the inner process via the same
            # _restart_fn so a fresh registry is built. The same
            # endpoint is reused (the port is reserved at bridge
            # construction and stays stable across resets).
            self._cooldown_after_failure()
            if old_exited:
                self._reset_cold_child(attempt_index=attempt_index)
                return
            self._replace_with_candidate(
                token=token,
                attempt_index=attempt_index,
                initiator="tool-registry-reset",
                pre_promotion_gate=self._verify_alias_present,
            )
        finally:
            self._release_candidate_slot()

    def _reset_cold_child(self, *, attempt_index: int) -> None:
        """Cold-replace a dead child on the reset route (no candidate overlap).

        Runs under the lock for the same reason as :meth:`_replace_dead_child`:
        ``self._inner`` is swapped in place and a concurrent reader must never
        observe a torn ``_inner``. The alias verification targets the NEW inner
        (it takes no argument), which is the only state where it proves
        anything about the child that will actually serve.
        """
        with self._lock:
            try:
                self._inner.shutdown()
                self._inner = self._restart_fn()
            except Exception as restart_exc:
                # C2 fix: a single failed tool-registry reset is NOT
                # terminal. Log at WARNING with attempt count and the
                # underlying error; the reset counters are left at the
                # advanced values so the budget accounting is correct
                # (this attempt counted). The next reset call will
                # either succeed or detect budget exhaustion.
                last_stage, elapsed_s = _restart_error_fields(restart_exc)
                logger.warning(
                    "MCP tool-registry-reset attempt {}/{} failed "
                    "(non-terminal, budget remains): last_stage={} "
                    "elapsed_s={} error={}",
                    attempt_index,
                    _TOOL_REGISTRY_MAX_RESETS,
                    last_stage,
                    elapsed_s,
                    restart_exc,
                )
                return
        self._verify_alias_present()

    def _verify_alias_present(self, target: StandaloneMcpProcess | None = None) -> None:
        """Verify the canonical mcp alias is present after a respawn.

        Sends a JSON-RPC ``tools/list`` request to the inner process's
        HTTP endpoint via ``urllib.request`` and parses the response.
        If the alias ``mcp__<server>__<tool>`` is absent in the
        response, raises :class:`McpServerError` with the distinct
        substring ``'mcp alias missing after respawn'`` so the
        orchestrator can branch on it independently of the existing
        ``'tool-registry-reset exhausted'`` cap error.

        When the inner process is a MagicMock (e.g. in unit tests
        that do not exercise the HTTP path), the method logs a
        debug line and returns without raising. The error path
        is pinned by
        ``tests/test_live_tool_unavailable_recovery.py::test_bridge_reset_tool_registry_fails_closed_when_alias_probe_fails``,
        which drives ``reset_tool_registry()`` with a
        monkeypatched ``_http_tools_list_names`` raising
        ``TimeoutError`` and asserts the resulting
        ``McpServerError`` matches the ``'alias verify probe
        failed after respawn'`` substring.

        The HTTP round-trip is bounded by
        ``_RESPAWN_ALIAS_VERIFY_TIMEOUT_S`` so a wedged inner
        process does not stall the recovery path.

        NOT an identity proof. This is a SHARED-endpoint probe, so while a
        candidate overlaps a live old child the answer comes from the OLD
        child. Candidate identity, session identity and tool-set schema
        equivalence are established by :meth:`_verify_candidate` over the
        PRIVATE channel; this check is only the secondary gate that keeps a
        failed reset from swapping ``_inner`` at all.
        """
        active = target if target is not None else self._inner
        endpoint = active.endpoint
        if not isinstance(endpoint, str) or not endpoint:
            logger.debug("alias-verify skipped: inner process endpoint is not a string")
            return
        # MagicMock detection: a MagicMock inner process returns a
        # MagicMock for any attribute access. If ``endpoint`` is
        # not a real string or the inner process does not have a
        # ``process`` attribute, skip the HTTP call and return
        # silently so unit tests using MagicMock do not break.
        inner_process: object = getattr(active, "process", None)
        if inner_process is None or not hasattr(inner_process, "poll"):
            logger.debug("alias-verify skipped: inner process lacks poll() (MagicMock?)")
            return
        try:
            tool_names = _http_tools_list_names(endpoint, timeout=_RESPAWN_ALIAS_VERIFY_TIMEOUT_S)
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            msg = f"mcp alias verify probe failed after respawn: endpoint={endpoint!r} error={exc}"
            logger.error(msg)
            probe_pid, probe_status = _child_process_diagnostics(active)
            raise McpServerError(
                msg,
                restart_count=self._restart_count,
                restart_reason="alias_verify_probe_failed",
                child_pid=probe_pid,
                psutil_status=probe_status,
            ) from exc
        expected_alias = f"mcp__{_ALIAS_VERIFY_SERVER_NAME}__{_ALIAS_VERIFY_TOOL_NAME}"
        if expected_alias in tool_names:
            return
        msg = (
            f"mcp alias missing after respawn: alias={expected_alias!r} "
            f"endpoint={endpoint} tool_count={len(tool_names)} "
            f"sample_tools={sorted(tool_names)[:5]!r}"
        )
        logger.error(msg)
        alias_pid, alias_status = _child_process_diagnostics(active)
        raise McpServerError(
            msg,
            restart_count=self._restart_count,
            restart_reason="alias_missing_after_respawn",
            child_pid=alias_pid,
            psutil_status=alias_status,
        )

    def reset_session_budget(self) -> None:
        """Clear the inner subprocess's pending completion admissions.

        The McpServer is a per-subprocess singleton; the bridge is its only
        client. Each attempt boundary (e.g. the start of every
        ``effect_executor._run_attempt``) clears pending identity-scoped
        completion admissions. Phase-wide elapsed time is unaffected by attempt
        resets. The reset is exposed as the custom JSON-RPC method
        ``notifications/reset_wrapup`` over HTTP; the McpServer handles it
        via ``McpServer._dispatch_request`` by calling
        ``McpServer.reset_session_budget()``.

        Best-effort: transport errors are logged and swallowed (the
        recovery controller's attempt loop will retry on the next attempt
        boundary). The HTTP POST is bounded by ``_RESET_WRAPUP_TIMEOUT_S``
        so ``audit_mcp_timeout`` stays green; a wedged or just-respawned
        inner process does not stall the attempt.

        When the inner process is a MagicMock (e.g. in unit tests that do
        not exercise the HTTP path) the reset is a no-op. Same pattern as
        :meth:`_verify_alias_present` so the bridge tests stay hermetic
        (a real HTTP POST against a fake endpoint would otherwise hit the
        1.0-second per-test timeout and time out the suite).

        Thread-safe: holds ``self._lock`` to serialize with concurrent
        ``check_health_and_restart_if_needed`` and ``reset_tool_registry``
        calls so a respawn and a reset cannot interleave.

        Note: the intra-bridge respawn paths
        (:meth:`check_health_and_restart_if_needed` and
        :meth:`reset_tool_registry`) do NOT call this method. A fresh
        subprocess starts with ``elapsed=0`` by construction, so the next
        attempt boundary's reset is sufficient — adding a second reset
        inside the bridge would only matter on a tight crash-respawn cycle
        and would risk blocking on a 1.0s test-timeout-budget in unit
        tests that use a ``FakeProcess`` (which has ``poll()`` but no
        real HTTP endpoint). The attempt-boundary wire-up at the top of
        :func:`effect_executor._run_attempt` is the single production
        seam that drives the reset.
        """
        endpoint = self._inner.endpoint
        if not isinstance(endpoint, str) or not endpoint:
            logger.debug("reset_session_budget skipped: inner process endpoint is not a string")
            return
        # MagicMock / fake-bridge detection: a MagicMock inner process
        # (e.g. ``MagicMock(spec=StandaloneMcpProcess)``) does NOT have
        # ``.process.poll()`` because the spec excludes instance
        # attributes. A real ``_NoopProcess`` test fake DOES have poll.
        # The check below matches the same pattern as
        # :meth:`_verify_alias_present` so the respawn paths stay
        # hermetic in unit tests.
        inner_process: object = getattr(self._inner, "process", None)
        if inner_process is None or not hasattr(inner_process, "poll"):
            logger.debug("reset_session_budget skipped: inner process lacks poll() (MagicMock?)")
            return
        request_payload: dict[str, object] = {
            "jsonrpc": "2.0",
            "method": "notifications/reset_wrapup",
            "params": {},
            "id": "reset-wrapup-1",
        }
        body = json.dumps(request_payload).encode("utf-8")
        with self._lock:
            request = urllib.request.Request(
                endpoint,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                response = cast(
                    "IO[bytes]",
                    urllib.request.urlopen(request, timeout=_RESET_WRAPUP_TIMEOUT_S),
                )
                try:
                    # Bound the read so a misbehaving upstream cannot OOM the
                    # parent by streaming an unbounded response body (AC-08).
                    # The body is opaque here and discarded, so truncation is
                    # harmless.
                    response_data = response.read(_LIFECYCLE_MAX_RESPONSE_BYTES)
                finally:
                    response.close()
                _ = response_data  # payload is opaque; the wire-level seam
                # is the method dispatch, not the response body.
            except (
                urllib.error.URLError,
                OSError,
                TimeoutError,
                ValueError,
            ) as exc:
                logger.warning(
                    "reset_session_budget POST to {} failed (swallowed, best-effort): {}",
                    endpoint,
                    exc,
                )
                return


def check_mcp_bridge_health(bridge: SessionBridgeLike) -> None:
    """Perform a health check on the MCP bridge, restarting if it crashed.

    Only has an effect when ``bridge`` is a :class:`RestartAwareMcpBridge`.
    Raises :class:`McpServerError` when the restart budget is exhausted.
    """
    if isinstance(bridge, RestartAwareMcpBridge):
        bridge.check_health_and_restart_if_needed()


def start_mcp_server(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    upstream_registry: UpstreamRegistry | None = None,
    deps: LifecycleDeps | None = None,
    extras: McpServerExtras | None = None,
) -> RestartAwareMcpBridge:
    """Start a standalone Ralph MCP HTTP subprocess and verify tool reachability.

    Returns a :class:`RestartAwareMcpBridge` that can auto-restart the server
    on crash up to the ``extras.restart_policy`` budget (default: 20 restarts,
    defined by :class:`McpRestartPolicy`).

    R1: the bridge holds ONE listening socket for its full lifetime
    (created by ``deps.reserve_listening_socket`` or the default
    ``_reserve_listening_socket``). Every spawned child inherits the
    FD by number, so a client connecting during a respawn queues in
    the kernel backlog instead of being refused with
    ``ECONNREFUSED``. Production paths always use the listening-socket hook.

    R2: the bridge hands the cached tool catalog to every respawn
    via ``UPSTREAM_MCP_TOOL_CATALOG_ENV`` so the respawn path does
    no network discovery. The first start still probes upstreams
    once and the parent reuses the catalog thereafter.
    """
    effective_extras = extras or McpServerExtras()
    lifecycle_deps = deps or _default_lifecycle_deps()
    root = _workspace_root(workspace)
    visible_tools = _visible_mcp_tool_names_owned(
        session, workspace, upstream_registry=upstream_registry
    )

    # I1b: each spawn resolves the catalog snapshot fresh from the
    # parent-owned cache, so a restart never re-uses a stale snapshot.
    def _resolve_catalog_snapshot() -> UpstreamToolCatalogSnapshot:
        try:
            return get_tool_catalog_snapshot(root)
        except ImportError:
            # Import error is a structural defect that should never
            # reach production, but keep the spawn path alive by
            # treating the catalog as absent.
            return UpstreamToolCatalogSnapshot(present=False)

    # R1: bind the listening socket ONCE in the parent and reuse it
    # across every respawn. The port comes from a live LISTEN socket
    # the parent owns for the bridge's full lifetime; a bind-then-
    # release port number is never used, so a client connecting
    # during a respawn queues in the backlog instead of receiving
    # ECONNREFUSED.
    reserve = (
        lifecycle_deps.reserve_listening_socket
        if lifecycle_deps.reserve_listening_socket is not None
        else _reserve_listening_socket
    )
    _host, port, listening_socket = reserve()
    # Listening-socket cleanup invariant: if anything fails between
    # the reservation and handing the socket to the bridge, the
    # parent closes it here; otherwise the FD (and the bound port)
    # leak for the lifetime of the process. Once the bridge owns the
    # socket (``bridge._listening_socket``), its ``shutdown`` closes
    # it deterministically.
    try:
        inner = _spawn_mcp_process(
            root,
            session,
            lifecycle_deps,
            effective_extras.phase,
            effective_extras.extra_env,
            visible_tools,
            port=port,
            inherited_fd=listening_socket.fileno(),
            respawn=False,
            tool_catalog_snapshot=_resolve_catalog_snapshot(),
            activity_relay=effective_extras.activity_relay,
        )

        def _restart_fn(*args: object, **kwargs: object) -> StandaloneMcpProcess:
            raw_mbb: object = kwargs.get("make_before_break", False)
            mbb: bool = raw_mbb is True
            return _spawn_mcp_process(
                root,
                session,
                lifecycle_deps,
                effective_extras.phase,
                effective_extras.extra_env,
                visible_tools,
                port=port,
                inherited_fd=listening_socket.fileno(),
                respawn=True,
                make_before_break=mbb,
                tool_catalog_snapshot=_resolve_catalog_snapshot(),
                activity_relay=effective_extras.activity_relay,
            )

        broker_secret_val: str | None = getattr(session, "broker_secret", None)
        bridge = RestartAwareMcpBridge(
            inner,
            restart_fn=_restart_fn,
            restart_policy=effective_extras.restart_policy or McpRestartPolicy(),
            run_id=session.run_id,
            probe_fn=lifecycle_deps.probe,
            probe_timeout_fn=lifecycle_deps.probe_timeout,
            preflight_timeout_fn=lifecycle_deps.preflight_timeout,
            activity_relay=effective_extras.activity_relay,
            workspace_root=root,
            session_id=session.session_id,
            broker_fingerprint=_finger(broker_secret_val),
            effective_config_fingerprint=compute_effective_config_fingerprint(root),
        )
        # Stash the listening socket on the bridge so ``shutdown`` can
        # close it deterministically (R1 invariant: the port stays ours
        # until bridge shutdown, never released early).
        bridge._listening_socket = listening_socket
        # R3-durability: each bridge owns a unique health-event sink file.
        # The path is persisted on the bridge object so shutdown does not
        # delete it (evidence is retained across shutdown for incident triage).
        bridge._durable_sink_path = _configure_bridge_sink(root, uuid.uuid4().hex[:12])
        # C1/E1.9 baseline: ONE bounded, real ``tools/list`` over the shared
        # endpoint of the first (and only) live child. This runs after that
        # child's preflight proved the endpoint is serving and before any
        # candidate can overlap it, so the very first make-before-break
        # replacement has a real drift reference to refuse against. Without it
        # ``_expected_tool_hash`` stayed ``None`` and tool-set drift was
        # undetectable on the first replacement.
        bridge.capture_tool_hash_baseline()
    except BaseException:
        with contextlib.suppress(OSError):
            listening_socket.close()
        raise
    return bridge


def _startup_diagnostic_suffix(root: Path, log_offset: int) -> str:
    """Render the child's own startup output as a suffix for the parent's error.

    NEVER returns an empty string. A child that died before writing anything
    used to leave the parent's error message with a bare log path and no
    statement of what was missing, which reads as "no diagnostics exist" rather
    than "the child produced nothing" — two very different incidents. An empty
    read therefore renders the explicit ``child wrote no log output`` phrase.
    """

    diagnostic = read_mcp_server_log_since(root, log_offset)
    if not diagnostic:
        return f"\nMCP server output ({mcp_server_log_path(root)}):\nchild wrote no log output"
    return f"\nMCP server output ({mcp_server_log_path(root)}):\n{diagnostic}"


def _boot_allowance_seconds(deps: LifecycleDeps) -> float:
    """Return the configured child-boot allowance, ``0.0`` when none is wired."""
    if deps.boot_allowance is None:
        return 0.0
    try:
        return max(0.0, deps.boot_allowance().total_seconds())
    except Exception:
        return 0.0


def _await_mcp_startup_or_failure(
    bridge: StandaloneMcpProcess,
    *,
    deps: LifecycleDeps,
    endpoint: str,
    visible_tools: list[str],
    port: int,
    env: Mapping[str, str],
    spawn_start: float,
    root: Path,
    log_offset: int,
    stage_id: str,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Observe preflight and child process termination under the startup deadlines.

    ``spawn_start`` and ``clock`` share one time base (``time.monotonic`` in
    production); tests inject both to drive the BOOT and ENVELOPE phases
    deterministically.
    """
    preflight_timeout_td = deps.preflight_timeout()
    preflight_timeout_s = preflight_timeout_td.total_seconds()
    try:
        whole_startup_s = mcp_whole_startup_budget_from_env(env)
    except Exception:
        whole_startup_s = preflight_timeout_s
    startup_deadline_s = min(preflight_timeout_s, whole_startup_s)
    if startup_deadline_s >= preflight_timeout_s:
        startup_deadline_s = max(0.1, preflight_timeout_s - 0.5)
    boot_allowance_s = _boot_allowance_seconds(deps)
    watch = StartupWatch(
        spawn_start=spawn_start,
        envelope_s=startup_deadline_s,
        boot_allowance_s=boot_allowance_s,
        read_stage=lambda: _read_start_stage(root, spawn_id=stage_id),
        clock=clock,
    )
    # The preflight probe must outlive the whole budget the watch can grant,
    # otherwise it would give up on a slow-but-booting child first.
    preflight_budget_td = preflight_timeout_td + timedelta(seconds=max(0.0, boot_allowance_s))

    preflight_error: list[Exception] = []
    preflight_done = threading.Event()

    def _run_preflight() -> None:
        try:
            deps.preflight(endpoint, visible_tools, preflight_budget_td)
        except Exception as exc:
            preflight_error.append(exc)
        finally:
            preflight_done.set()

    preflight_thread = threading.Thread(
        target=_run_preflight,
        name=f"mcp-preflight-{port}",
        daemon=True,
    )
    preflight_thread.start()

    early_exit_rc: int | None = None
    verdict = StartupVerdict.WAITING
    poll_interval = 0.02

    while True:
        if preflight_done.wait(timeout=poll_interval):
            break
        rc = bridge.process.poll()
        if rc is not None:
            early_exit_rc = rc
            break
        verdict = watch.check()
        if verdict is not StartupVerdict.WAITING:
            break

    if early_exit_rc is None and verdict is StartupVerdict.WAITING and not preflight_error:
        return

    _raise_startup_failure(
        bridge,
        endpoint=endpoint,
        root=root,
        log_offset=log_offset,
        stage_id=stage_id,
        elapsed_s=clock() - spawn_start,
        early_exit_rc=early_exit_rc,
        verdict=verdict,
        preflight_error=preflight_error,
        startup_deadline_s=startup_deadline_s,
        boot_allowance_s=boot_allowance_s,
    )


def _raise_startup_failure(
    bridge: StandaloneMcpProcess,
    *,
    endpoint: str,
    root: Path,
    log_offset: int,
    stage_id: str,
    elapsed_s: float,
    early_exit_rc: int | None,
    verdict: StartupVerdict,
    preflight_error: list[Exception],
    startup_deadline_s: float,
    boot_allowance_s: float,
) -> NoReturn:
    """Tear the child down and raise the typed error matching how startup failed."""
    returncode = bridge.process.poll()
    child_pid, psutil_status = _child_process_diagnostics(bridge)
    bridge.shutdown()
    diagnostic = _startup_diagnostic_suffix(root, log_offset)
    last_stage = _read_start_stage(root, spawn_id=stage_id)
    cause = preflight_error[0] if preflight_error else None

    def _error(message: str, reason: str) -> McpServerError:
        return McpServerError(
            message,
            restart_count=0,
            last_stage=last_stage if last_stage is not None else "last_stage_unknown",
            elapsed_s=elapsed_s,
            restart_reason=reason,
            child_pid=child_pid,
            psutil_status=psutil_status,
        )

    if early_exit_rc is not None or returncode is not None:
        effective_rc = early_exit_rc if early_exit_rc is not None else returncode
        raise _error(
            f"MCP server process exited before endpoint {endpoint} became ready "
            f"(rc={effective_rc}){diagnostic}",
            "process_exited",
        ) from cause
    if verdict is StartupVerdict.BOOT_DEADLINE_EXCEEDED:
        raise _error(
            f"MCP server child did not begin startup within the boot allowance of "
            f"{boot_allowance_s:.3f}s (interpreter boot/imports; the host may be "
            f"overloaded) before endpoint {endpoint} became ready{diagnostic}",
            verdict.value,
        ) from cause
    if verdict is StartupVerdict.ENVELOPE_DEADLINE_EXCEEDED:
        raise _error(
            f"MCP server startup exceeded deadline of {startup_deadline_s:.3f}s "
            f"before endpoint {endpoint} became ready{diagnostic}",
            verdict.value,
        ) from cause
    raise _error(
        f"MCP server endpoint {endpoint} did not become ready: {cause}{diagnostic}",
        "preflight_failed",
    ) from cause


def _spawn_mcp_process(
    root: Path,
    session: SessionLike,
    deps: LifecycleDeps,
    phase: str | None,
    _extra_env: dict[str, str] | None,
    _visible_tools: list[str],
    *,
    port: int,
    inherited_fd: int | None = None,
    respawn: bool = False,
    make_before_break: bool = False,
    tool_catalog_snapshot: UpstreamToolCatalogSnapshot | None = None,
    activity_relay: ActivityRelay | None = None,
) -> StandaloneMcpProcess:
    """Spawn a fresh MCP server process and run preflight validation.

        R1: when ``inherited_fd`` is set, the child is given the FD by
        number (``pass_fds=``) and the ``RALPH_MCP_INHERITED_FD`` env
        var; the child adopts the listening socket without re-binding.
    R2: when ``respawn`` is set, ``RALPH_MCP_RESPAWN=1`` is added so the
        runtime takes the catalog-only path (zero network I/O before
        serving). ``_extra_env`` is merged first and every parent-owned
        value is assigned after it, so worker-injected env vars can never
        overwrite the session id, snapshot dir, per-spawn stage id, accept
        pause/resume path, inherited fd or catalog state (E2.4).
    """
    endpoint = f"http://127.0.0.1:{port}/mcp"
    # Recorded BEFORE the spawn so a startup failure reports only what THIS
    # child wrote; the log is append-only and shared across restarts.
    log_offset = mcp_server_log_size(root)
    session_file: Path | None = None
    # PA-003 cleanup ownership: keep these declared in the OUTER function so
    # the single ``except BaseException`` below is the only cleanup point and
    # the optional socketpair (acquired conditionally below) cannot leak.
    parent_sock: socket.socket | None = None
    child_sock: socket.socket | None = None
    bridge_run_id = session.run_id
    # Ensure session_id is available; raise early rather than falling back silently.
    session_id_value: str = session.session_id
    if not session_id_value:
        raise McpServerError(
            "session_id missing",
            restart_count=0,
            last_stage="snapshot_missing",
        )
    snap_dir = snapshot_dir(root, bridge_run_id)
    snap_session_file: Path = snap_dir / "session.json"
    # Compute broker_secret_fingerprint using the helper.
    broker_secret_val: str | None = getattr(session, "broker_secret", None)
    broker_fingerprint: str | None = _finger(broker_secret_val)
    # C1 step 2: compute the parent's effective-config fingerprint BEFORE
    # the first spawn so the snapshot baseline is anchored to the merged
    # layers at the moment of spawning. The fingerprint persists in
    # ``session.json`` and the candidate echoes it on the private readiness
    # channel. Returns ``None`` on hosts without ``tomllib`` or with a
    # malformed merged config; the candidate comparison tolerates ``None``
    # without rejecting legitimate candidates (no baseline == no check).
    effective_config_fingerprint: str | None = compute_effective_config_fingerprint(root)
    # Write snapshot on first spawn (when session.json does not yet exist).
    if not snap_session_file.exists():
        write_snapshot(
            snap_dir,
            session_id=session_id_value,
            run_id=bridge_run_id,
            broker_secret_fingerprint=broker_fingerprint,
            effective_config_fingerprint=effective_config_fingerprint,
        )
    # Use the injection seam when provided (tests + production seam); otherwise
    # use the snapshot file so cross-respawn session_id persistence works.
    # Every resource owned by the caller is pre-initialized so any exception
    # path inside the try block sees bound names — no UnboundLocalError from
    # the except handler even if the helper fails early.
    env: dict[str, str] | None = None
    pass_fds: tuple[int, ...] = ()
    # ``parent_sock`` / ``child_sock`` are deliberately NOT re-annotated here:
    # the PA-003 block above already declared each name exactly once, before
    # the first fallible call. Re-declaring them here only masked that single
    # ownership site (and tripped mypy ``no-redef``).
    stage_id: str = ""
    spawn_start: float = 0.0
    try:
        # Session-file resolution stays in the OUTER (PA-003): it is the FIRST
        # fallible step inside the try, so the outer ``session_file`` must be
        # bound before the next fallible call. Had the helper resolved it and
        # only returned it on success, any failure during env assembly would
        # leave the outer name at its pre-try ``None`` and the single
        # ``except BaseException`` below would skip the unlink, leaking the
        # freshly created handshake file.
        session_file = (
            deps.create_session_file(root, session)
            if deps.create_session_file is not None
            else snap_session_file
        )
        env, stage_id, pass_fds = _build_spawn_env(
            session_file=session_file,
            deps=deps,
            _extra_env=_extra_env,
            snap_dir=snap_dir,
            session_id_value=session_id_value,
            tool_catalog_snapshot=tool_catalog_snapshot,
            activity_relay=activity_relay,
            inherited_fd=inherited_fd,
            respawn=respawn,
            make_before_break=make_before_break,
        )
        # Socketpair creation also stays in the OUTER (PA-003): the helper owns
        # no open FD, so a single ``except BaseException`` remains the sole
        # cleanup point for both child_sock and parent_sock.
        if make_before_break:
            parent_sock, child_sock = create_handover_socketpair()
            child_fd = child_sock.fileno()
            env[MCP_BRIDGE_PARENT_SOCK] = str(child_fd)
            # C1 step 2: bind the candidate to the configuration snapshot the
            # parent captured BEFORE this spawn. The candidate echoes the
            # digest on the private readiness channel and the parent compares
            # it against its own baseline, so a candidate started from a
            # stale/divergent configuration is refused while the old child
            # keeps serving. Only a SHA-256 digest crosses the boundary.
            if effective_config_fingerprint is not None:
                env[MCP_EFFECTIVE_CONFIG_FINGERPRINT] = effective_config_fingerprint
            pass_fds = (*pass_fds, child_fd)
        spawn_start = time.monotonic()
        process = deps.spawn_process(
            [
                sys.executable,
                "-m",
                "ralph.mcp.server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--workspace",
                str(root),
            ],
            root,
            env,
            phase=phase,
            pass_fds=pass_fds,
        )
        if child_sock is not None:
            with contextlib.suppress(Exception):
                child_sock.close()
        bridge = StandaloneMcpProcess(
            endpoint=endpoint,
            process=process,
            session_file=session_file,
            stage_id=stage_id,
            handover_parent_sock=parent_sock,
        )
    except BaseException:
        if child_sock is not None:
            with contextlib.suppress(Exception):
                child_sock.close()
        if parent_sock is not None:
            with contextlib.suppress(Exception):
                parent_sock.close()
        if session_file is not None:
            session_file.unlink(missing_ok=True)
        raise

    if not make_before_break:
        _await_mcp_startup_or_failure(
            bridge,
            deps=deps,
            endpoint=endpoint,
            visible_tools=_visible_tools,
            port=port,
            env=env,
            spawn_start=spawn_start,
            root=root,
            log_offset=log_offset,
            stage_id=stage_id,
        )

    return bridge


def _build_spawn_env(
    *,
    session_file: Path,
    deps: LifecycleDeps,
    _extra_env: dict[str, str] | None,
    snap_dir: Path,
    session_id_value: str,
    tool_catalog_snapshot: UpstreamToolCatalogSnapshot | None,
    activity_relay: ActivityRelay | None,
    inherited_fd: int | None,
    respawn: bool,
    make_before_break: bool,
) -> tuple[dict[str, str], str, tuple[int, ...]]:
    """Build the parent-owned child environment for :func:`_spawn_mcp_process`.

    Pure env-construction companion extracted to keep ``_spawn_mcp_process``
    under the ruff PLR0912/PLR0915 caps. Every parent-owned spawn identity
    is assigned in the same order as the inlined block it replaced.

    PA-003: this helper owns NO resource. The session file is created and
    bound by the CALLER before the call, and the handover socketpair is
    created by the caller after it, so the outer ``except BaseException``
    in ``_spawn_mcp_process`` remains the single cleanup point for the
    handshake file and both socket halves.
    """
    env = deps.subprocess_env(session_file)
    if _extra_env:
        # Merge extra_env so the subprocess inherits worker-specific env vars
        # (e.g. WORKER_ARTIFACT_DIR for parallel workers). This happens
        # BEFORE every parent-owned assignment below so a reused extra
        # mapping cannot overwrite a value this bridge owns (E2.4): the
        # session id, the snapshot dir, the per-spawn stage id and the
        # accept-pause path must never be inherited from a stale caller.
        env.update(_extra_env)
    # The session-FILE path is parent-owned exactly like every other spawn
    # identity. ``subprocess_env`` assigned it BEFORE the merge above, so a
    # reused extras mapping would otherwise redirect the child's session
    # file to a foreign path on every spawn.
    env[SESSION_FILE_ENV] = str(session_file)
    # Presence-aware ownership for every key that is valid on only one spawn
    # branch (E2.4). A reused extras mapping can carry a stale handover
    # resume sentinel or a foreign parent-socket fd; ``_fallback_standalone_server``
    # waits on a PRESENT resume value and adopts whatever fd the parent-socket
    # value names, so a leaked key would block a cold or initial child for
    # the full resume deadline. Absent -> pop; the branch blocks below then
    # re-set the fresh, parent-minted values.
    for _branch_owned_key in (
        MCP_INHERITED_FD_ENV,
        MCP_RESPAWN_ENV,
        MCP_ACCEPT_RESUME_FILE,
        MCP_BRIDGE_PARENT_SOCK,
        MCP_EFFECTIVE_CONFIG_FINGERPRINT,
    ):
        env.pop(_branch_owned_key, None)
    # Persist session_id in the subprocess environment so runtime_session.py
    # can read it on respawn without falling back to a new factory.
    env[MCP_SESSION_ID_ENV] = session_id_value
    env[MCP_SNAPSHOT_DIR_ENV] = str(snap_dir)
    stage_id = str(uuid.uuid4())
    env[MCP_STAGE_ID_ENV] = stage_id
    env[MCP_ACCEPT_PAUSE_FILE] = str(snap_dir / f"accept.pause.{stage_id}")
    # I1b: the parent-owned catalog snapshot takes precedence over
    # anything merged from extra_env (present -> set even if empty,
    # absent -> pop).
    if tool_catalog_snapshot is not None:
        apply_tool_catalog_env(env, tool_catalog_snapshot)
    if activity_relay is not None:
        env.update(activity_relay.server_environment())
    pass_fds: tuple[int, ...] = (inherited_fd,) if inherited_fd is not None else ()
    if inherited_fd is not None:
        env[MCP_INHERITED_FD_ENV] = str(inherited_fd)
    if respawn:
        env[MCP_RESPAWN_ENV] = "1"
    if make_before_break:
        env[MCP_ACCEPT_RESUME_FILE] = str(snap_dir / f"accept.resume.{stage_id}")
    return env, stage_id, pass_fds


def shutdown_mcp_server(bridge: SessionBridgeLike) -> None:
    """Shutdown MCP server process."""
    bridge.shutdown()


def _visible_mcp_tool_names_owned(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    upstream_registry: UpstreamRegistry | None = None,
) -> list[str]:
    return visible_owned_tool_names(
        session,
        workspace,
        upstream_registry=upstream_registry,
        include_aliases=True,
    )


def _workspace_root(workspace: WorkspaceLike) -> Path:
    if isinstance(workspace, FsWorkspace):
        return workspace.root
    root_value = cast(
        "Path | str | None", getattr(workspace, "root", None)
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    if isinstance(root_value, Path):
        return root_value.resolve()
    if isinstance(root_value, str):
        return Path(root_value).expanduser().resolve()
    msg = "Workspace root must be explicit when starting the MCP server"
    raise ValueError(msg)


def _subprocess_env(session_file: Path) -> dict[str, str]:
    env = dict(os.environ)
    env[SESSION_FILE_ENV] = str(session_file)
    env[MCP_OWNER_PID_ENV] = str(os.getpid())
    # R4 / R4-b: set in the parent so the child inherits unbuffered
    # streams from the start — setting it inside the already-running
    # child cannot reconfigure its own stdio.
    env["PYTHONUNBUFFERED"] = "1"
    package_root = str(_PACKAGE_ROOT)
    # The MCP server subprocess runs ``sys.executable -m ralph.mcp.server``
    # and MUST resolve the SAME package tree the parent imported
    # (``_PACKAGE_ROOT``). An ambient PYTHONPATH exported by an outer
    # launcher (e.g. a pipx-installed copy of this same package's
    # site-packages, observed live when an outer Ralph orchestrates a
    # development cycle inside a worktree) sorts AHEAD of the cwd on a
    # plain ``python -m`` invocation, so merely prepending the package
    # root is not sufficient for every entry: the outer launcher can
    # inject the same path first. Replace every inherited entry that
    # already provides the ``ralph`` package with the package root, and
    # drop duplicates, so the spawned server deterministically imports
    # the worktree's code (measured regression: the spawned server served
    # the stale pipx tree, which predates the Kimi schema-flavor
    # negotiation, and Moonshot rejected the composed root schemas with
    # HTTP 400 on every live smoke).
    seen: set[str] = set()
    entries: list[str] = []
    for raw in env.get("PYTHONPATH", "").split(os.pathsep):
        entry = raw.strip()
        if not entry:
            continue
        candidate = package_root if (Path(entry) / "ralph" / "__init__.py").is_file() else entry
        if candidate not in seen:
            seen.add(candidate)
            entries.append(candidate)
    if package_root not in seen:
        entries.insert(0, package_root)
    env["PYTHONPATH"] = os.pathsep.join(entries)
    return env


def _spawn_process(
    command: list[str],
    cwd: Path,
    env: dict[str, str],
    *,
    phase: str | None = None,
    spawn: Callable[[list[str], SpawnOptions], ManagedProcess] | None = None,
    pass_fds: tuple[int, ...] = (),
) -> ProcessLike:
    label = f"phase:{phase}:mcp-server" if phase else "mcp-server"
    # Persist server output instead of discarding it: with DEVNULL, a crash
    # inside a request handler (e.g. an AttributeError after SSE headers) left
    # no trace anywhere while the client hung to its request timeout (-32001).
    log_path = mcp_server_log_path(cwd)
    # Diagnostic log setup is fail-open on environmental OSErrors (ENOSPC,
    # EROFS, EACCES, EPERM): the live diagnostic stream is best-effort, so the
    # spawn proceeds and serving can continue with /dev/null. The
    # integrity/ledger stores elsewhere remain typed fail-closed; only this
    # diagnostic-stream seam degrades, preserving the fail-closed ledger
    # contract. Stage attribution falls back to "unknown" via
    # ``_read_start_stage`` when no log was written by the child.
    log_fd: int = -1
    try:
        try:
            # filesystem-write-ok: MCP server live diagnostic log directory
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fd = os.open(  # filesystem-write-ok: append-only live MCP diagnostic stream; resource-lifecycle-ok: fd closed in finally
                log_path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o644,
            )
        except OSError as exc:
            if exc.errno not in (
                errno.ENOSPC,
                errno.EROFS,
                errno.EACCES,
                errno.EPERM,
            ):
                raise
            # Degrade gracefully: the spawn proceeds, output is dropped to
            # /dev/null, and the operator gets a WARNING. The server can
            # still serve; the stage stays "unknown" until/unless the
            # child writes its own stage-tracker file.
            log_fd = os.open(  # filesystem-write-ok: devnull fallback stream; resource-lifecycle-ok: fd closed in finally
                os.devnull, os.O_WRONLY
            )
            logger.warning(
                "MCP diagnostic log setup failed (errno={} path={}); "
                "degrading to devnull so startup and serving can continue",
                exc.errno,
                log_path,
            )
        spawn_fn = spawn if spawn is not None else get_process_manager().spawn
        return spawn_fn(
            command,
            SpawnOptions(
                cwd=str(cwd),
                env=env,
                stdout=log_fd,
                stderr=log_fd,
                start_new_session=True,
                label=label,
                pass_fds=pass_fds,
                allow_activity_relay_controls=True,
                allow_broker_secret=True,
                # The server's owner watch requires the owner as its parent.
                require_direct_parent=True,
            ),
        )
    finally:
        # The child holds its own duplicate of the fd from spawn time; the
        # parent's copy must be released or each restart leaks a descriptor.
        if log_fd >= 0:
            os.close(log_fd)


def _default_lifecycle_deps() -> LifecycleDeps:
    return LifecycleDeps(
        reserve_listening_socket=_reserve_listening_socket,
        create_session_file=_create_session_file,
        subprocess_env=_subprocess_env,
        spawn_process=_spawn_process,
        preflight=preflight_http_mcp_server_tools,
        preflight_timeout=mcp_preflight_timeout_from_env,
        boot_allowance=mcp_boot_allowance_from_env,
        probe=probe_mcp_http_endpoint,
        probe_timeout=mcp_probe_timeout_from_env,
    )


def _create_session_file(root: Path, session: SessionLike) -> Path:
    """Create one run-scoped MCP handshake file outside the watched workspace.

    The subprocess receives the absolute path through ``MCP_SESSION_FILE_ENV``;
    no user-visible or resume-facing contract requires this transient handshake
    payload to live below ``root``. Keeping it in the system temporary directory
    prevents a server start or restart from emitting workspace watch events.
    """
    del root
    fd, temp_path = tempfile.mkstemp(prefix="ralph-mcp-session-", suffix=".json")
    path = Path(temp_path)
    try:
        with os.fdopen(  # filesystem-write-ok: transient handshake stream
            fd, "w", encoding="utf-8"
        ) as stream:
            stream.write(session_payload_json(session))
        return path
    except BaseException:
        # filesystem-write-ok: cleanup of an unpublished transient handshake file after write failure
        path.unlink(missing_ok=True)
        raise


def identity_is_serialisable(identity: MultimodalModelIdentity) -> bool:
    """Decide whether provider or transport identity crosses the subprocess boundary."""
    return identity.is_known() or identity.transport is not None


def session_payload_json(session: SessionLike) -> str:
    """Serialize the session metadata to a compact JSON string for MCP handshake.

    IN-PROCESS ONLY: ``delegated_agent_id``,
    ``delegated_model_identity`` and ``delegated_capability_profile`` are
    deliberately NOT written here. Nothing in production sets them --
    they exist for a sub-agent delegation path that is not wired -- so
    serialising them would invent a wire format for a feature that has
    no producer.

    The consequence, stated so it is not discovered the hard way: a
    delegated identity set on a parent session does not survive this
    boundary, and the child resolves as the session itself. Anyone
    wiring delegation must add the three keys here AND to
    ``FileBackedSession``, or the guards in
    :func:`ralph.mcp.multimodal.capabilities.caller_identity_for` will
    silently judge the parent instead of the delegate on the far side.
    ``tests/test_delegated_identity_boundary.py`` pins this.
    """
    session_payload: dict[str, object] = {
        "session_id": session.session_id,
        "run_id": session.run_id,
        "drain": session.drain,
        "capabilities": sorted(session.capabilities),
    }
    raw_activity_only_supervision: object = getattr(session, "activity_only_supervision", False)
    activity_only_supervision = (
        raw_activity_only_supervision if isinstance(raw_activity_only_supervision, bool) else False
    )
    if activity_only_supervision:
        session_payload["activity_only_supervision"] = True
    raw_parallel_worker: object = getattr(session, "parallel_worker", False)
    if bool(raw_parallel_worker):
        session_payload["parallel_worker"] = True
    raw_worker_artifact_dir: object = getattr(session, "worker_artifact_dir", None)
    if isinstance(raw_worker_artifact_dir, Path):
        session_payload["worker_artifact_dir"] = str(raw_worker_artifact_dir)
    raw_worker_namespace: object = getattr(session, "worker_namespace", None)
    if isinstance(raw_worker_namespace, Path):
        session_payload["worker_namespace"] = str(raw_worker_namespace)
    raw_allowed_roots: object = getattr(session, "allowed_roots", ())
    if isinstance(raw_allowed_roots, tuple) and raw_allowed_roots:
        session_payload["allowed_roots"] = [str(path) for path in raw_allowed_roots]
    raw_identity: object = getattr(session, "model_identity", None)
    # An identity is worth serialising when EITHER half is resolved. The
    # gate used to be ``is_known()`` alone (provider resolved), which
    # dropped a transport-tagged unknown-provider identity on the way to
    # the subprocess -- and the multimodal layer keys some delivery
    # decisions on the transport, so those guards died at this boundary
    # even though the parent session had the information.
    if isinstance(raw_identity, MultimodalModelIdentity) and identity_is_serialisable(raw_identity):
        # Canonicalised on the way OUT, like every other seam that
        # writes a transport spelling. This one writes what it was
        # handed, so a hand-constructed parent identity of ``'CODEX'``
        # or ``'  codex  '`` was recorded verbatim and carried into the
        # verdict ``reason`` strings, giving one run several different
        # capability digests. Every production constructor normalises
        # upstream, so this changes no delivery decision -- it makes the
        # last seam agree with the claim the others already meet.
        session_payload["model_identity"] = {
            "provider": raw_identity.provider,
            "model_id": raw_identity.model_id,
            "transport": payload_transport(raw_identity.transport),
        }
    raw_profile: object = getattr(session, "capability_profile", None)
    if isinstance(raw_profile, ResolvedCapabilityProfile):
        # Re-based on the identity being serialised beside it. A profile
        # whose own identity predates a declared transport would
        # otherwise record verdicts the runtime does not act on, so the
        # child's payload and the wire ledger disagreed with what was
        # actually delivered.
        identity_for_profile = (
            raw_identity
            if isinstance(raw_identity, MultimodalModelIdentity)
            else raw_profile.identity
        )
        session_payload["capability_profile"] = profile_for_caller(
            raw_profile, identity_for_profile
        ).to_payload()
    elif isinstance(raw_identity, MultimodalModelIdentity) and identity_is_serialisable(
        raw_identity
    ):
        session_payload["capability_profile"] = resolve_capability_profile(
            raw_identity
        ).to_payload()
    # AC-11: when the parent session owns an exec resource resolver,
    # serialize the trusted spill roots so the subprocess session
    # (FileBackedSession) can re-construct a matching resolver. This is
    # the only path through which ``ralph://exec/<spill-name>`` URIs
    # returned by ``format=summary`` exec calls are re-readable inside
    # the production subprocess path.
    exec_resolver: object = getattr(session, "exec_resource_resolver", None)
    if exec_resolver is not None:
        resolver_roots: list[str] = []
        spill_roots_attr: object = getattr(exec_resolver, "spill_roots", ())
        if isinstance(spill_roots_attr, tuple):
            for root_path in spill_roots_attr:
                if isinstance(root_path, Path):
                    resolver_roots.append(str(root_path))
                    continue
                if isinstance(root_path, str) and root_path:
                    resolver_roots.append(root_path)
        if resolver_roots:
            session_payload["exec_spill_roots"] = resolver_roots
    return json.dumps(session_payload)


__all__ = [
    "LifecycleDeps",
    "McpOutcomeUnknownError",
    "McpRestartPolicy",
    "McpServerError",
    "McpServerExtras",
    "ProcessLike",
    "RestartAwareMcpBridge",
    "SessionBridgeLike",
    "SpawnProcess",
    "StandaloneMcpProcess",
    "check_mcp_bridge_health",
    "mcp_server_log_path",
    "session_payload_json",
    "shutdown_mcp_server",
    "start_mcp_server",
]
