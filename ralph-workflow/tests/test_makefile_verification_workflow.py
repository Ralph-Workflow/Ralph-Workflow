from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = REPO_ROOT / "Makefile"
UNIT_TEST_SHARD_COUNT = 49


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


def _assert_any_line_contains(body: list[str], needle: str) -> None:
    assert any(needle in line for line in body)


def _assert_all_lines_contain(body: list[str], needles: list[str]) -> None:
    assert all(all(needle in line for needle in needles) for line in body)


def test_verify_target_delegates_to_wrapper_module() -> None:
    verify_body = _target_body("verify")

    assert verify_body == ["@uv run python -m ralph.verify"]


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
    assert '"not subprocess_e2e"' in test_cov_body[1]
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

    expected_unit_markers = [
        "$(PYTEST_CORE_PATHS)",
        "$(PYTEST_RUNTIME_PATHS_MCP)",
        "$(PYTEST_RUNTIME_PATHS_PIPELINE)",
        "$(PYTEST_RUNTIME_PATHS_RECOVERY)",
        "$(PYTEST_ROOT_PATHS_A_ACTIVITY)",
        "$(PYTEST_ROOT_PATHS_A_AGENT)",
        "$(PYTEST_ROOT_PATHS_A_AGENTS)",
        "$(PYTEST_ROOT_PATHS_A_AGY)",
        "$(PYTEST_ROOT_PATHS_A_ANALYSIS)",
        "$(PYTEST_ROOT_PATHS_A_API)",
        "$(PYTEST_ROOT_PATHS_A_ARTIFACT)",
        "$(PYTEST_ROOT_PATHS_A_ASYNCIO)",
        "$(PYTEST_ROOT_PATHS_A_AUDIT)",
        "$(PYTEST_ROOT_PATHS_B)",
        "$(PYTEST_ROOT_PATHS_C_CAPABILITY)",
        "$(PYTEST_ROOT_PATHS_C_CHECKPOINT)",
        "$(PYTEST_ROOT_PATHS_C_CHILD_CLASSIFIER)",
        "$(PYTEST_ROOT_PATHS_C_CLAUDE)",
        "$(PYTEST_ROOT_PATHS_C_CLI_1)",
        "$(PYTEST_ROOT_PATHS_C_CLI_2)",
        "$(PYTEST_ROOT_PATHS_C_CODEX_COMMIT)",
        "$(PYTEST_ROOT_PATHS_C_COMPLETION)",
        "$(PYTEST_ROOT_PATHS_C_CONFIG)",
        "$(PYTEST_ROOT_PATHS_C_CUSTOM_POLICY)",
        "$(PYTEST_ROOT_PATHS_C_CYCLE)",
        "$(PYTEST_ROOT_PATHS_D)",
        "$(PYTEST_ROOT_PATHS_E)",
        "$(PYTEST_ROOT_PATHS_F)",
        "$(PYTEST_ROOT_PATHS_G)",
        "$(PYTEST_ROOT_PATHS_H)",
        "$(PYTEST_ROOT_PATHS_I)",
        "$(PYTEST_ROOT_PATHS_L)",
        "$(PYTEST_ROOT_PATHS_M_CONFIG)",
        "$(PYTEST_ROOT_PATHS_M_ARTIFACTS)",
        "$(PYTEST_ROOT_PATHS_M_BRIDGE)",
        "$(PYTEST_ROOT_PATHS_M_CAPABILITY)",
        "$(PYTEST_ROOT_PATHS_M_CORE)",
        "$(PYTEST_ROOT_PATHS_M_RUNTIME)",
        "$(PYTEST_ROOT_PATHS_M_SERVER)",
        "$(PYTEST_ROOT_PATHS_M_TOOL)",
        "$(PYTEST_ROOT_PATHS_N)",
        "$(PYTEST_ROOT_PATHS_O)",
        "$(PYTEST_ROOT_PATHS_PA_PC)",
        "$(PYTEST_ROOT_PATHS_PD_PF)",
        "$(PYTEST_ROOT_PATHS_PG_PI)",
        "$(PYTEST_ROOT_PATHS_PJ_PL)",
        "$(PYTEST_ROOT_PATHS_PM_PZ)",
        "$(PYTEST_ROOT_PATHS_Q_S)",
        "$(PYTEST_ROOT_PATHS_T_Z)",
    ]

    assert len(unit_body) == UNIT_TEST_SHARD_COUNT
    assert len(integration_body) == 1
    _assert_all_lines_contain(
        unit_body,
        ["python -m ralph.verify_timeout", "--suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS)"],
    )
    assert "uv run python -m ralph.verify_timeout" in integration_body[0]
    assert "--suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS)" in integration_body[0]

    for marker in expected_unit_markers:
        _assert_any_line_contains(unit_body, marker)

    assert "python -m pytest tests/integration/ -q" in integration_body[0]


def test_test_subprocess_e2e_uses_same_timeout_wrapper() -> None:
    e2e_body = _target_body("test-subprocess-e2e")

    assert e2e_body == [
        "uv run python -m ralph.verify_timeout "
        "--suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- "
        "python -m pytest tests/ -q -n 1 -m subprocess_e2e"
    ]


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
