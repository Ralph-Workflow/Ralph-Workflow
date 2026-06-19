.PHONY: help install stable dev install-dev test test-unit test-integration test-subprocess-e2e lint typecheck build build-wheel build-sdist publish test-pypi test-pypi-upload twine-upload twine-upload-testpypi clean dist clean-dist fmt format-check ruff-fix mypy-fix pre-commit dist-homebrew verify coverage dist-binary dist-pypi dist-all formula-check dead-code docs docs-linkcheck serve-docs packaging-smoke

# pytest-xdist worker count for parallel test execution.
#
# Default: ``2`` — pinned to a count that keeps the full suite under the
# 60s combined budget on the current development environment without
# starving individual tests past the 1s per-test limit. On systems with
# 12+ fast cores, ``auto`` may be faster; override as needed:
#   PYTEST_WORKERS=auto make test
#   PYTEST_WORKERS=8 make test
#
# The 60s combined test budget is ABSOLUTE and IMMUTABLE — enforced
# by ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS. This per-suite timeout
# is a SECONDARY cap only.
PYTEST_WORKERS ?= 2
export PYTEST_WORKERS
export PYTHONPATH := $(shell realpath .)
# NOTE: PYTEST_SUITE_TIMEOUT_SECONDS is a PER-SUITE timeout only.
# The combined total test budget (60 s, ABSOLUTE and IMMUTABLE) for
# `make verify` is enforced in ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS,
# not here. Do NOT raise this to work around slow tests — fix the test
# design instead.
PYTEST_SUITE_TIMEOUT_SECONDS ?= 60
# LIVE_AGY_SUITE_TIMEOUT_SECONDS is the per-suite timeout for the
# `test-live-agy` target. Live AGY invocations require real network
# round-trips to the Antigravity backend and run well past the 60s
# per-suite cap that gates `test-subprocess-e2e`. The live tests
# are marked `live_agy` and run under this target only.
#
# This is a PER-SUITE timeout only; the 60s combined
# `_TOTAL_TEST_BUDGET_SECONDS` cap on `make verify` does NOT include
# this target. Raising it does NOT raise the verify budget.
LIVE_AGY_SUITE_TIMEOUT_SECONDS ?= 600
RUN_PYTHON = $(if $(VIRTUAL_ENV),python,uv run python)
help:
	@echo "Ralph Workflow - Makefile commands"
	@echo ""
	@echo "  install         Set up the dev build + install the 'rdev' launcher (~/.local/bin/rdev)"
	@echo "  stable          Install the pinned stable build as the global 'ralph' via uv tool"
	@echo "  dev             Sync the dev environment only (no 'rdev' launcher)"
	@echo "  install-dev     Alias for dev"
	@echo "  test           Run the full pytest suite without coverage (excludes subprocess_e2e)"
	@echo "  test-unit      Run unit/root tests without tests/integration/"
	@echo "  test-integration Run tests/integration/ only (excludes subprocess_e2e)"
	@echo "  test-subprocess-e2e Run tests that require real subprocesses or network sockets (60s limit)"
	@echo "  test-live-agy    Run live AGY subprocess tests (excluded from test-subprocess-e2e; longer timeout)"
	@echo "  lint           Run ruff linting"
	@echo "  typecheck      Run mypy type checking"
	@echo "  fmt            Format code with ruff"
	@echo "  format-check   Check formatting without applying changes"
	@echo "  pre-commit     Run pre-commit hooks"
	@echo "  build          Build wheel and sdist"
	@echo "  build-wheel    Build wheel only"
	@echo "  build-sdist    Build sdist only"
	@echo "  twine-upload   Upload dist/* to PyPI via Twine"
	@echo "  twine-upload-testpypi Upload dist/* to Test PyPI via Twine"
	@echo "  publish        Alias for twine-upload"
	@echo "  test-pypi      Alias for twine-upload-testpypi"
	@echo "  test-pypi-upload Alias for twine-upload-testpypi"
	@echo "  clean          Clean build artifacts"
	@echo "  clean-dist     Clean dist folder"
	@echo "  dist-homebrew  Build Homebrew distribution"
	@echo "  verify         Run full verification (ruff, mypy, pytest; excludes docs build and coverage)"
	@echo "  dist-binary    Build PyInstaller single-file binary"
	@echo "  dist-pypi      Build and publish to PyPI via hatch"
	@echo "  dist-all       Build both binary and PyPI distributions"
	@echo "  formula-check  Validate Homebrew formula syntax"
	@echo "  dead-code      Run Vulture dead-code audit (fails while backlog exists)"
	@echo "  docs           Build Sphinx HTML documentation"
	@echo "  docs-linkcheck Check all hyperlinks in docs (opt-in, not in verify)"
	@echo "  serve-docs     Build and serve docs at http://localhost:8080"
	@echo ""
	@echo "  run            Run ralph CLI directly"
	@echo "  check          Run all checks (lint, typecheck, test)"

# Installation
# install  -> dev build: sync uv env + write `rdev` launcher (run the dev build with `rdev`)
# stable   -> pinned release as the isolated global `ralph` command via `uv tool`
install:
	$(RUN_PYTHON) -m ralph.install

stable:
	$(RUN_PYTHON) -m ralph.install --stable

dev:
	uv sync --extra dev

install-dev: dev

# Testing
test:
	$(RUN_PYTHON) -m ralph.test_suites

test-unit:
	$(RUN_PYTHON) -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/ -q -n $(PYTEST_WORKERS) --dist worksteal --ignore=tests/integration -m "not subprocess_e2e and not smoke"

test-integration:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/integration/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e and not smoke"

test-subprocess-e2e:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/ -q -n 1 -m "subprocess_e2e and not smoke and not live_agy and not verify_budget_real_time"

# Live AGY tests are excluded from `test-subprocess-e2e` because they
# require real network round-trips to the Antigravity backend and run
# well past the 60s suite cap. The live tests are marked BOTH
# `subprocess_e2e` AND `live_agy`; this target collects only the
# `live_agy`-marked tests and gives them a sized per-suite timeout.
#
# The marker exclusion for `test-subprocess-e2e` is enforced by
# `tests/test_makefile_verification_workflow.py::test_test_subprocess_e2e_uses_same_timeout_wrapper`
# so any drift fails CI.
test-live-agy:
	uv run python -m ralph.verify_timeout --suite-timeout $(LIVE_AGY_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/ -q -n 1 -m "live_agy"

# Real-runtime budget test that runs ``make test`` to assert the 60 s combined
# test budget. This test consumes ~25 s of wall-clock time by design (it
# runs the full pytest suite as a subprocess to assert the budget holds),
# so it is excluded from ``test-subprocess-e2e`` and the 60 s per-suite cap
# via the ``verify_budget_real_time`` marker. Run on demand before/after a
# release to confirm the 60 s budget still holds; the test budget is
# otherwise pinned by the import-time invariants in ``ralph/verify.py``
# and the per-test/per-suite invariants in ``tests/test_verify_invariants.py``.
test-verify-budget:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/test_verify_budget_real_time.py -v

coverage: test-cov

test-cov:
	rm -f .coverage.pytest .coverage.pytest.*
	uv run python -m pytest tests/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e and not smoke" --cov=ralph --cov-report=
	uv run coverage report --data-file=.coverage.pytest --fail-under=80

# Linting
lint:
	uv run ruff check ralph/ tests/

typecheck:
	uv run python -m mypy ralph/

fmt:
	uv run ruff format ralph/ tests/

format-check:
	uv run ruff format --check ralph/ tests/

ruff-fix:
	uv run ruff check --fix ralph/ tests/

mypy-fix:
	uv run python -m mypy ralph/

# Pre-commit
pre-commit:
	pre-commit run --all-files

# Building
build: clean-dist
	uv run --with hatchling hatch build

build-wheel: clean-dist
	uv run --with hatchling hatch build --wheel

build-sdist: clean-dist
	uv run --with hatchling hatch build --sdist

# Publishing
twine-upload: build
	uv run --with twine python -m twine upload dist/*

twine-upload-testpypi: build
	uv run --with twine python -m twine upload --repository testpypi dist/*

publish: build
	uv run --with twine python -m twine upload dist/*

test-pypi: build
	uv run --with twine python -m twine upload --repository testpypi dist/*

test-pypi-upload: build
	uv run --with twine python -m twine upload --repository testpypi dist/*

# Cleaning
clean:
	rm -rf build/
	rm -rf .pytest_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	rm -rf .mypy_cache/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

clean-dist:
	rm -rf dist/
	rm -rf build/
	rm -rf *.egg-info/

# Homebrew
dist-homebrew: build-wheel
	@mkdir -p Formula
	@echo "Building Homebrew bottle..."
	@python -m installer --print-blob --no-scrollbar ralph_python*.whl 2>/dev/null || \
		echo "Note: Homebrew bottle creation requires macOS and Homebrew environment"

# Distribution targets
# Combined 60s test budget enforced by ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS (IMMUTABLE).
verify: verify-drift
	@uv run python -m ralph.verify

# Drift detection: prevents the Pro contract invariants from regressing.
#
# The three greps below fail CI when the engine regresses:
#   1. No hardcoded source-prompt construction outside the resolver —
#      scans ralph/ for PROMPT.md literals except in ralph/pro_support/prompt.py,
#      the engine-owned ralph/prompts/ package (which writes the
#      materialised CURRENT_PROMPT.md), and documentation.
#   2. No marker references outside the marker reader — scans ralph/ for
#      .ralph/run.json except in ralph/pro_support/marker.py.
#   3. (Verified by audit_mcp_timeout, not grepped here.)
#
# Failures of any of these greps indicate a drift away from the Pro
# contract; investigate before allowing the test pipeline to continue.
verify-drift:
	@bash -c 'set -e; \
	  if grep -RIn --include="*.py" ralph/ \
	    -e "PROMPT.md" -e "Path(\"PROMPT.md\")" \
	    --exclude-dir=__pycache__ --exclude-dir=.venv \
	    | grep -v "ralph/pro_support/prompt.py" \
	    | grep -v "ralph/pro_support/env.py" \
	    | grep -v "ralph/pro_support/__init__.py" \
	    | grep -v "ralph/prompts/" \
	    | grep -v "tests/" \
	    | grep -v "\.agent/" \
	    | grep -v "cli/main.py" \
	    | grep -v "cli/commands/init.py" \
	    | grep -v "cli/commands/prompt_helper.py" \
	    | grep -v "cli/commands/prompt_helper_prompt.py" \
	    | grep -v "cli/commands/diagnose.py" \
	    | grep -v "cli/commands/run.py" \
	    | grep -v "cli/commands/smoke.py" \
	    | grep -v "config/welcome.py" \
	    | grep -v "onboarding.py" \
	    | grep -v "files/operations.py" \
	    | grep -v "agents/invoke/__init__.py" \
	    | grep -v "agents/invoke/_commands.py" \
	    | grep -v "agents/invoke/_command_builders/" \
	    | grep -v "agents/invoke/_runtime_resolvers/" \
	    | grep -v "mcp/artifacts/product_spec.py" \
	    | grep -v "mcp/server/_in_memory_transport.py" \
	    | grep -v "mcp/server/_fallback_http_handler.py" \
	    | grep -v "phases/integrity.py" \
	    | grep -v "policy/validation/_api.py" \
	    | grep -v "policy/defaults/" \
	    | grep -v "display/prompt_reader.py" \
	    | grep -v "pipeline/prompt_prep.py" \
	    | grep -v "pipeline/orchestrator.py" \
	    | grep -v "prompts/system_prompt.py" \
	    | grep -v "prompts/materialize.py" \
	    | grep -v "prompts/developer/" \
	    | grep -v "parallel/worker_runtime.py" \
	    | grep -v "_retry_progress_guard.py" \
	    | grep -v "policy/loader.py" \
	    | grep -v "phases/required_artifacts.py" \
	    | grep -v "testing/audit_di_seam.py" \
	    ; then \
	      echo "drift: hardcoded source-prompt construction outside the resolver" >&2; \
	      exit 1; \
	    fi; \
	  if grep -RIn --include="*.py" ralph/ ".ralph/run.json" \
	    | grep -v "ralph/pro_support/marker.py" \
	    | grep -v "ralph/pro_support/watcher.py" \
	    | grep -v "pro_support_inventory.md" \
	    ; then \
	      echo "drift: .ralph/run.json referenced outside ralph/pro_support/marker.py" >&2; \
	      exit 1; \
	    fi; \
	  if grep -RIn --include="*.py" ralph/pro_support/ "time\.sleep" \
	    ; then \
	      echo "drift: ralph/pro_support must not use time.sleep" >&2; \
	      exit 1; \
	    fi; \
	  if grep -RIn --include="*.py" ralph/ "RALPH_" \
	    | grep -v -E "RALPH_(WORKFLOW_PRO|WORKSPACE|PROMPT_PATH)\b" \
	    | grep -v "ralph/pro_support/env.py" \
	    | grep -v "ralph/pro_support/__init__.py" \
	    | grep -v "tests/test_pro_support" \
	    | grep -v "tests/test_audit_mcp_timeout" \
	    | grep -v "tests/test_run_loop_pro_integration" \
	    | grep -v "tests/test_audit_lint_bypass" \
	    | grep -v "tests/test_audit_test_policy" \
	    | grep -v "tests/test_audit_typecheck_bypass" \
	    | grep -v "tests/test_audit_di_seam" \
	    | grep -v "tests/test_pro_support_contract" \
	    | grep -v "tests/test_pro_support_cross_repo_marker" \
	    | grep -v "tests/test_no_dead_code" \
	    | grep -v "tests/test_opencode_mcp_config_drift" \
	    ; then \
	      echo "drift: ralph/ uses a RALPH_* env var outside the canonical three" >&2; \
	      exit 1; \
	    fi; \
	  echo "verify-drift: OK"'

policy-loader-smoke:
	@python -c "import sys; sys.stderr = open('/dev/null', 'w'); from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"

packaging-smoke:
	@mkdir -p $(shell realpath .)/../tmp
	@rm -rf $(shell realpath .)/../tmp/packaging-smoke
	@python -m venv --without-pip $(shell realpath .)/../tmp/packaging-smoke
	@PYTHONPATH="" uv pip install --quiet --python $(shell realpath .)/../tmp/packaging-smoke/bin/python $(shell realpath .)/../ralph-workflow/
	@ls -la $(shell realpath .)/../tmp/packaging-smoke/bin/ralph-prompt
	@PYTHONPATH="" $(shell realpath .)/../tmp/packaging-smoke/bin/python -c "from ralph.cli._prompt_helper_entry import main; print('entrypoint importable')"
	@uv run pytest -q tests/test_ralph_prompt_entry.py
	-@rm -rf $(shell realpath .)/../tmp/packaging-smoke

dist-binary:
	@echo "Building PyInstaller binary..."
	pyinstaller ralph-workflow.spec
	@echo "Binary built at dist/ralph-workflow"

dist-pypi:
	@echo "Building and publishing to PyPI..."
	hatch build
	hatch publish

dist-all: dist-binary dist-pypi
	@echo "All distributions built successfully"

formula-check:
	@echo "Validating Homebrew formula..."
	@if command -v brew >/dev/null 2>&1; then \
		brew audit --strict Formula/ralph-workflow.rb; \
	else \
		echo "Homebrew not installed, skipping formula check"; \
	fi

dead-code:
	uv run --with vulture python -m vulture --config pyproject.toml

docs:
	uv run --extra docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html -W --keep-going

docs-linkcheck:
	uv run --extra docs sphinx-build -b linkcheck docs/sphinx docs/sphinx/_build/linkcheck

serve-docs: docs
	@echo "Serving docs at http://localhost:8080"
	cd docs/sphinx/_build/html && python -m http.server 8080

# Development helpers
run:
	python -m ralph

check:
	@echo "Running all checks..."
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) test

.DEFAULT_GOAL := help
