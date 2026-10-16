"""Tests for checkpoint and record migration, filesystem, and persistence boundary.

Verifies:
1. Legacy checkpoint and integration records (including for-each-ref, ls-files,
   dict evidence, and last_failure_cause) load into one authoritative state or
   explicit intervention.
2. Projections are rebuilt one-way with no newest-timestamp merging.
3. Projection writers cannot mutate the authority.
4. Each Transition is durable and atomic through an injected record store/workspace.
5. Crash before and after each transition is idempotent.
6. Checkpoint handle and persistence boundary with injected backend.
"""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.checkpoint.checkpoint_payload import CheckpointPayload
from ralph.checkpoint.execution_history import ExecutionHistory
from ralph.checkpoint.execution_step import ExecutionStep
from ralph.checkpoint.run_context import RunContext
from ralph.checkpoint.step_outcome import StepOutcome
from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline import checkpoint as ckpt
from ralph.pipeline.auto_integrate_record import (
    MARK_RESOLVING_REBASE,
    PROMOTE_TO_INTEGRATED,
    START_INTEGRATING,
    IntegrationRecord,
    RecordBusyError,
    RecordReadOutcomeDisposition,
    RecordUnreadableError,
    clear_record,
    parse_record_payload,
    read_record,
    read_record_outcome,
    record_path,
)
from ralph.pipeline.auto_integrate_record_store import (
    IntegrationRecordStore,
    apply_record_transition,
)
from ralph.pipeline.auto_integrate_record_store import (
    RecordUnreadableError as StoreRecordUnreadableError,
)
from ralph.pipeline.checkpoint import Checkpoint
from ralph.pipeline.rebase_state import RebaseState, ResolutionFailureCause
from ralph.pipeline.state import PipelineState
from ralph.pipeline.state_models import (
    ResolutionFailureCause as ExportedResolutionFailureCause,
)


class _InMemoryBackend(FileBackend):
    """In-memory FileBackend implementation for atomic persistence tests."""

    def __init__(self) -> None:
        self.files: dict[Path, bytes] = {}
        self.write_calls: list[tuple[Path, bytes]] = []
        self.replace_calls: list[tuple[Path, Path]] = []
        self.sync_calls: list[Path] = []
        self.mkdir_calls: list[Path] = []
        self.unlink_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self.files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.mkdir_calls.append(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        if path not in self.files:
            raise FileNotFoundError(f"Path not found: {path}")
        return self.files[path].decode(encoding)

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding))

    def read_bytes(self, path: Path) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(f"Path not found: {path}")
        return self.files[path]

    def write_bytes(self, path: Path, content: bytes) -> None:
        self.write_calls.append((path, content))
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        if source not in self.files:
            raise FileNotFoundError(f"Source not found: {source}")
        self.replace_calls.append((source, destination))
        self.files[destination] = self.files.pop(source)

    def sync_directory(self, path: Path) -> None:
        self.sync_calls.append(path)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        self.unlink_calls.append(path)
        if missing_ok:
            self.files.pop(path, None)
        else:
            if path not in self.files:
                raise FileNotFoundError(f"Path not found: {path}")
            del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


# ---------------------------------------------------------------------------
# 1. State models export contract
# ---------------------------------------------------------------------------


def test_state_models_re_exports_resolution_failure_cause() -> None:
    """ResolutionFailureCause must be exported from ralph.pipeline.state_models."""
    assert ExportedResolutionFailureCause is ResolutionFailureCause
    assert ExportedResolutionFailureCause.NONE.value == "NONE"
    assert ExportedResolutionFailureCause.AGENT_FAILURE.value == "AGENT_FAILURE"


# ---------------------------------------------------------------------------
# 2. Legacy record and checkpoint migration tests
# ---------------------------------------------------------------------------


def test_legacy_record_ls_files_surfaces_explicit_intervention() -> None:
    """Legacy records carrying ls-files evidence key require operator intervention."""
    payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": [
            ["ls-files", "some_tracked_files"],
        ],
    }
    record = parse_record_payload(payload)
    assert record is not None
    assert record.diagnostic_intervention_required is True
    assert "legacy repository-global evidence observation present" in (
        record.repair_cap_reason or ""
    )


def test_legacy_record_prefixed_ls_files_surfaces_intervention() -> None:
    """Legacy records carrying ls-files:stage require operator intervention."""
    payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": [
            ["ls-files:stage", "100644 deadbeef 0 path/file.py"],
        ],
    }
    record = parse_record_payload(payload)
    assert record is not None
    assert record.diagnostic_intervention_required is True


def test_legacy_record_dict_format_evidence_loads_cleanly() -> None:
    """Diagnostic evidence stored as a dictionary loads and migrates one-way."""
    payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": {
            "work": "unstaged diff",
            "controls": "controls data",
        },
    }
    record = parse_record_payload(payload)
    assert record is not None
    assert not record.diagnostic_intervention_required
    evidence_dict = dict(record.diagnostic_evidence)
    assert evidence_dict.get("unstaged_work") == "unstaged diff"
    assert evidence_dict.get("commit_controls") == "controls data"


def test_legacy_record_last_failure_cause_migration() -> None:
    """Legacy records without last_failure_cause migrate to NONE or AGENT_FAILURE."""
    # Default case: no cause, no sentinel reason -> NONE
    payload1: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
    }
    rec1 = parse_record_payload(payload1)
    assert rec1 is not None
    assert rec1.last_failure_cause == ResolutionFailureCause.NONE

    # Sentinel reason -> AGENT_FAILURE
    payload2: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "last_reason": "resolution_agent_failure",
    }
    rec2 = parse_record_payload(payload2)
    assert rec2 is not None
    assert rec2.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE

    # repair_last_error sentinel -> AGENT_FAILURE
    payload3: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "repair_last_error": "resolution_agent_failure",
    }
    rec3 = parse_record_payload(payload3)
    assert rec3 is not None
    assert rec3.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE

    # Explicit failure cause -> preserved
    payload4: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "last_failure_cause": "UNRESOLVED_CONFLICT",
    }
    rec4 = parse_record_payload(payload4)
    assert rec4 is not None
    assert rec4.last_failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT


def test_corrupt_record_last_failure_cause_fails_closed() -> None:
    """Corrupt last_failure_cause value in record payload causes loader to fail closed."""
    payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "last_failure_cause": "INVALID_UNKNOWN_CAUSE",
    }
    rec = parse_record_payload(payload)
    assert rec is None


# ---------------------------------------------------------------------------
# 3. Projections rebuilt one-way with no newest-timestamp merging
# ---------------------------------------------------------------------------


def test_checkpoint_payload_from_dict_and_load_preserves_authority() -> None:
    """CheckpointPayload reconstructs state as single authority without timestamp merging."""
    state = PipelineState(
        phase="development",
        rebase=RebaseState(
            last_reason="resolution_agent_failure",
        ),
    )
    run_ctx = RunContext.new()
    history = ExecutionHistory.new()
    payload = CheckpointPayload(
        state=state,
        run_context=run_ctx,
        execution_history=history,
        working_dir="/test/dir",
    )

    data = payload.to_dict()
    # Inject divergent timestamp into raw json dict to test that it is NOT merged
    data["legacy_timestamp"] = "2099-01-01T00:00:00Z"
    data["timestamp"] = "2099-01-01T00:00:00Z"

    reconstructed = CheckpointPayload.from_dict(data)
    assert reconstructed.state.phase == "development"
    assert reconstructed.phase == "development"
    # State was not overwritten or mutated by newest timestamp
    assert reconstructed.state.rebase.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE

    # Loading via checkpoint.load from backend
    backend = _InMemoryBackend()
    path = Path("/workspace/.agent/checkpoint.json")
    backend.write_text(path, json.dumps(data))

    loaded_state = ckpt.load(path, backend=backend)
    assert loaded_state is not None
    assert loaded_state.phase == "development"
    assert loaded_state.rebase.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE


# ---------------------------------------------------------------------------
# 4. Projection writers cannot mutate the authority
# ---------------------------------------------------------------------------


def test_projection_writers_cannot_mutate_authority() -> None:
    """IntegrationRecord and PipelineState are immutable and cannot be mutated by projection writers."""
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
        last_failure_cause=ResolutionFailureCause.NONE,
    )
    with pytest.raises(ValidationError):
        record.phase = "integrated"

    with pytest.raises(ValidationError):
        record.last_failure_cause = ResolutionFailureCause.AGENT_FAILURE

    state = PipelineState(phase="planning")
    with pytest.raises(ValidationError):
        state.phase = "complete"

    # RebaseState projection cannot mutate record
    rebase = RebaseState(last_outcome=None)
    with pytest.raises(ValidationError):
        rebase.fast_forwarded = True


# ---------------------------------------------------------------------------
# 5. Transition durability and atomicity through injected record store
# ---------------------------------------------------------------------------


def test_integration_record_store_transitions_atomic_and_durable() -> None:
    """Each Transition is durably and atomically persisted through IntegrationRecordStore."""
    backend = _InMemoryBackend()
    root = Path("/test-workspace")
    store = IntegrationRecordStore(root, backend=backend)

    assert store.read() is None

    # 1. START_INTEGRATING
    initial = store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )
    assert initial.phase == "integrating"
    assert initial.target == "main"
    assert backend.exists(store.record_path)
    assert len(backend.replace_calls) == 1

    # 2. MARK_RESOLVING_REBASE
    resolving = store.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert resolving.resolving_rebase is True
    assert len(backend.replace_calls) == 2

    # 3. PROMOTE_TO_INTEGRATED
    promoted = store.transition(
        PROMOTE_TO_INTEGRATED,
        phase="integrated",
        integrated_feature_sha="3" * 40,
        resolving_rebase=False,
    )
    assert promoted.phase == "integrated"
    assert promoted.integrated_feature_sha == "3" * 40
    assert len(backend.replace_calls) == 3


def test_apply_record_transition_helper() -> None:
    """apply_record_transition convenience helper writes through injected store."""
    backend = _InMemoryBackend()
    root = Path("/workspace")
    rec = apply_record_transition(
        root,
        START_INTEGRATING,
        backend=backend,
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha=None,
    )
    assert rec.phase == "integrating"
    read_back = read_record(root, backend=backend)
    assert read_back is not None
    assert read_back.pre_feature_sha == "a" * 40


# ---------------------------------------------------------------------------
# 6. Crash before/after each transition idempotence
# ---------------------------------------------------------------------------


def test_crash_before_transition_idempotent() -> None:
    """A crash before transition execution leaves prior state intact; retry applies it."""
    backend = _InMemoryBackend()
    root = Path("/crash-test")
    store = IntegrationRecordStore(root, backend=backend)

    store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )
    replaces_before = len(backend.replace_calls)

    # Simulated crash: no mutation performed. Next run performs the transition cleanly:
    updated = store.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert updated.resolving_rebase is True
    assert len(backend.replace_calls) == replaces_before + 1


def test_crash_after_transition_idempotent_replay() -> None:
    """Replaying an already applied transition after a crash is idempotent and no-op."""
    backend = _InMemoryBackend()
    root = Path("/crash-test")
    store = IntegrationRecordStore(root, backend=backend)

    store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )
    store.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)

    replaces_after = len(backend.replace_calls)

    # Replay identical transition after crash:
    replayed = store.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert replayed.resolving_rebase is True
    # atomic_write_bytes_if_changed skips write and replace on unchanged payload
    assert len(backend.replace_calls) == replaces_after


def test_crash_after_promote_to_integrated_idempotent_replay() -> None:
    """Replaying PROMOTE_TO_INTEGRATED after crash is idempotent and does not trip guard."""
    backend = _InMemoryBackend()
    root = Path("/crash-test")
    store = IntegrationRecordStore(root, backend=backend)

    store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="1" * 40,
        pre_target_sha="2" * 40,
    )
    store.transition(
        PROMOTE_TO_INTEGRATED,
        phase="integrated",
        integrated_feature_sha="3" * 40,
    )

    replaces_count = len(backend.replace_calls)

    # Crash after write: replaying the transition with same integrated sha returns current record
    replayed = store.transition(
        PROMOTE_TO_INTEGRATED,
        phase="integrated",
        integrated_feature_sha="3" * 40,
    )
    assert replayed.phase == "integrated"
    assert replayed.integrated_feature_sha == "3" * 40
    # No new replace performed
    assert len(backend.replace_calls) == replaces_count


# ---------------------------------------------------------------------------
# 7. Checkpoint handle with backend boundary tests
# ---------------------------------------------------------------------------


def test_checkpoint_handle_with_injected_backend() -> None:
    """Checkpoint class operations are routed through the injected FileBackend."""
    backend = _InMemoryBackend()
    path = Path("/agent/checkpoint.json")
    # Stray tmp file should be cleaned up on init
    tmp_path = Path("/agent/checkpoint.json.tmp")
    backend.write_text(tmp_path, "stray bytes")

    handle = Checkpoint(path, backend=backend)
    assert not backend.exists(tmp_path)
    assert not handle.exists()

    state = PipelineState(phase="planning")
    handle.save(state)
    assert handle.exists()
    assert backend.exists(path)

    loaded = handle.load()
    assert loaded is not None
    assert loaded.phase == "planning"

    summary = handle.inspect()
    assert '"phase": "planning"' in summary

    handle.remove()
    assert not handle.exists()
    assert not backend.exists(path)


@pytest.mark.asyncio
async def test_checkpoint_async_methods_with_injected_backend() -> None:
    """Checkpoint async methods operate cleanly with injected FileBackend."""
    backend = _InMemoryBackend()
    path = Path("/agent/async_checkpoint.json")
    handle = Checkpoint(path, backend=backend)

    state = PipelineState(phase="development")
    await handle.save_async(state)
    assert handle.exists()

    loaded = await handle.load_async()
    assert loaded is not None
    assert loaded.phase == "development"


# ---------------------------------------------------------------------------
# 8. Execution components round-trip through from_dict
# ---------------------------------------------------------------------------


def test_execution_components_round_trip() -> None:
    """StepOutcome, ExecutionStep, ExecutionHistory, RunContext round trip cleanly."""
    outcome = StepOutcome.failure("disk error", recoverable=True)
    outcome_dict = outcome.to_dict()
    reconstructed_outcome = StepOutcome.from_dict(outcome_dict)
    assert reconstructed_outcome.kind == "failure"
    assert reconstructed_outcome.error == "disk error"
    assert reconstructed_outcome.recoverable is True

    step = ExecutionStep.new("planning", 1, "test_step", outcome)
    step_dict = step.to_dict()
    reconstructed_step = ExecutionStep.from_dict(step_dict)
    assert reconstructed_step.phase == "planning"
    assert reconstructed_step.iteration == 1
    assert reconstructed_step.outcome.kind == "failure"

    history = ExecutionHistory.new({"file.txt": "sha123"}).add_step_bounded(step, limit=5)
    hist_dict = history.to_dict()
    reconstructed_hist = ExecutionHistory.from_dict(hist_dict)
    assert len(reconstructed_hist.steps) == 1
    assert reconstructed_hist.file_snapshots["file.txt"] == "sha123"

    run_ctx = RunContext.new().record_developer_iteration().record_reviewer_pass()
    ctx_dict = run_ctx.to_dict()
    reconstructed_ctx = RunContext.from_dict(ctx_dict)
    assert reconstructed_ctx.actual_developer_runs == 1
    assert reconstructed_ctx.actual_reviewer_runs == 1


# ---------------------------------------------------------------------------
# 8. Typed record read outcome (read_record_outcome) -- closed dispositions
# ---------------------------------------------------------------------------


class _TransientBackend(_InMemoryBackend):
    """Backend whose first read of a path fails with the given errno/error."""

    def __init__(self, files: dict[Path, bytes], *, err: Exception) -> None:
        super().__init__()
        self.files.update(files)
        self._err = err
        self.busy_reads = 1

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        if self.busy_reads > 0:
            self.busy_reads -= 1
            raise self._err
        return super().read_text(path, encoding=encoding)


def _valid_record_bytes() -> bytes:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
    )
    return record.model_dump_json().encode("utf-8")


def test_read_record_outcome_absent() -> None:
    backend = _InMemoryBackend()
    outcome = read_record_outcome(Path("/ws"), backend=backend)
    assert outcome.disposition is RecordReadOutcomeDisposition.ABSENT
    assert outcome.record is None
    assert outcome.unreadable_reason is None


def test_read_record_outcome_present_round_trips_exact_record() -> None:
    backend = _InMemoryBackend()
    root = Path("/ws")
    backend.files[record_path(root)] = _valid_record_bytes()
    outcome = read_record_outcome(root, backend=backend)
    assert outcome.disposition is RecordReadOutcomeDisposition.PRESENT
    assert outcome.record is not None
    assert outcome.record.target == "main"
    # Restart re-read: same bytes -> identical record, byte-for-byte payload preserved
    outcome2 = read_record_outcome(root, backend=backend)
    assert outcome2.record == outcome.record
    assert backend.read_bytes(record_path(root)) == _valid_record_bytes()


def test_read_record_outcome_corrupt_json_is_unreadable() -> None:
    backend = _InMemoryBackend()
    root = Path("/ws")
    backend.files[record_path(root)] = b"{not json at all"
    outcome = read_record_outcome(root, backend=backend)
    assert outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE
    assert outcome.unreadable_reason == "corrupt_json"
    assert outcome.record is None


def test_read_record_outcome_unsupported_schema_is_unreadable() -> None:
    backend = _InMemoryBackend()
    root = Path("/ws")
    # Valid JSON dict, but an unknown phase value (older / partial write).
    payload: dict[str, object] = {"phase": "warping", "target": "main"}
    backend.files[record_path(root)] = json.dumps(payload).encode("utf-8")
    outcome = read_record_outcome(root, backend=backend)
    assert outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE
    assert outcome.unreadable_reason == "unsupported_schema"


def test_read_record_outcome_eagain_is_busy_with_no_minted_deadline() -> None:
    """BUSY is reserved for demonstrable EAGAIN/EWOULDBLOCK contention.

    No attempt/deadline is minted at read time: a fresh deadline would
    replenish the bounded-retry budget on every failing read. The
    consumer must consult existing PERSISTED retry state and decide
    REQUIRE_INTERVENTION itself when no valid unexpired deadline exists.
    """
    root = Path("/ws")
    err = OSError(errno.EAGAIN, "resource temporarily unavailable")
    backend = _TransientBackend({record_path(root): _valid_record_bytes()}, err=err)
    outcome = read_record_outcome(root, backend=backend)
    assert outcome.disposition is RecordReadOutcomeDisposition.BUSY
    assert outcome.busy_attempt is None
    assert outcome.busy_deadline is None
    # Second read succeeds: BUSY is transient, not a permanent state.
    outcome2 = read_record_outcome(root, backend=backend)
    assert outcome2.disposition is RecordReadOutcomeDisposition.PRESENT


def test_read_record_outcome_ewouldblock_is_busy() -> None:
    root = Path("/ws")
    err = OSError(errno.EWOULDBLOCK, "operation would block")
    backend = _TransientBackend({record_path(root): _valid_record_bytes()}, err=err)
    outcome = read_record_outcome(root, backend=backend)
    assert outcome.disposition is RecordReadOutcomeDisposition.BUSY
    assert outcome.busy_deadline is None


@pytest.mark.parametrize(
    ("label", "err"),
    [
        ("permission", OSError(errno.EACCES, "permission denied")),
        ("generic_os", OSError("backend exploded")),
        ("decode", UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")),
        ("keyerror", KeyError("backend internal key missing")),
    ],
)
def test_read_record_outcome_read_failures_are_unreadable(label: str, err: Exception) -> None:
    """Backend read/permission/decoding failures map to UNREADABLE, never BUSY."""
    del label
    root = Path("/ws")
    backend = _TransientBackend({record_path(root): _valid_record_bytes()}, err=err)
    outcome = read_record_outcome(root, backend=backend)
    assert outcome.disposition is RecordReadOutcomeDisposition.UNREADABLE
    assert outcome.unreadable_reason == "backend_read_failure"
    assert outcome.record is None


def test_clear_record_refuses_to_unlink_unreadable_evidence() -> None:
    backend = _InMemoryBackend()
    root = Path("/ws")
    backend.files[record_path(root)] = b"{corrupt"
    with pytest.raises(RecordUnreadableError):
        clear_record(root, backend=backend)
    # Evidence is byte-identical: the refusal never mutates the file.
    assert backend.read_bytes(record_path(root)) == b"{corrupt"


def test_clear_record_absent_is_idempotent() -> None:
    backend = _InMemoryBackend()
    clear_record(Path("/never-existed"), backend=backend)


def test_store_transition_refuses_start_integrating_over_unreadable_evidence() -> None:
    backend = _InMemoryBackend()
    root = Path("/ws")
    corrupt = b"{unreadable evidence"
    backend.files[record_path(root)] = corrupt
    store = IntegrationRecordStore(root, backend=backend)
    with pytest.raises(StoreRecordUnreadableError):
        store.transition(
            START_INTEGRATING,
            target="main",
            pre_feature_sha="a" * 40,
            pre_target_sha=None,
        )
    # The unreadable file is left byte-identical; no fresh START_INTEGRATING
    # record was written over the evidence.
    assert backend.read_bytes(record_path(root)) == corrupt
    assert backend.write_calls == []
    assert backend.replace_calls == []


def test_store_transition_refuses_all_mutations_on_busy_read() -> None:
    """BUSY (EAGAIN contention) refuses every mutation, preserving file bytes.

    With no valid unexpired persisted deadline, the consumer decides
    REQUIRE_INTERVENTION; the store never mints a fresh allowance.
    """
    root = Path("/ws")
    existing = _valid_record_bytes()
    err = OSError(errno.EAGAIN, "resource temporarily unavailable")
    backend = _TransientBackend({record_path(root): existing}, err=err)
    backend.busy_reads = 10  # persistent contention across the two store reads
    store = IntegrationRecordStore(root, backend=backend)
    with pytest.raises(RecordBusyError):
        store.transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    with pytest.raises(RecordBusyError):
        store.transition(
            START_INTEGRATING,
            target="main",
            pre_feature_sha="a" * 40,
            pre_target_sha=None,
        )
    assert backend.read_bytes(record_path(root)) == existing
    assert backend.write_calls == []
    assert backend.replace_calls == []


def test_store_transition_busy_absent_file_does_not_create_record() -> None:
    """BUSY on a transiently-unreadable existing file never creates a fresh record."""
    root = Path("/ws")
    evidence = b"{torn write evidence"
    err = OSError(errno.EAGAIN, "lock held")
    backend = _TransientBackend({record_path(root): evidence}, err=err)
    backend.busy_reads = 10  # persistent contention across the two store reads
    store = IntegrationRecordStore(root, backend=backend)
    with pytest.raises(RecordBusyError):
        store.transition(
            START_INTEGRATING,
            target="main",
            pre_feature_sha="a" * 40,
            pre_target_sha=None,
        )
    assert backend.read_bytes(record_path(root)) == evidence


def test_store_transition_absent_start_integrating_semantics_preserved() -> None:
    backend = _InMemoryBackend()
    store = IntegrationRecordStore(Path("/ws"), backend=backend)
    initial = store.transition(
        START_INTEGRATING,
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha=None,
    )
    assert initial.phase == "integrating"
    assert backend.exists(store.record_path)
