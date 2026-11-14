from __future__ import annotations

import json
from pathlib import Path

import pytest
from loguru import logger

from ralph.pipeline.auto_integrate_record import (
    INTEGRATION_RECORD_VERSION,
    IntegrationRecord,
    RecordLoadDisposition,
    has_usable_record,
    load_record,
    record_path,
)


def _record() -> IntegrationRecord:
    return IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="a" * 40,
        pre_target_sha="b" * 40,
    )


def _fixture_payload(kind: str) -> str | None:
    payload = _record().model_dump(mode="json")
    if kind == "old_version":
        payload["record_version"] = 0
    elif kind == "newer_version":
        payload["record_version"] = INTEGRATION_RECORD_VERSION + 1
    elif kind == "truncated_json":
        return '{"record_version":'
    elif kind == "invalid_field":
        payload["phase"] = "hand-edited"
    elif kind == "missing_file":
        return None
    elif kind == "corrupt_file":
        return "not-json"
    else:
        raise AssertionError(kind)
    return json.dumps(payload)


@pytest.mark.parametrize(
    ("kind", "rebuildable", "expected"),
    [
        (kind, rebuildable, expected)
        for kind in (
            "old_version",
            "newer_version",
            "truncated_json",
            "invalid_field",
            "missing_file",
            "corrupt_file",
        )
        for rebuildable, expected in (
            (True, RecordLoadDisposition.MIGRATED)
            if kind == "old_version"
            else (True, RecordLoadDisposition.REBUILT),
            (False, RecordLoadDisposition.MIGRATED)
            if kind == "old_version"
            else (False, RecordLoadDisposition.DISCARDED),
        )
    ],
)
def test_record_load_self_heals_with_one_notice(
    tmp_path: Path,
    kind: str,
    rebuildable: bool,
    expected: RecordLoadDisposition,
) -> None:
    payload = _fixture_payload(kind)
    path = record_path(tmp_path)
    if payload is not None:
        path.parent.mkdir(parents=True)
        path.write_text(payload, encoding="utf-8")
    notices: list[str] = []
    sink = logger.add(lambda message: notices.append(str(message)), level="WARNING")
    try:
        result = load_record(
            tmp_path,
            rebuild_from_git=lambda _root: _record() if rebuildable else None,
        )
    finally:
        logger.remove(sink)

    assert result.disposition is expected
    assert len(notices) == 1
    assert (result.record is not None) is (expected is not RecordLoadDisposition.DISCARDED)


def test_corrupt_record_does_not_block_mechanical_finish_facts(tmp_path: Path) -> None:
    path = record_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{", encoding="utf-8")
    observed_roots: list[Path] = []

    def observe_git_facts(root: Path) -> IntegrationRecord | None:
        observed_roots.append(root)
        return None

    result = load_record(tmp_path, rebuild_from_git=observe_git_facts)

    assert not has_usable_record(result)
    assert result.disposition is RecordLoadDisposition.DISCARDED
    assert observed_roots == [tmp_path]
