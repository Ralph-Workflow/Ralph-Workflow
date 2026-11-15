from __future__ import annotations

from ralph.testing.audit_integration_no_legacy import selftest


def test_audit_integration_no_legacy_selftest() -> None:
    assert selftest() == 0
