# Ralph (Python) Makefile
# Root convenience wrapper around ralph-workflow/Makefile

PY_DIR := ralph-workflow

.PHONY: all build verify lint format format-check typecheck test test-cov clean install dev install-dev help

all: verify

build:
	$(MAKE) -C $(PY_DIR) build

verify:
	$(MAKE) -C $(PY_DIR) verify

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

test-cov:
	$(MAKE) -C $(PY_DIR) test-cov

clean:
	$(MAKE) -C $(PY_DIR) clean

install:
	$(MAKE) -C $(PY_DIR) install

dev:
	$(MAKE) -C $(PY_DIR) dev

install-dev:
	$(MAKE) -C $(PY_DIR) install-dev

help:
	@echo "Ralph (Python) root targets"
	@echo "  make verify      - lint + typecheck + tests with coverage"
	@echo "  make lint        - run ruff checks"
	@echo "  make typecheck   - run strict mypy"
	@echo "  make test        - run pytest"
	@echo "  make test-cov    - run pytest with coverage threshold"
	@echo "  make build       - build Python distribution"
	@echo "  make install     - install package and refresh pipx executable"
	@echo "  make dev         - editable install with dev deps"
	@echo "  make install-dev - alias for make dev"
