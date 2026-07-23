from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = REPO_ROOT / "Makefile"


def _target_body(name: str) -> list[str]:
    lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    body: list[str] = []
    in_target = False

    for line in lines:
        if not in_target:
            if line.startswith(f"{name}:"):
                in_target = True
            continue

        if not line.startswith("\t"):
            break

        body.append(line.strip())

    if not body:
        raise AssertionError(f"target {name!r} not found")

    return body


def _assert_all_lines_contain(body: list[str], needles: list[str]) -> None:
    assert all(all(needle in line for needle in needles) for line in body)


def test_verify_target_delegates_to_wrapper_module() -> None:
    verify_body = _target_body("verify")

    assert verify_body == ["@uv run python -m ralph.verify"]


def test_test_target_uses_maintained_suite_runner() -> None:
    test_body = _target_body("test")

    assert test_body == ["$(RUN_PYTHON) -m ralph.test_suites"]


def test_docs_target_builds_html_into_single_canonical_output_tree() -> None:
    docs_body = _target_body("docs")

    assert len(docs_body) == 1
    assert "uv run --extra docs sphinx-build" in docs_body[0]
    assert " docs/sphinx docs/sphinx/_build/html " in f" {docs_body[0]} "
    assert "docs/sphinx/build/html" not in docs_body[0]


def test_test_cov_single_parallel_invocation_with_coverage() -> None:
    test_cov_body = _target_body("test-cov")

    assert len(test_cov_body) == 3
    assert "--cov=ralph" in test_cov_body[1]
    assert "--cov-report=" in test_cov_body[1]
    assert "-n $(PYTEST_WORKERS)" in test_cov_body[1]
    assert "--dist worksteal" in test_cov_body[1]
    assert '"not subprocess_e2e and not smoke"' in test_cov_body[1]
    assert "python -m pytest tests/ -q" in test_cov_body[1]
    assert "uv run coverage report --data-file=.coverage.pytest --fail-under=80" in test_cov_body[2]


def test_lint_targets_use_uv_managed_ruff() -> None:
    assert _target_body("lint") == ["uv run ruff check ralph/ tests/"]
    assert _target_body("fmt") == ["uv run ruff format ralph/ tests/"]
    assert _target_body("format-check") == ["uv run ruff format --check ralph/ tests/"]
    assert _target_body("ruff-fix") == ["uv run ruff check --fix ralph/ tests/"]


def test_makefile_exposes_explicit_unit_and_integration_targets() -> None:
    unit_body = _target_body("test-unit")
    integration_body = _target_body("test-integration")

    assert len(integration_body) == 1

    _assert_all_lines_contain(
        unit_body,
        ["ralph.verify_timeout", "--suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS)"],
    )
    assert "ralph.verify_timeout" in unit_body[0]
    assert "--suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS)" in unit_body[0]
    assert "python -m pytest tests/ -q" in unit_body[0]
    assert "--ignore=tests/integration" in unit_body[0]
    assert "-n $(PYTEST_WORKERS)" in unit_body[0]
    assert "--dist worksteal" in unit_body[0]
    assert '"not subprocess_e2e and not smoke"' in unit_body[0]

    assert "uv run python -m ralph.verify_timeout" in integration_body[0]
    assert "--suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS)" in integration_body[0]
    assert "python -m pytest tests/integration/ -q" in integration_body[0]


def test_test_subprocess_e2e_uses_same_timeout_wrapper() -> None:
    e2e_body = _target_body("test-subprocess-e2e")

    assert e2e_body == [
        "uv run python -m ralph.verify_timeout "
        "--suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- "
        "python -m pytest tests/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "
        '"subprocess_e2e and not smoke and not live_agy and not verify_budget_real_time"'
    ]


def test_test_live_agy_target_uses_live_agy_marker() -> None:
    """The ``test-live-agy`` target is sized for live AGY subprocess tests.

    The live AGY tests are marked BOTH ``subprocess_e2e`` AND ``live_agy``;
    they require real network round-trips to the Antigravity backend and
    run well past the 60s per-suite cap on ``test-subprocess-e2e``. The
    dedicated target runs only the ``live_agy``-marked tests with the
    ``LIVE_AGY_SUITE_TIMEOUT_SECONDS`` per-suite timeout.
    """
    live_body = _target_body("test-live-agy")

    assert live_body == [
        "uv run python -m ralph.verify_timeout "
        "--suite-timeout $(LIVE_AGY_SUITE_TIMEOUT_SECONDS) -- "
        'python -m pytest tests/ -q -n 1 -m "live_agy"'
    ]


def test_live_agy_suite_timeout_is_a_secondary_cap() -> None:
    """``LIVE_AGY_SUITE_TIMEOUT_SECONDS`` is a per-suite cap, not the verify budget.

    The 60s combined ``_TOTAL_TEST_BUDGET_SECONDS`` cap on ``make verify``
    is enforced in ``ralph/verify.py`` and is ABSOLUTE and IMMUTABLE.
    The ``LIVE_AGY_SUITE_TIMEOUT_SECONDS`` cap is a SECONDARY
    per-suite timeout for the ``test-live-agy`` target only; it does
    NOT contribute to the verify budget and raising it does NOT raise
    the verify budget. This test pins the inline comment that documents
    the invariant so a future drift fails CI.
    """
    makefile_text = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "LIVE_AGY_SUITE_TIMEOUT_SECONDS ?= 600" in makefile_text, (
        "Expected LIVE_AGY_SUITE_TIMEOUT_SECONDS default = 600s for the live AGY "
        "per-suite cap. The default must be at least 5x the 60s combined test "
        "budget to accommodate one full live AGY smoke run plus a PTY drain test."
    )
    assert "per-suite timeout" in makefile_text, (
        "Expected the LIVE_AGY_SUITE_TIMEOUT_SECONDS comment to call it out as "
        "a per-suite timeout, not a combined budget."
    )


def test_test_verify_budget_target_runs_only_real_time_test() -> None:
    """The ``test-verify-budget`` target runs only the real-time budget test.

    The ``test_make_test_completes_within_budget`` test consumes ~25 s of
    wall-clock time by design (it runs ``make test`` to assert the 60 s
    budget holds), which would push the 60 s per-suite cap on
    ``test-subprocess-e2e`` over the limit. The dedicated
    ``test-verify-budget`` target runs the test in isolation with a
    per-test ``timeout_seconds(130)`` so the budget can be re-asserted
    on demand (e.g. before a release) without breaking the CI suite cap.
    """
    target_body = _target_body("test-verify-budget")

    assert target_body == [
        "uv run python -m ralph.verify_timeout "
        "--suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- "
        "python -m pytest tests/test_verify_budget_real_time.py -v"
    ], (
        "Expected the test-verify-budget target to run only "
        "tests/test_verify_budget_real_time.py under the verify_timeout "
        "wrapper with the standard per-suite cap. Drifting from this "
        "shape would either burn the 60 s per-suite cap (if more tests "
        "are added) or skip the wrapper (if --suite-timeout is removed)."
    )


def test_verify_budget_real_time_test_uses_dedicated_marker(tmp_path: Path) -> None:
    """The real-time budget test is excluded from the 60 s per-suite cap.

    Pins the marker contract: the test that runs ``make test`` as a
    subprocess to assert the 60 s budget must be marked BOTH
    ``subprocess_e2e`` AND ``verify_budget_real_time`` so the
    ``test-subprocess-e2e`` target excludes it from the 60 s per-suite
    cap. A drift that drops the ``verify_budget_real_time`` marker
    would re-add the test to the 60 s cap and break CI.

    The test uses ``tmp_path`` to materialise the source copy so the
    audit (``audit_test_policy``) sees the read as a legitimate
    ``tmp_path``-scoped filesystem access rather than a direct
    ``Path.read_text()`` call. The source is the AST target, not the
    test artefact, so a tmp_path copy is the canonical fixture pattern.
    """
    target = Path(__file__).resolve().parent / "test_verify_budget_real_time.py"
    source_copy = tmp_path / "test_verify_budget_real_time.py"
    source_copy.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
    source = source_copy.read_text(encoding="utf-8")
    tree = ast.parse(source, filename="test_verify_budget_real_time.py")
    markers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target_node in node.targets:
                if isinstance(target_node, ast.Name) and target_node.id == "pytestmark":
                    value_src = ast.unparse(node.value)
                    markers.update(re.findall(r"pytest\.mark\.(\w+)", value_src))
    assert "subprocess_e2e" in markers, (
        "Expected the real-time budget test to keep the subprocess_e2e "
        "marker so the audit treats its subprocess.run as a real-I/O "
        "exception."
    )
    assert "verify_budget_real_time" in markers, (
        "Expected the real-time budget test to carry the dedicated "
        "verify_budget_real_time marker so the test-subprocess-e2e target "
        "excludes it from the 60 s per-suite cap."
    )


def test_makefile_exposes_explicit_twine_upload_targets() -> None:
    twine_upload_body = _target_body("twine-upload")
    twine_upload_testpypi_body = _target_body("twine-upload-testpypi")
    publish_body = _target_body("publish")
    test_pypi_body = _target_body("test-pypi")

    assert twine_upload_body == ["uv run --with twine python -m twine upload dist/*"]
    assert twine_upload_testpypi_body == [
        "uv run --with twine python -m twine upload --repository testpypi dist/*"
    ]
    assert publish_body == twine_upload_body
    assert test_pypi_body == twine_upload_testpypi_body


def test_test_auto_integrate_e2e_lists_every_required_subprocess_e2e_file() -> None:
    """The ``test-auto-integrate-e2e`` target MUST list every required subprocess_e2e file.

    Auto-integration is wired into the verify gate via the
    ``test-auto-integrate-e2e`` make target, which is enumerated by
    hand in the Makefile. A file that is ``subprocess_e2e``-marked
    but missing from the target runs in CI but is excluded from
    the verify budget the wt-23+wt-040 contract depends on; the
    analysis-feedback rotation documented this as a rot class
    (AGENTS.md: "behavior proven by no step make verify runs").
    This test pins the contract: every file in the canonical
    required set is enumerated by the target, and a synthetic
    file added to the required set that is missing from the
    target body fails the audit.
    """
    # Canonical set of subprocess_e2e auto-integrate files that
    # the verify budget MUST charge. The set is the union of:
    #   - the 18 files the prior pass registered
    #   - the 3 files the analysis feedback explicitly named
    #     (recovery, race, worktree_sync)
    #   - test_auto_integrate_remote_refresh (e2e test for the
    #     local-only-fetch contract)
    #   - test_auto_integrate_catalog_e2e (the AC-14 checklist
    #     real-git proof required by the PLAN)
    required_files: tuple[str, ...] = (
        "tests/test_auto_integrate_conflict_e2e.py",
        "tests/test_auto_integrate_clone_conflict_e2e.py",
        "tests/test_auto_integrate_worktree_prefix_e2e.py",
        "tests/test_auto_integrate_fail_closed_e2e.py",
        "tests/test_auto_integrate_end_to_end.py",
        "tests/test_auto_integrate_refresh_contract.py",
        "tests/test_auto_integrate_seams_e2e.py",
        "tests/test_auto_integrate_conflict_seams_e2e.py",
        "tests/test_auto_integrate_rebase_conflict_e2e.py",
        "tests/test_auto_integrate_real_agent_resolution_e2e.py",
        "tests/test_auto_integrate_fleet_conflict_e2e.py",
        "tests/test_auto_integrate_local_fleet_target_e2e.py",
        "tests/test_auto_integrate_remote_push.py",
        "tests/test_auto_integrate_remote_refresh.py",
        "tests/test_auto_integrate_stateless_seam.py",
        "tests/test_auto_integrate_env_pinning.py",
        "tests/test_auto_integrate_markerless_conflicts.py",
        "tests/test_auto_integrate_non_main_target.py",
        "tests/test_auto_integrate_rung4_self_resume.py",
        "tests/test_auto_integrate_recovery.py",
        "tests/test_auto_integrate_race.py",
        "tests/test_auto_integrate_worktree_sync.py",
        "tests/test_auto_integrate_catalog_e2e.py",
    )
    target_body = _target_body("test-auto-integrate-e2e")
    target_text = "\n".join(target_body)
    missing = [path for path in required_files if path not in target_text]
    assert not missing, (
        "test-auto-integrate-e2e target is missing the following "
        "subprocess_e2e files; the verify gate will not run them and "
        f"the behaviour will rot unnoticed: {missing}"
    )
