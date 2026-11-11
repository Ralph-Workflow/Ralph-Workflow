.PHONY: help install stable dev install-dev rdev uv-preflight test test-fast test-unit test-integration test-subprocess-e2e test-auto-integrate-e2e test-verification-smoke test-install-make-smoke test-multimodal-smoke test-visual-capture test-visual-verdict test-visual-smoke lint typecheck build build-wheel build-sdist publish test-pypi test-pypi-upload twine-upload twine-upload-testpypi clean dist clean-dist fmt format-check ruff-fix mypy-fix pre-commit dist-homebrew verify coverage dist-binary dist-pypi dist-all formula-check dead-code docs docs-linkcheck route-linkcheck policy-citation-linkcheck serve-docs packaging-smoke

MULTIMODAL_SMOKE_WORKERS ?= 8
VERIFICATION_SMOKE_WORKERS ?= 2
PYTEST_WORKERS ?= auto
export PYTEST_WORKERS
PYTEST_XDIST_WORKERS_PER_SHARD ?= 0
export PYTEST_XDIST_WORKERS_PER_SHARD
export PYTHONPATH := $(shell realpath .)
export VIRTUAL_ENV := $(abspath .venv)
PYTEST_SUITE_TIMEOUT_SECONDS ?= 60
COVERAGE_SUITE_TIMEOUT_SECONDS ?= 120
LIVE_AGY_SUITE_TIMEOUT_SECONDS ?= 600
RUN_PYTHON = uv run --locked --project . python
help:
	@echo "Ralph Workflow - Makefile commands"
	@echo ""
	@echo "  install         Refresh the self-contained manual -build snapshot and 'rdev' launcher"
	@echo "  stable          Install the latest stable build as global 'ralph' via uv tool"
	@echo "  dev             Refresh the self-contained -dev snapshot and 'rdev' launcher"
	@echo "  install-dev     Alias for dev"
	@echo "  rdev            Alias for dev, named after the launcher it installs"
	@echo "  test           Run the full pytest suite without coverage (excludes subprocess_e2e)"
	@echo "  test-fast      Run the static suite-routing contract profile (not full verification)"
	@echo "  test-unit      Run unit/root tests without tests/integration/"
	@echo "  test-integration Run tests/integration/ only (excludes subprocess_e2e)"
	@echo "  test-subprocess-e2e Run tests that require real subprocesses or network sockets (60s limit)"
	@echo "  test-verification-smoke  All deterministic smoke suites in one budget-tracked invocation"
	@echo "  test-install-make-smoke  Offline real-process make install transaction proof (wired into 'make verify')"
	@echo "  test-live-agy    Run live AGY subprocess tests (excluded from test-subprocess-e2e; longer timeout)"
	@echo "  test-multimodal-smoke  Drive the multimodal smoke stub across the six harness identities (smoke-marked, in \`make verify\`)"
	@echo "  test-visual-capture   Capture, lifecycle, and MCP-visibility visual contracts"
	@echo "  test-visual-verdict   Black-box tests for the DesignVerdict three-input boundary"
	@echo "  test-visual-smoke     Deterministic offline visual smoke (capture + verdict fixtures; budget-tracked in \`make verify\`)"
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

uv-preflight:
	@uv_path=$$(command -v uv) || { echo "uv >= 0.7.0 is required; install it: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }; uv_version=$$($$uv_path --version 2>/dev/null) || { echo "uv >= 0.7.0 is required; install it: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }; printf '%s\n' "$$uv_version" | awk '$$1 == "uv" && $$2 ~ /^[0-9]+\.[0-9]+\.[0-9]+([.+-][0-9A-Za-z.-]+)?$$/ { split($$2, v, /[.+-]/); if (v[1] > 0 || (v[1] == 0 && (v[2] > 7 || (v[2] == 7 && v[3] >= 0)))) exit 0 } { exit 1 }' || { echo "uv >= 0.7.0 is required; install it: https://docs.astral.sh/uv/getting-started/installation/" >&2; exit 1; }

install: uv-preflight
	$(RUN_PYTHON) -m ralph.install --build

dev: uv-preflight
	$(RUN_PYTHON) -m ralph.install

stable: uv-preflight
	$(RUN_PYTHON) -m ralph.install --stable

install-dev: dev

rdev: dev

test:
	$(RUN_PYTHON) -m ralph.test_suites

test-fast:
	uv run --locked --project . python -m ralph.test_suites --profile fast

test-unit:
	uv run --locked --project . python -m ralph.test_suites --profile unit

test-integration:
	uv run --locked --project . python -m ralph.test_suites --profile integration

test-subprocess-e2e:
	$(RUN_PYTHON) -m ralph.test_suites --subprocess-e2e

test-live-agy:
	uv run python -m ralph.verify_timeout --suite-timeout $(LIVE_AGY_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/ -q -n 1 -m "live_agy"

test-verification-smoke:
	$(RUN_PYTHON) -m pytest tests/test_install_make_smoke.py tests/test_smoke_multimodal_end_to_end.py tests/test_visual_smoke.py tests/agents/invoke/test_real_pty_completion_no_terminal_lost.py -q -n $(VERIFICATION_SMOKE_WORKERS) --dist worksteal -m "smoke and subprocess_e2e"

test-install-make-smoke:
	$(RUN_PYTHON) -m pytest tests/test_install_make_smoke.py -q -m "smoke and subprocess_e2e"

test-multimodal-smoke:
	$(RUN_PYTHON) -m pytest tests/test_smoke_multimodal_end_to_end.py -q -n $(MULTIMODAL_SMOKE_WORKERS) --dist worksteal -m "smoke and subprocess_e2e"

test-visual-capture:
	$(RUN_PYTHON) -m pytest tests/test_visual_capture.py tests/test_visual_capture_lifecycle.py tests/test_mcp_server_multimodal_tool_visibility_1.py -q -n $(PYTEST_WORKERS) --dist worksteal

test-visual-verdict:
	$(RUN_PYTHON) -m pytest tests/test_visual_verdict.py -q -n $(PYTEST_WORKERS) --dist worksteal

test-visual-smoke:
	$(RUN_PYTHON) -m pytest tests/test_visual_smoke.py -q -m "smoke and subprocess_e2e"

test-auto-integrate-e2e:
	$(RUN_PYTHON) -m ralph.test_suites --auto-integrate-e2e

test-verify-budget:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/test_verify_budget_real_time.py -v

coverage: test-cov

test-cov:
	rm -f .coverage.pytest .coverage.pytest.*
	RALPH_PYTEST_SUITE_TIMEOUT_SECONDS=$(COVERAGE_SUITE_TIMEOUT_SECONDS) uv run python -m pytest tests/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e and not smoke" --cov=ralph --cov-report= --basetemp=$${TMPDIR:-/tmp}/ralph-coverage --no-header --tb=no --maxfail=1
	uv run coverage report --data-file=.coverage.pytest --fail-under=80

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

pre-commit:
	pre-commit run --all-files

build: clean-dist
	uv run --with hatchling hatch build

build-wheel: clean-dist
	uv run --with hatchling hatch build -t wheel

build-sdist: clean-dist
	uv run --with hatchling hatch build -t sdist

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

dist-homebrew: build-wheel
	@bash scripts/packaging_operations.sh dist-homebrew

verify: formula-check verify-drift docs
	@uv run python -m ralph.verify

verify-drift:
	@bash scripts/verify_drift.sh

verify-evidence:
	@cd .. && uv run --project ralph-workflow python scripts/record_verify_run.py

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

policy-citation-linkcheck:
	cd .. && python3 scripts/check_route_page_links.py ralph-workflow/ralph/project_policy/starters/*.md

serve-docs: docs
	@echo "Serving docs at http://localhost:8080"
	cd docs/sphinx/_build/html && python -m http.server 8080

run:
	python -m ralph

check:
	@echo "Running all checks..."
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) test

.DEFAULT_GOAL := help
