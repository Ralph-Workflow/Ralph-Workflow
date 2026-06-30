# Ralph (Python) Makefile
# Root convenience wrapper around ralph-workflow/Makefile

PY_DIR := ralph-workflow

.PHONY: all build verify lint format format-check typecheck test test-unit test-integration test-cov test-subprocess-e2e clean install stable dev install-dev publish test-pypi twine-upload twine-upload-testpypi help docs serve-docs packaging-smoke setup-hooks

all: verify

setup-hooks:
	@if [ "$$(git config core.hooksPath)" != ".githooks" ]; then \
		echo "  installing git hooks …"; \
		git config core.hooksPath .githooks; \
		echo "  ✓ hooks installed"; \
	else \
		echo "  ✓ hooks already installed"; \
	fi

build:
	$(MAKE) -C $(PY_DIR) build

verify:
	$(MAKE) -C $(PY_DIR) verify

packaging-smoke:
	$(MAKE) -C $(PY_DIR) packaging-smoke

lint:
	$(MAKE) -C $(PY_DIR) lint

format:
	$(MAKE) -C $(PY_DIR) format

format-check:
	$(MAKE) -C $(PY_DIR) format-check

typecheck:
	$(MAKE) -C $(PY_DIR) typecheck

test:
	$(MAKE) -C $(PY_DIR) test

test-unit:
	$(MAKE) -C $(PY_DIR) test-unit

test-integration:
	$(MAKE) -C $(PY_DIR) test-integration

test-cov:
	$(MAKE) -C $(PY_DIR) test-cov

test-subprocess-e2e:
	$(MAKE) -C $(PY_DIR) test-subprocess-e2e

clean:
	$(MAKE) -C $(PY_DIR) clean

publish:
	$(MAKE) -C $(PY_DIR) publish

test-pypi:
	$(MAKE) -C $(PY_DIR) test-pypi

twine-upload:
	$(MAKE) -C $(PY_DIR) twine-upload

twine-upload-testpypi:
	$(MAKE) -C $(PY_DIR) twine-upload-testpypi

install:
	$(MAKE) -C $(PY_DIR) install

stable:
	$(MAKE) -C $(PY_DIR) stable

dev:
	$(MAKE) -C $(PY_DIR) dev

install-dev:
	$(MAKE) -C $(PY_DIR) install-dev

docs:
	$(MAKE) -C $(PY_DIR) docs

serve-docs:
	$(MAKE) -C $(PY_DIR) serve-docs

help:
	@echo "Ralph (Python) root targets"
	@echo "  make verify      - docs build + ruff + mypy + 60s-capped tests + 14 audits (excludes coverage)"
	@echo "  make setup-hooks - install the .githooks pre-commit hook path"
	@echo "  make lint        - run ruff checks"
	@echo "  make typecheck   - run strict mypy"
	@echo "  make test        - run the full pytest suite without coverage"
	@echo "  make test-unit   - run tests excluding tests/integration/"
	@echo "  make test-integration - run tests/integration/ only"
	@echo "  make test-cov    - run the full pytest suite with coverage"
	@echo "  make test-subprocess-e2e - run subprocess-e2e marked tests (60s limit)"
	@echo "  make build       - build Python distribution"
	@echo "  make publish     - upload dist/* to PyPI via Twine"
	@echo "  make test-pypi   - upload dist/* to Test PyPI via Twine"
	@echo "  make twine-upload - explicit PyPI Twine upload target"
	@echo "  make twine-upload-testpypi - explicit Test PyPI Twine upload target"
	@echo "  make install     - dev build + 'rdev' launcher (~/.local/bin/rdev)"
	@echo "  make stable      - install/upgrade the pinned stable 'ralph' via uv tool"
	@echo "  make dev         - sync the dev environment only (no 'rdev' launcher)"
	@echo "  make install-dev - alias for make dev"
	@echo "  make docs        - build Sphinx HTML documentation"
	@echo "  make serve-docs  - build and serve docs at http://localhost:8080"
