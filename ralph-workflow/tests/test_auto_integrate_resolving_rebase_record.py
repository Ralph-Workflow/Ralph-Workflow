from __future__ import annotations

from pathlib import Path

from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    record_path,
    set_resolving_rebase,
    write_record,
)

_TARGET = "main"


def _integrating_record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target=_TARGET,
        pre_feature_sha="f" * 40,
        pre_target_sha="a" * 40,
    )


def test_set_resolving_rebase_persists_the_public_record_flag(tmp_path: Path) -> None:
    write_record(tmp_path, _integrating_record())

    assert set_resolving_rebase(tmp_path, True) is True

    persisted = read_record(tmp_path)
    assert persisted is not None
    assert persisted.resolving_rebase is True


def test_set_resolving_rebase_is_a_successful_noop_without_a_record(tmp_path: Path) -> None:
    assert set_resolving_rebase(tmp_path, True) is True
    assert read_record(tmp_path) is None


def test_unparseable_record_is_preserved_without_authorizing_resolution(tmp_path: Path) -> None:
    for content in ("{not json at all", '{"phase": "a-phase-from-the-future"}'):
        path = record_path(tmp_path)
        path.parent.mkdir(exist_ok=True)
        path.write_text(content, encoding="utf-8")

        assert set_resolving_rebase(tmp_path, True) is False
        assert path.read_text(encoding="utf-8") == content
