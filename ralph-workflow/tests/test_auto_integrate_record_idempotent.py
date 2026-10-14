"""Black-box regression coverage for durable auto-integration record publication."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.artifacts.file_backend import FileBackend
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record


class _RecordBackend(FileBackend):
    """In-memory durable publication boundary that exposes observable mutations."""

    def __init__(self) -> None:
        self.files: dict[Path, bytes] = {}
        self.write_calls: list[tuple[Path, bytes]] = []
        self.replace_calls: list[tuple[Path, Path]] = []
        self.sync_calls: list[Path] = []
        self.mkdir_calls: list[Path] = []

    def exists(self, path: Path) -> bool:
        return path in self.files

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.mkdir_calls.append(path)

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        return self.files[path].decode(encoding)

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        self.write_bytes(path, content.encode(encoding))

    def read_bytes(self, path: Path) -> bytes:
        return self.files[path]

    def write_bytes(self, path: Path, content: bytes) -> None:
        self.write_calls.append((path, content))
        self.files[path] = content

    def replace(self, source: Path, destination: Path) -> None:
        self.replace_calls.append((source, destination))
        self.files[destination] = self.files.pop(source)

    def sync_directory(self, path: Path) -> None:
        self.sync_calls.append(path)

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if missing_ok:
            self.files.pop(path, None)
        else:
            del self.files[path]

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return []


def _record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feature-before",
        pre_target_sha="target-before",
    )


def test_auto_integrate_record_regression_identical_replay_skips_publication_and_barrier() -> None:
    """S-2: replaying unchanged durable state makes no new filesystem mutations."""
    backend = _RecordBackend()
    root = Path("/virtual-workspace")
    record = _record()

    write_record(root, record, backend=backend)
    writes_after_first = list(backend.write_calls)
    replaces_after_first = list(backend.replace_calls)
    syncs_after_first = list(backend.sync_calls)
    mkdirs_after_first = list(backend.mkdir_calls)

    write_record(root, record, backend=backend)

    assert backend.write_calls == writes_after_first
    assert backend.replace_calls == replaces_after_first
    assert backend.sync_calls == syncs_after_first
    assert backend.mkdir_calls == mkdirs_after_first
    record_file = root / ".agent" / "auto_integrate_in_progress.json"
    assert backend.files[record_file] == record.model_dump_json().encode("utf-8")


# --- Transition-table coverage (Work Unit B) ---


def test_transition_table_has_fifteen_unique_entries() -> None:
    """The closed transition table has 15 unique values."""
    from ralph.pipeline.auto_integrate_record import Transition, transition_by_name

    names = {t.name for t in Transition.ALL}
    assert len(names) == 15
    # And each name resolves through the lookup.
    for name in names:
        assert transition_by_name(name).name == name


def test_transition_apply_rejects_unknown_field() -> None:
    """Smuggling an unknown field past the table raises ValueError."""
    from ralph.pipeline.auto_integrate_record import MARK_RESOLVING_REBASE

    record = _record()
    with pytest.raises(ValueError, match="does not accept field"):
        record.with_transition(MARK_RESOLVING_REBASE, target="develop")


def test_with_transition_uses_legal_field_map() -> None:
    """``MARK_RESOLVING_REBASE`` writes only the resolving_rebase field."""
    from ralph.pipeline.auto_integrate_record import MARK_RESOLVING_REBASE

    record = _record()
    updated = record.with_transition(MARK_RESOLVING_REBASE, resolving_rebase=True)
    assert updated.resolving_rebase is True
    # No other field was touched.
    assert updated.target == record.target
    assert updated.pre_feature_sha == record.pre_feature_sha


def test_release_diagnostic_evidence_clears_fields() -> None:
    """``RELEASE_DIAGNOSTIC_EVIDENCE`` clears the diagnostic evidence and ownership."""
    from ralph.pipeline.auto_integrate_record import (
        HOLD_DIAGNOSTIC_EVIDENCE,
        RELEASE_DIAGNOSTIC_EVIDENCE,
    )

    record = _record()
    held = record.with_transition(
        HOLD_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(("head", "abc"),),
        diagnostic_ownership="fingerprint",
    )
    assert held.diagnostic_evidence == (("head", "abc"),)
    released = held.with_transition(
        RELEASE_DIAGNOSTIC_EVIDENCE,
        diagnostic_evidence=(),
        diagnostic_ownership=None,
    )
    assert released.diagnostic_evidence == ()
    assert released.diagnostic_ownership is None


def test_raise_diagnostic_intervention_sets_field() -> None:
    """``RAISE_DIAGNOSTIC_INTERVENTION`` is the bounded-retry terminus."""
    from ralph.pipeline.auto_integrate_record import RAISE_DIAGNOSTIC_INTERVENTION

    record = _record()
    updated = record.with_transition(
        RAISE_DIAGNOSTIC_INTERVENTION,
        diagnostic_intervention_required=True,
        repair_cap_reason="diagnostic repair exhausted; operator intervention required",
    )
    assert updated.diagnostic_intervention_required is True
    assert (
        updated.repair_cap_reason == "diagnostic repair exhausted; operator intervention required"
    )


def test_promote_to_integrated_requires_integrating_phase() -> None:
    """``PROMOTE_TO_INTEGRATED`` rejects records whose phase is not ``integrating``."""
    from ralph.pipeline.auto_integrate_record import PROMOTE_TO_INTEGRATED

    integrated = _record().model_copy(
        update={"phase": "integrated", "integrated_feature_sha": "abc"}
    )
    with pytest.raises(ValueError, match="is not legal from phase"):
        integrated.with_transition(
            PROMOTE_TO_INTEGRATED,
            phase="integrated",
            integrated_feature_sha="def",
        )


def test_legacy_record_loads_with_defaulted_intervention_field() -> None:
    """A legacy on-disk record without the new fields loads with defaults."""
    from ralph.pipeline.auto_integrate_record import (
        IntegrationRecord,
        parse_record_payload,
    )

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (("work", "old-key"),),
    }
    parsed = parse_record_payload(legacy)
    assert isinstance(parsed, IntegrationRecord)
    assert parsed.diagnostic_intervention_required is False
    assert parsed.repair_cap_reason is None
    # The legacy ``work`` key was migrated to ``unstaged_work``.
    assert dict(parsed.diagnostic_evidence).get("unstaged_work") == "old-key"


def test_legacy_record_with_for_each_ref_surfaces_intervention() -> None:
    """A legacy record carrying ``for-each-ref`` is intervention-required."""
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (("for-each-ref", "deadbeef"),),
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    assert parsed.diagnostic_intervention_required is True
    assert "legacy repository-global evidence" in (parsed.repair_cap_reason or "")


def test_legacy_record_with_branch_reflog_drops_observation() -> None:
    """The branch reflog is common-dir; the migrator drops it with a reason marker."""
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (
            ("work", "old-key"),
            ("/tmp/repo/.git/logs/refs/heads/main", "somehash"),
        ),
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    keys = {k for k, _ in parsed.diagnostic_evidence}
    assert "/tmp/repo/.git/logs/refs/heads/main" not in keys
    assert "unstaged_work" in keys
    assert "legacy common-dir reflog observation dropped" in (parsed.repair_cap_reason or "")


def test_legacy_record_with_merge_state_path_key_migrated() -> None:
    """A legacy ``str(absolute_path)`` for a merge state file is re-categorised."""
    from ralph.pipeline.auto_integrate_record import parse_record_payload

    legacy = {
        "phase": "integrating",
        "target": "main",
        "pre_feature_sha": "a" * 40,
        "pre_target_sha": "b" * 40,
        "diagnostic_evidence": (
            ("/tmp/repo/.git/worktrees/wt/MERGE_HEAD", "abc"),
            ("/tmp/repo/.git/worktrees/wt/rebase-merge/git-rebase-todo", "def"),
        ),
    }
    parsed = parse_record_payload(legacy)
    assert parsed is not None
    keys = {k for k, _ in parsed.diagnostic_evidence}
    assert "merge_state_files:/tmp/repo/.git/worktrees/wt/MERGE_HEAD" in keys
    assert "rebase_state_files:/tmp/repo/.git/worktrees/wt/rebase-merge/git-rebase-todo" in keys


def test_transition_rejects_unknown_name() -> None:
    """``transition_by_name`` raises on an unknown name."""
    from ralph.pipeline.auto_integrate_record import transition_by_name

    with pytest.raises(ValueError, match="unknown transition"):
        transition_by_name("BOGUS")
