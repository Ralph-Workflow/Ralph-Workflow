from __future__ import annotations

import time
from pathlib import Path

import pytest

import ralph.pipeline._integration_diagnostic as diagnostic
from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    parse_record_payload,
)
from ralph.pipeline.operation_evidence import (
    EvidenceVerdict,
    identity_fingerprint,
)
from tests._resolver_controller import make_resolver_controller


class _InMemoryBackend(FileBackend):
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


def _record_with_lease(held_at: float | None = None) -> IntegrationRecord:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
    )
    held = held_at if held_at is not None else time.time()
    return record.model_copy(
        update={"diagnostic_ownership": f"{identity_fingerprint(record)}:{held}"}
    )


def test_identity_fingerprint_isolated_from_defaulted_fields() -> None:
    record_a = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        integrated_feature_sha="2" * 40,
    )
    record_b = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        integrated_feature_sha="2" * 40,
        repair_last_error="a transient error message",
    )
    assert identity_fingerprint(record_a) == identity_fingerprint(record_b)


def test_operation_root_for_three_call_sites_agree(tmp_path: Path) -> None:
    wt = tmp_path / "some-worktree"
    wt.mkdir()
    record_reconcile = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        operation_kind="target_reconcile",
        owning_worktree=str(wt),
    )
    record_other = IntegrationRecord(
        phase="integrated",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        operation_kind="feature_integrate",
        owning_worktree=str(wt),
    )
    assert diagnostic._operation_root_for(record_reconcile, Path("/default")) == wt
    assert diagnostic._operation_root_for(record_other, Path("/default")) == Path("/default")


def test_lease_expired_auto_releases(tmp_path: Path) -> None:
    from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import (
        read_record,
        write_record,
    )
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    record = _record_with_lease(held_at=time.time() - (diagnostic.EVIDENCE_LEASE_SECONDS + 100))
    record = record.model_copy(update={"diagnostic_evidence": (("reflog", "old"),)})
    write_record(tmp_path, record)

    verdict = diagnostic.release_verified_diagnostic(tmp_path, record)
    assert verdict == EvidenceVerdict.UNKNOWN

    recover_incomplete_integration(WorkspaceScope(tmp_path), backend=DEFAULT_FILE_BACKEND)

    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False
    assert next_integration_action(on_disk) != IntegrationAction.REQUIRE_INTERVENTION


def test_ralph_moves_head_does_not_block(tmp_path: Path) -> None:
    from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import (
        read_record,
        write_record,
    )
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        integrated_feature_sha="2" * 40,
        diagnostic_evidence=(("head", "0" * 40), ("reflog", "old_reflog")),
    )
    lease = f"{identity_fingerprint(record)}:{time.time()}"
    record = record.model_copy(update={"diagnostic_ownership": lease})
    write_record(tmp_path, record)

    verdict = diagnostic.release_verified_diagnostic(tmp_path, record)
    assert verdict == EvidenceVerdict.UNCHANGED

    recover_incomplete_integration(WorkspaceScope(tmp_path), backend=DEFAULT_FILE_BACKEND)

    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False
    assert next_integration_action(on_disk) != IntegrationAction.REQUIRE_INTERVENTION


@pytest.mark.timeout_seconds(5)
def test_ralph_moves_real_head_does_not_block(tmp_path: Path) -> None:
    from git import Actor, Repo
    from git.cmd import Git

    from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import (
        read_record,
        write_record,
    )
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    Git().init(str(tmp_path))
    repo = Repo(tmp_path)
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "t@example.com").release()
    actor = Actor("Test", "t@example.com")
    base = tmp_path / "README.md"
    base.write_text("# Test\n", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("initial", author=actor, committer=actor)
    repo.git.branch("-M", "main")
    feature_file = tmp_path / "feature.txt"
    feature_file.write_text("feature\n", encoding="utf-8")
    repo.index.add(["feature.txt"])
    repo.index.commit("feature base", author=actor, committer=actor)
    feature_sha = repo.head.commit.hexsha
    pre_target_sha = repo.branches.main.commit.hexsha
    (tmp_path / "main.md").write_text("main change\n", encoding="utf-8")
    repo.git.checkout("main")
    repo.index.add(["main.md"])
    repo.index.commit("main advance", author=actor, committer=actor)
    advanced_target_sha = repo.branches.main.commit.hexsha

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha=feature_sha,
        pre_target_sha=pre_target_sha,
        integrated_feature_sha=feature_sha,
        diagnostic_evidence=(("head", feature_sha), ("reflog", "old_reflog")),
    )
    lease = f"{identity_fingerprint(record)}:{time.time()}"
    record = record.model_copy(update={"diagnostic_ownership": lease})
    write_record(tmp_path, record)

    repo.git.checkout(advanced_target_sha)
    new_head = repo.head.commit.hexsha
    assert new_head != feature_sha, "test setup: HEAD must have moved"

    verdict = diagnostic.release_verified_diagnostic(tmp_path, record)
    assert verdict == EvidenceVerdict.UNCHANGED

    recover_incomplete_integration(WorkspaceScope(tmp_path), backend=DEFAULT_FILE_BACKEND)

    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False
    assert next_integration_action(on_disk) != IntegrationAction.REQUIRE_INTERVENTION


def test_legacy_record_loads_without_intervention() -> None:
    legacy: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "0" * 40,
        "pre_target_sha": "1" * 40,
        "diagnostic_evidence": (("for-each-ref", "deadbeef"),),
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.diagnostic_intervention_required is False
    assert dict(parsed.diagnostic_evidence) == {}


def test_loaded_over_cap_counter_proceeds_to_recovery() -> None:
    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "0" * 40,
        "pre_target_sha": "1" * 40,
        "merge_commit_repair_attempts": 541,
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.merge_commit_repair_attempts == diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP - 1
    assert parsed.diagnostic_intervention_required is False


def test_diagnostic_evidence_verdict_expired_lease_with_different_fingerprint_returns_unknown(
    tmp_path: Path,
) -> None:
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
    )
    stale_lease = f"different_fingerprint:{time.time() - (diagnostic.EVIDENCE_LEASE_SECONDS + 100)}"
    record = record.model_copy(update={"diagnostic_ownership": stale_lease})
    verdict = diagnostic.release_verified_diagnostic(tmp_path, record)
    assert verdict == EvidenceVerdict.UNKNOWN


def test_recovery_on_flagged_record_without_evidence_auto_releases(tmp_path: Path) -> None:
    from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import (
        read_record,
        write_record,
    )
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_intervention_required=True,
        merge_commit_repair_attempts=5,
        repair_cap_reason="diagnostic repair exhausted; operator intervention required",
    )
    write_record(tmp_path, record)

    recover_incomplete_integration(WorkspaceScope(tmp_path), backend=DEFAULT_FILE_BACKEND)

    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_intervention_required is False
    assert on_disk.merge_commit_repair_attempts < diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP
    assert on_disk.repair_cap_reason is None
    assert next_integration_action(on_disk) != IntegrationAction.REQUIRE_INTERVENTION


def test_handoff_integration_diagnostic_at_repair_cap_rederives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.pipeline.auto_integrate_record import read_record, write_record

    def _fake_needed(*_args: object, **_kwargs: object) -> bool:
        return False

    def _fake_check(*_args: object, **_kwargs: object) -> tuple[bool, str]:
        return (True, "ok")

    monkeypatch.setattr(diagnostic, "diagnostic_needed", _fake_needed)
    monkeypatch.setattr(diagnostic, "_check_operation_completion_proof", _fake_check)

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        merge_commit_repair_attempts=diagnostic.DIAGNOSTIC_REPAIR_ATTEMPT_CAP,
    )
    lease = f"{identity_fingerprint(record)}:{time.time()}"
    record = record.model_copy(update={"diagnostic_ownership": lease})
    write_record(tmp_path, record)

    res = diagnostic.handoff_integration_diagnostic(
        controller=make_resolver_controller(),
        root=tmp_path,
        record=record,
        failure="diagnostic failure",
        agents=(),
        invoke=lambda agent, prompt: True,
    )
    assert res is True
    on_disk = read_record(tmp_path)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False


def test_load_record_with_fingerprint_mismatched_lease_clears_hold_and_prevents_livelock(
    tmp_path: Path,
) -> None:
    import json

    from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
    from ralph.pipeline._integration_action import (
        IntegrationAction,
        next_integration_action,
    )
    from ralph.pipeline.auto_integrate_record import (
        read_record,
        record_path,
    )
    from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
    from ralph.workspace.scope import WorkspaceScope

    raw_payload = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "0" * 40,
        "pre_target_sha": "1" * 40,
        "diagnostic_evidence": [["unstaged_work", "dirty"]],
        "diagnostic_ownership": f"stale_fingerprint_mismatch:{time.time()}",
        "diagnostic_intervention_required": True,
        "repair_cap_reason": "diagnostic repair exhausted; operator intervention required",
    }
    rec_file = record_path(tmp_path)
    rec_file.parent.mkdir(parents=True, exist_ok=True)
    rec_file.write_text(json.dumps(raw_payload), encoding="utf-8")

    loaded = read_record(tmp_path, backend=DEFAULT_FILE_BACKEND)
    assert loaded is not None
    assert loaded.diagnostic_evidence == ()
    assert loaded.diagnostic_ownership is None
    assert loaded.diagnostic_intervention_required is False
    assert loaded.repair_cap_reason is None

    assert next_integration_action(loaded) != IntegrationAction.REQUIRE_INTERVENTION

    recover_incomplete_integration(WorkspaceScope(tmp_path), backend=DEFAULT_FILE_BACKEND)

    on_disk = read_record(tmp_path, backend=DEFAULT_FILE_BACKEND)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False
    assert on_disk.repair_cap_reason is None
    assert next_integration_action(on_disk) != IntegrationAction.REQUIRE_INTERVENTION


def test_legacy_record_loads_then_next_run_progresses() -> None:
    import json

    import ralph.pipeline.auto_integrate_recovery_record as recovery_record
    from ralph.pipeline._integration_action import IntegrationAction, next_integration_action
    from ralph.pipeline.auto_integrate_record import (
        parse_record_payload,
        read_record,
        record_path,
    )
    from ralph.workspace.scope import WorkspaceScope

    root = Path("/virtual/workspace")
    backend = _InMemoryBackend()

    legacy_payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "0" * 40,
        "pre_target_sha": "1" * 40,
        "diagnostic_evidence": [
            ["for-each-ref", "deadbeef"],
            ["legacy_unrecognised_sensor", "value"],
        ],
        "diagnostic_intervention_required": True,
    }

    parsed = parse_record_payload(legacy_payload)
    assert parsed is not None
    assert parsed.diagnostic_intervention_required is False
    assert dict(parsed.diagnostic_evidence) == {}

    rec_file = record_path(root)
    backend.write_text(rec_file, json.dumps(legacy_payload))

    outcome = recovery_record._maybe_release_diagnostic(
        root,
        WorkspaceScope(root),
        backend,
    )
    assert isinstance(outcome, IntegrationRecord), (
        "next-run release path must NOT escalate to a CHANGED-skip; the "
        "legacy-shape record must progress to a clean, no-intervention state"
    )
    assert outcome.diagnostic_evidence == ()
    assert outcome.diagnostic_ownership is None
    assert outcome.diagnostic_owner_pid is None
    assert outcome.diagnostic_intervention_required is False
    assert next_integration_action(outcome) != IntegrationAction.REQUIRE_INTERVENTION

    on_disk = read_record(root, backend=backend)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_owner_pid is None
    assert on_disk.diagnostic_intervention_required is False
    assert next_integration_action(on_disk) != IntegrationAction.REQUIRE_INTERVENTION


@pytest.mark.parametrize("resolving_rebase", [False, True])
def test_flagged_record_loads_and_clears_into_the_ladder(resolving_rebase: bool) -> None:
    import ralph.pipeline.auto_integrate_recovery_record as recovery_record
    from ralph.pipeline._integration_action import IntegrationAction, next_integration_action
    from ralph.pipeline.auto_integrate_record import (
        parse_record_payload,
        read_record,
        record_path,
    )
    from ralph.workspace.scope import WorkspaceScope

    root = Path("/virtual/workspace")
    backend = _InMemoryBackend()

    evidence = (
        ("head", "0" * 40),
        ("unstaged_work", "clean"),
    )
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        resolving_rebase=resolving_rebase,
        diagnostic_evidence=evidence,
        diagnostic_intervention_required=True,
    )
    lease = f"{identity_fingerprint(record)}:{time.time()}"
    record = record.model_copy(update={"diagnostic_ownership": lease})

    raw_payload: dict[str, object] = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "0" * 40,
        "pre_target_sha": "1" * 40,
        "resolving_rebase": resolving_rebase,
        "diagnostic_evidence": [["head", "0" * 40], ["unstaged_work", "clean"]],
        "diagnostic_intervention_required": True,
        "diagnostic_ownership": lease,
    }
    parsed = parse_record_payload(raw_payload)
    assert parsed is not None
    assert parsed.diagnostic_evidence == ()
    assert parsed.diagnostic_intervention_required is False
    assert parsed.diagnostic_ownership is None
    assert next_integration_action(parsed) != IntegrationAction.REQUIRE_INTERVENTION

    rec_file = record_path(root)
    import json

    backend.write_text(rec_file, json.dumps(raw_payload))
    initial_replace_count = len(backend.replace_calls)

    outcome = recovery_record._maybe_release_diagnostic(
        root,
        WorkspaceScope(root),
        backend,
    )
    assert isinstance(outcome, IntegrationRecord)
    assert outcome.diagnostic_evidence == ()
    assert outcome.diagnostic_ownership is None
    assert outcome.diagnostic_intervention_required is False

    assert len(backend.replace_calls) > initial_replace_count, (
        "durable release transition must atomically persist updated record to backend"
    )
    raw_text = backend.read_text(rec_file)
    assert '"diagnostic_evidence":[]' in raw_text or '"diagnostic_evidence": []' in raw_text
    assert '"diagnostic_ownership":null' in raw_text or '"diagnostic_ownership": null' in raw_text
    assert (
        '"diagnostic_intervention_required":false' in raw_text
        or '"diagnostic_intervention_required": false' in raw_text
    )

    on_disk = read_record(root, backend=backend)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_intervention_required is False
    action = next_integration_action(on_disk)
    assert action != IntegrationAction.REQUIRE_INTERVENTION


def test_kill_mid_diagnostic_next_run_releases_dead_owner() -> None:
    import ralph.pipeline.auto_integrate_recovery_record as recovery_record
    from ralph.pipeline._auto_integrate_record_restamp import is_owner_dead
    from ralph.pipeline._integration_action import IntegrationAction, next_integration_action
    from ralph.pipeline.auto_integrate_record import (
        read_record,
        write_record,
    )
    from ralph.workspace.scope import WorkspaceScope

    root = Path("/virtual/workspace")
    backend = _InMemoryBackend()

    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        diagnostic_evidence=(("head", "0" * 40),),
    )
    lease = f"{identity_fingerprint(record)}:{time.time()}"
    record = record.model_copy(
        update={
            "diagnostic_ownership": lease,
            "diagnostic_owner_pid": 99999999,
        }
    )

    assert is_owner_dead(record) is True, (
        "pre-load dead-owner predicate must fire on diagnostic_owner_pid=99999999"
    )

    write_record(root, record, backend=backend)

    outcome = recovery_record._maybe_release_diagnostic(
        root,
        WorkspaceScope(root),
        backend,
    )
    assert isinstance(outcome, IntegrationRecord), (
        "dead owner must NOT escalate to a CHANGED-skip; the next-run "
        "release path must clear the record"
    )

    on_disk = read_record(root, backend=backend)
    assert on_disk is not None
    assert on_disk.diagnostic_evidence == ()
    assert on_disk.diagnostic_ownership is None
    assert on_disk.diagnostic_owner_pid is None
    assert on_disk.diagnostic_intervention_required is False
    assert next_integration_action(on_disk) != IntegrationAction.REQUIRE_INTERVENTION
