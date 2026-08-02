.PHONY: help install stable dev install-dev test test-unit test-integration test-subprocess-e2e test-auto-integrate-e2e lint typecheck build build-wheel build-sdist publish test-pypi test-pypi-upload twine-upload twine-upload-testpypi clean dist clean-dist fmt format-check ruff-fix mypy-fix pre-commit dist-homebrew verify coverage dist-binary dist-pypi dist-all formula-check dead-code docs docs-linkcheck route-linkcheck policy-citation-linkcheck serve-docs packaging-smoke

# pytest-xdist worker count for parallel test execution.
#
# Default: `auto` resolves to the fixed measured-safe eight-shard profile,
# independent of available CPU count. The 1.0 s per-test SIGALRM budget charges
# wall clock; this profile keeps the maintained suite below the immutable 60 s
# budget even on low-core hosts.
# Operators may override after measuring their own host.
#
# The raw targets (test-unit / test-integration / test-subprocess-e2e /
# test-cov / test-auto-integrate-e2e) interpolate ``-n $(PYTEST_WORKERS)``
# literally, so this exported default governs both ralph.test_suites and
# the raw-target paths. Keep conditional assignment so operators can
# override:
#   PYTEST_WORKERS=24 make test   # measured automatic profile
#   PYTEST_WORKERS=2 make test    # may exceed the suite budget
#   PYTEST_WORKERS=32 make test   # may exceed 1s per-test under load
#
# The 60s combined test budget is ABSOLUTE and IMMUTABLE — enforced
# by ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS. This per-suite timeout
# is a SECONDARY cap only. PYTEST_WORKERS is parallelism, not a budget
# extension — increasing parallelism keeps wall-clock under the budget,
# but past a saturation point it starves individual workers past the
# per-test 1 s SIGALRM cap instead.
PYTEST_WORKERS ?= auto
export PYTEST_WORKERS
export PYTHONPATH := $(shell realpath .)
# ``uv run`` refuses to honor an inherited ``VIRTUAL_ENV`` that points
# outside the current project (``warning: VIRTUAL_ENV=... does not
# match the project environment path .venv``). Make targets below
# already run from the project root, so re-point ``VIRTUAL_ENV`` at
# the canonical project-local ``.venv`` before any ``uv run`` is
# invoked. This silences the warning without weakening the
# environment-detection contract: if ``.venv`` does not exist
# ``uv run`` will recreate it as it would without ``VIRTUAL_ENV``.
ifneq ($(wildcard .venv/pyvenv.cfg),)
  export VIRTUAL_ENV := $(abspath .venv)
endif
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
# Always select the project-managed interpreter. Setting VIRTUAL_ENV alone does
# not alter PATH, so a bare `python` can bypass .venv and miss dependencies.
RUN_PYTHON = uv run python
help:
	@echo "Ralph Workflow - Makefile commands"
	@echo ""
	@echo "  install         Refresh the self-contained manual -build snapshot and 'rdev' launcher"
	@echo "  stable          Install the latest stable build as global 'ralph' via uv tool"
	@echo "  dev             Refresh the self-contained -dev snapshot and 'rdev' launcher"
	@echo "  install-dev     Alias for dev"
	@echo "  test           Run the full pytest suite without coverage (excludes subprocess_e2e)"
	@echo "  test-unit      Run unit/root tests without tests/integration/"
	@echo "  test-integration Run tests/integration/ only (excludes subprocess_e2e)"
	@echo "  test-subprocess-e2e Run tests that require real subprocesses or network sockets (60s limit)"
	@echo "  test-live-agy    Run live AGY subprocess tests (excluded from test-subprocess-e2e; longer timeout)"
	@echo "  test-auto-integrate-e2e Run the real-git auto-integration end-to-end proof (wired into 'make verify')"
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
	@echo "  verify         Run full verification (docs build + ruff, mypy, pytest, audits; excludes coverage)"
	@echo "  dist-binary    Build PyInstaller single-file binary"
	@echo "  dist-pypi      Build and publish to PyPI via hatch"
	@echo "  dist-all       Build both binary and PyPI distributions"
	@echo "  formula-check  Validate Homebrew formula syntax"
	@echo "  dead-code      Run Vulture dead-code audit (fails while backlog exists)"
	@echo "  docs           Build Sphinx HTML documentation (gated by 'make verify' via Make prerequisite)"
	@echo "  docs-linkcheck Check all hyperlinks in docs (opt-in; not in verify, run on demand)"
	@echo "  route-linkcheck Check links in non-Sphinx route pages (opt-in, not in verify)"
	@echo "  serve-docs     Build and serve docs at http://localhost:8080"
	@echo ""
	@echo "  run            Run ralph CLI directly"
	@echo "  check          Run all checks (lint, typecheck, test)"

# Installation delegates to the tested installer; `rdev` runs copied snapshots.
install:
	$(RUN_PYTHON) -m ralph.install --build

dev:
	$(RUN_PYTHON) -m ralph.install

stable:
	$(RUN_PYTHON) -m ralph.install --stable

install-dev: dev

# Testing
test:
	$(RUN_PYTHON) -m ralph.test_suites

test-unit:
	$(RUN_PYTHON) -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/ -q -n $(PYTEST_WORKERS) --dist worksteal --ignore=tests/integration -m "not subprocess_e2e and not smoke"

test-integration:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/integration/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e and not smoke"

# Uses the maintained static file selector, avoiding full-suite collection
# merely to deselect non-E2E tests. The selector preserves the canonical
# subprocess-E2E marker expression inside each explicit shard.
test-subprocess-e2e:
	$(RUN_PYTHON) -m ralph.test_suites --subprocess-e2e

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

# Focused profile over the same explicit 24-file registry used by
# the combined ``make test`` invocation.
test-auto-integrate-e2e:
	$(RUN_PYTHON) -m ralph.test_suites --auto-integrate-e2e

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
	uv run --with hatchling hatch build -t wheel

build-sdist: clean-dist
	uv run --with hatchling hatch build -t sdist

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
	@bash scripts/packaging_operations.sh dist-homebrew

# Distribution targets
# Combined 60s test budget enforced by ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS (IMMUTABLE).
# `docs` is a Make prerequisite (NOT a `_VERIFY_STEPS` entry) so the Sphinx
# build stays non-budget-tracked and the immutable 60s test budget is
# unchanged. `make docs` uses `uv run --extra docs sphinx-build -W` so any
# Sphinx warning fails the build before the python-verify step runs.
verify: verify-drift docs
	@uv run python -m ralph.verify

# Drift detection delegates to the bounded, fail-closed policy script.
verify-drift:
	@bash scripts/verify_drift.sh

policy-loader-smoke:
	@python -c "import sys; sys.stderr = open('/dev/null', 'w'); from pathlib import Path; from ralph.policy.loader import load_policy; load_policy(Path('ralph/policy/defaults'))"

packaging-smoke:
	@bash scripts/packaging_operations.sh packaging-smoke

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
	@bash scripts/packaging_operations.sh formula-check

dead-code:
	uv run --with vulture python -m vulture --config pyproject.toml

docs:
	uv run --extra docs sphinx-build -b html docs/sphinx docs/sphinx/_build/html -W --keep-going

docs-linkcheck:
	uv run --extra docs sphinx-build -b linkcheck docs/sphinx docs/sphinx/_build/linkcheck

route-linkcheck:
	cd .. && python3 scripts/check_route_page_links.py README.md START_HERE.md docs/README.md ralph-workflow/docs/README.md

# Verify that every research-citation URL in the bundled project-policy
# starters resolves (HTTP < 400), satisfying the PROMPT's
# 'verify that every cited URL resolves' link-verification requirement.
# On-demand maintenance gate; not wired into 'make verify' because it
# requires network I/O.
policy-citation-linkcheck:
	cd .. && python3 scripts/check_route_page_links.py ralph-workflow/ralph/project_policy/starters/*.md

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
