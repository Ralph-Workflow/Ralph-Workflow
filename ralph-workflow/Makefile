.PHONY: help install dev install-dev test test-unit test-integration test-subprocess-e2e lint typecheck build build-wheel build-sdist publish test-pypi test-pypi-upload twine-upload twine-upload-testpypi clean dist clean-dist fmt format-check ruff-fix mypy-fix pre-commit dist-homebrew verify coverage dist-binary dist-pypi dist-all formula-check dead-code docs docs-linkcheck serve-docs packaging-smoke

PYTEST_WORKERS ?= 4
PYTEST_SUITE_TIMEOUT_SECONDS ?= 30
PYTEST_CORE_PATHS = tests/agents tests/config tests/display tests/fixtures tests/unit
PYTEST_RUNTIME_PATHS = tests/mcp tests/pipeline tests/recovery
PYTEST_ROOT_PATHS_A = tests/test_[aA]*.py
PYTEST_ROOT_PATHS_B = tests/test_[bB]*.py
PYTEST_ROOT_PATHS_C_D = tests/test_[c-dC-D]*.py
PYTEST_ROOT_PATHS_E_F = tests/test_[e-fE-F]*.py
PYTEST_ROOT_PATHS_G_H = tests/test_[g-hG-H]*.py
PYTEST_ROOT_PATHS_I_J = tests/test_[i-jI-J]*.py
PYTEST_ROOT_PATHS_K_L = tests/test_[k-lK-L]*.py
PYTEST_ROOT_PATHS_M = tests/test_[mM]*.py
PYTEST_ROOT_PATHS_N = tests/test_[nN]*.py
PYTEST_ROOT_PATHS_O = tests/test_[oO]*.py
PYTEST_ROOT_PATHS_PA_PC = tests/test_p[a-cA-C]*.py
PYTEST_ROOT_PATHS_PD_PF = tests/test_p[d-fD-F]*.py
PYTEST_ROOT_PATHS_PG_PI = tests/test_p[g-iG-I]*.py
PYTEST_ROOT_PATHS_PJ_PL = tests/test_p[j-lJ-L]*.py
PYTEST_ROOT_PATHS_PM_PZ = tests/test_p[m-zM-Z]*.py
PYTEST_ROOT_PATHS_Q_S = tests/test_[q-sQ-S]*.py
PYTEST_ROOT_PATHS_T_Z = tests/test_[t-zT-Z]*.py

help:
	@echo "Ralph Workflow - Makefile commands"
	@echo ""
	@echo "  install         Install package and refresh pipx executable"
	@echo "  dev             Install package in editable mode with dev dependencies"
	@echo "  install-dev     Alias for dev"
	@echo "  test           Run the full pytest suite without coverage (excludes subprocess_e2e)"
	@echo "  test-unit      Run unit/root tests without tests/integration/"
	@echo "  test-integration Run tests/integration/ only (excludes subprocess_e2e)"
	@echo "  test-subprocess-e2e Run tests that require real subprocesses or network sockets (60s limit)"
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
install:
	python -m ralph.install

dev:
	python -m pip install -e ".[dev]"

install-dev: dev

# Testing
test:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_B) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_D) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_E_F) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_G_H) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_I_J) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_K_L) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_N) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_O) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PA_PC) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PD_PF) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PG_PI) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PJ_PL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PM_PZ) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_Z) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/integration/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"

test-unit:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_CORE_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_RUNTIME_PATHS) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_A) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_B) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_C_D) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_E_F) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_G_H) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_I_J) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_K_L) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_M) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_N) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_O) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PA_PC) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PD_PF) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PG_PI) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PJ_PL) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_PM_PZ) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_Q_S) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest $(PYTEST_ROOT_PATHS_T_Z) -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"

test-integration:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/integration/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e"

coverage: test-cov

test-subprocess-e2e:
	uv run python -m ralph.verify_timeout --suite-timeout $(PYTEST_SUITE_TIMEOUT_SECONDS) -- python -m pytest tests/ -q -n 1 -m subprocess_e2e

test-cov:
	rm -f .coverage.pytest .coverage.pytest.*
	uv run python -m pytest tests/ -q -n $(PYTEST_WORKERS) --dist worksteal -m "not subprocess_e2e" --cov=ralph --cov-report=
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
verify: packaging-smoke policy-loader-smoke
	@uv run python -m ralph.verify

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
	@rm -rf $(shell realpath .)/../tmp/packaging-smoke

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
