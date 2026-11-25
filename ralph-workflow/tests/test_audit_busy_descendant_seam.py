from __future__ import annotations

from pathlib import Path

import pytest

from ralph.testing import audit_activity_aware_watchdog as audit

_READERS = ("_process_reader.py", "_pty_line_reader.py")

_WIRED_CLASSIFY = (
    "    def _classify_quiet(self):\n"
    "        reported = self._strategy.classify_quiet(self._handle, self._probe)\n"
    "        return quiet_state_with_busy_descendants(reported, self._descendants_busy)\n"
)
_UNWIRED_CLASSIFY = (
    "    def _classify_quiet(self):\n"
    "        return self._strategy.classify_quiet(self._handle, self._probe)\n"
)
_WIRED_CORROBORATE = (
    "    def _corroborate(self):\n"
    "        alive_by = alive_by_with_busy_descendants(None, self._descendants_busy)\n"
    "        return alive_by\n"
)
_UNWIRED_CORROBORATE = (
    "    def _corroborate(self):\n        alive_by = None\n        return alive_by\n"
)


def _audit(tmp_path: Path, reader: str, body: str) -> set[str]:
    package_root = tmp_path / "fake_ralph"
    (package_root / "agents" / "invoke").mkdir(parents=True)
    (package_root / "agents" / "invoke" / reader).write_text(
        "class Reader:\n" + body, encoding="utf-8"
    )
    return {v.category for v in audit.audit_activity_aware_watchdog(package_root)}


@pytest.mark.parametrize("reader", _READERS)
def test_reader_wired_to_busy_descendants_passes(tmp_path: Path, reader: str) -> None:
    kinds = _audit(tmp_path, reader, _WIRED_CLASSIFY + _WIRED_CORROBORATE)
    assert "busy_descendant_seam" not in kinds


@pytest.mark.parametrize("reader", _READERS)
@pytest.mark.parametrize(
    "body",
    [_UNWIRED_CLASSIFY + _WIRED_CORROBORATE, _WIRED_CLASSIFY + _UNWIRED_CORROBORATE],
)
def test_reader_that_drops_busy_descendant_evidence_is_flagged(
    tmp_path: Path, reader: str, body: str
) -> None:
    assert "busy_descendant_seam" in _audit(tmp_path, reader, body)


def test_other_invoke_modules_are_not_held_to_the_reader_seam(tmp_path: Path) -> None:
    kinds = _audit(tmp_path, "_completion_helpers.py", _UNWIRED_CLASSIFY + _UNWIRED_CORROBORATE)
    assert "busy_descendant_seam" not in kinds


def test_production_readers_route_quiet_and_corroboration_through_busy_descendants() -> None:
    package_root = Path(audit.__file__).resolve().parents[1]
    violations = audit.audit_activity_aware_watchdog(package_root)
    assert [v for v in violations if v.category == "busy_descendant_seam"] == []
