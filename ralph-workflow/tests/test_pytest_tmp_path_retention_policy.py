"""Pins the ``tmp_path`` retention policy that keeps ``make test`` in budget.

pytest's defaults keep the ``tmp_path`` tree of EVERY passing test for three
sessions. With ~12k tests this suite leaves roughly 150 MB behind per session,
so a machine that runs ``make test`` repeatedly accumulates gigabytes under
``$TMPDIR/pytest-of-<user>/``. The bill arrives at the START of a later
session: ``make_numbered_dir_with_cleanup`` rmtree's the expired session
directories before the first test runs, and that unbounded rmtree is charged
to the IMMUTABLE 60 s combined test budget in
``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``.

That carry-over was measured turning a 37 s ``make test`` into one killed at
60 s -- a failure caused by nothing in the repository and fixable by nothing in
a test. Declaring the retention policy is the fix, and this test is what stops
it being dropped again.

``failed`` keeps the temp tree of every FAILING test, so no debugging surface is
lost; only passing tests' trees are reclaimed at teardown, while still in page
cache. Nothing here relaxes a timeout, a budget, a worker count or an exclusion.
"""

from __future__ import annotations

import configparser
from pathlib import Path

_PYTEST_INI = Path(__file__).resolve().parents[1] / "pytest.ini"


def _ini_section() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    parser.read(_PYTEST_INI, encoding="utf-8")
    return parser["pytest"]


def test_passing_tests_do_not_retain_their_temp_trees() -> None:
    """``all`` is what let gigabytes accumulate; ``failed`` is the contract."""
    assert _ini_section()["tmp_path_retention_policy"] == "failed"


def test_only_the_current_session_directory_is_retained() -> None:
    """Three retained sessions is three sessions' worth of startup rmtree."""
    assert _ini_section()["tmp_path_retention_count"] == "1"


def test_failing_tests_still_keep_their_temp_tree_for_debugging() -> None:
    """The policy must never be tightened to ``none``.

    ``none`` would delete a failing test's ``tmp_path`` too, destroying the
    evidence an operator needs to diagnose it. The budget problem is solved by
    reclaiming PASSING tests' trees; failures keep theirs.
    """
    assert _ini_section()["tmp_path_retention_policy"] != "none"
