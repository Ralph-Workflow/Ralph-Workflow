.PHONY: help install dev install-dev test test-unit test-integration lint typecheck build build-wheel build-sdist publish test-pypi test-pypi-upload clean dist clean-dist fmt format-check ruff-fix mypy-fix pre-commit dist-homebrew verify coverage dist-binary dist-pypi dist-all formula-check dead-code docs

PYTEST_WORKERS ?= 8
COVERAGE_WORKERS ?= 16

help:
	@echo "Ralph Workflow - Makefile commands"
	@echo ""
	@echo "  install         Install package and refresh pipx executable"
	@echo "  dev             Install package in editable mode with dev dependencies"
	@echo "  install-dev     Alias for dev"
	@echo "  test           Run the full pytest suite without coverage"
	@echo "  test-unit      Run unit/root tests without tests/integration/"
	@echo "  test-integration Run tests/integration/ only"
	@echo "  lint           Run ruff linting"
	@echo "  typecheck      Run mypy type checking"
	@echo "  fmt            Format code with ruff"
	@echo "  format-check   Check formatting without applying changes"
	@echo "  pre-commit     Run pre-commit hooks"
	@echo "  build          Build wheel and sdist"
	@echo "  build-wheel    Build wheel only"
	@echo "  build-sdist    Build sdist only"
	@echo "  publish        Publish to PyPI (requires TWINE_PASSWORD)"
	@echo "  test-pypi      Publish to Test PyPI"
	@echo "  test-pypi-upload Publish wheel to Test PyPI"
	@echo "  clean          Clean build artifacts"
	@echo "  clean-dist     Clean dist folder"
	@echo "  dist-homebrew  Build Homebrew distribution"
	@echo "  verify         Run full verification (ruff, mypy, pytest with coverage)"
	@echo "  dist-binary    Build PyInstaller single-file binary"
	@echo "  dist-pypi      Build and publish to PyPI via hatch"
	@echo "  dist-all       Build both binary and PyPI distributions"
	@echo "  formula-check  Validate Homebrew formula syntax"
	@echo "  dead-code      Run Vulture dead-code audit (fails while backlog exists)"
	@echo "  docs           Build Sphinx HTML documentation (not part of verify)"
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
	uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n $(PYTEST_WORKERS)

test-unit:
	uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n $(PYTEST_WORKERS) --ignore=tests/integration

test-integration:
	uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/integration/ -q -n $(PYTEST_WORKERS)

coverage: test-cov

test-cov:
	uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n $(PYTEST_WORKERS) --cov=ralph --cov-report=term-missing --cov-report=html --cov-fail-under=80

# Linting
lint:
	ruff check ralph/ tests/

typecheck:
	uv run python -m mypy ralph/

fmt:
	ruff format ralph/ tests/

format-check:
	ruff format --check ralph/ tests/

ruff-fix:
	ruff check --fix ralph/ tests/

mypy-fix:
	uv run python -m mypy ralph/

# Pre-commit
pre-commit:
	pre-commit run --all-files

# Building
build: clean-dist
	python -m build

build-wheel: clean-dist
	python -m build --wheel

build-sdist: clean-dist
	python -m build --sdist

# Publishing
publish: dist
	twine upload dist/*

test-pypi: dist
	twine upload --repository testpypi dist/*

test-pypi-upload: dist
	twine upload --repository testpypi dist/*

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
verify:
	@echo "Running full verification..."
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) test-cov

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
	uv run --with sphinx --with sphinx-rtd-theme --with myst-parser sphinx-build -b html docs/sphinx docs/sphinx/build/html -W --keep-going

# Development helpers
run:
	python -m ralph

check:
	@echo "Running all checks..."
	@$(MAKE) lint
	@$(MAKE) typecheck
	@$(MAKE) test

.DEFAULT_GOAL := help
