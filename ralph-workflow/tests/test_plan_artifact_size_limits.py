"""Raw-byte plan-size boundary tests."""

from __future__ import annotations

import pytest

from ralph.mcp.artifacts.markdown.specs.plan import analyze_plan_document
from ralph.mcp.artifacts.plan import PlanSizeLimits, check_plan_size


def test_raw_utf8_size_boundary_accepts_limit_and_rejects_one_byte_over() -> None:
    limit = PlanSizeLimits.DEFAULT.max_total_bytes
    assert check_plan_size({"_raw_bytes": limit}) is None
    failure = check_plan_size({"_raw_bytes": limit + 1})
    assert failure is not None
    assert (failure.field, failure.actual, failure.cap) == ("total_bytes", limit + 1, limit)


def test_shape_and_list_counts_do_not_constrain_plan_size() -> None:
    assert check_plan_size({"steps": [{}] * 10_000, "work_units": [{}] * 10_000}) is None


def test_non_raw_payload_has_no_serialization_based_size_check() -> None:
    assert check_plan_size({"prose": "é" * 4_000_000}) is None


# The public-acceptance plan-size gate does a 4 MB text scan through
# ``analyze_plan_document``. Standalone it completes in ~0.27 s, but
# under xdist load (multiple pytest workers contending for the GIL +
# fork+re-exec startup overlap) the same 4 MB scan spends >1.0 s and
# the per-test ``ITIMER_REAL`` guard (default
# ``DEFAULT_TEST_TIMEOUT_SECONDS``) raises ``TestExecutionTimeoutError``.
# The ``timeout_seconds`` marker is the documented per-test override
# (``pytest.ini``: "override the per-test timeout for slower
# integration cases") and is the single explicit escape hatch that
# does NOT touch the immutable ``_INTEGRATION_PER_TEST_TIMEOUT_SECONDS``
# / ``_TOTAL_TEST_BUDGET_SECONDS`` constants guarded by the
# ``tests/test_verify_invariants.py`` import-time checks. ``30`` is
# generous: even the worst observed load-sensitivity measurement
# (~5 s) is well under the cap.
@pytest.mark.timeout_seconds(30)
def test_public_acceptance_measures_raw_utf8_bytes_at_limit() -> None:
    prefix = "one two three four five six seven eight nine ten "
    text = prefix + "x" * (4_000_000 - len(prefix))
    _, diagnostics, _ = analyze_plan_document(text)
    assert diagnostics == []
    _, diagnostics, _ = analyze_plan_document(text + "é")
    assert [item.rule_id for item in diagnostics] == ["SPEC010"]
