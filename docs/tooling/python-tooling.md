# Python Tooling Guide

This document covers the Python tooling used in the Ralph Workflow project.

## Overview

Ralph uses modern Python tooling for linting, type checking, testing, building, and distribution:

| Tool | Purpose | Config |
|------|---------|--------|
| ruff | Linter + formatter (replaces black, flake8, isort) | `ruff.toml` |
| mypy | Strict type checker | `mypy.ini` |
| pytest | Test runner with coverage | `pyproject.toml` |
| hatch | Build backend + publish | `pyproject.toml` |
| pyinstaller | Single-file binary distribution | `ralph-workflow.spec` |

## ruff (Linter + Formatter)

ruff combines linting and formatting in a single fast tool, replacing black, flake8, isort, and several other linting tools.

### Running ruff

```bash
# Lint all code
make lint

# Format all code
make fmt

# Check formatting without applying
make format-check

# Auto-fix lint issues
make ruff-fix
```

### Configuration

The `ruff.toml` file configures:
- **select**: Enabled lint rules (E, F, W, I, N, UP, ANN, B, C4, SIM, RUF, TCH, PTH, PERF, PL)
- **ignore**: Disabled rules (ANN101, ANN102 for self/cls annotations)
- **line-length**: 100 characters
- **format**: Ruff's own formatter (drop-in black replacement)

### Key Rule Groups

- **E, F, W**: pycodestyle, pyflakes, warnings
- **I**: isort compatibility
- **N**: pep8-naming
- **UP**: pyupgrade
- **ANN**: flake8-annotations (type hints)
- **B**: flake8-bugbear
- **C4**: flake8-comprehensions
- **SIM**: flake8-simplify
- **RUF**: ruff-specific rules
- **TCH**: flake8-type-checking (imports in TYPE_CHECKING blocks)
- **PTH**: flake8-use-pathlib
- **PERF**: perflint (performance)
- **PL**: pylint

## mypy (Type Checker)

mypy provides strict static type checking with Pydantic integration.

### Running mypy

```bash
# Type check all code
make typecheck

# With mypy directly
cd ralph-workflow
mypy ralph/
```

### Configuration

The `mypy.ini` file configures:
- **strict = true**: Enable all strict checks
- **python_version = 3.12**: Target version
- **plugins = pydantic.mypy**: Pydantic v2 plugin support

### Per-Module Overrides

Third-party libraries without type stubs are handled via per-module overrides in `mypy.ini`:

```ini
[[overrides]]
module = "git.*"
ignore_missing_imports = true
```

## pytest (Test Runner)

pytest runs tests with coverage reporting.

### Running pytest

```bash
# Run all tests
make test

# Run with coverage report
make test-cov

# Run specific test file
pytest tests/test_orchestrator.py -v

# Run tests matching a pattern
pytest -k "policy" -v
```

### Coverage Requirements

- Minimum 80% branch coverage required
- Coverage report shows missing lines: `--cov-report=term-missing`
- HTML report available: `--cov-report=html`

### Test Structure

```
tests/
├── __init__.py
├── conftest.py              # Shared fixtures
├── test_checkpoint.py       # Checkpoint serialization
├── test_cli.py             # CLI commands
├── test_config_loader.py    # Configuration loading
├── test_git_operations.py  # Git operations
├── test_parsers.py         # Agent output parsers
├── test_reducer.py         # Pipeline reducer pure functions
├── test_policy_validation.py  # Policy validation (to be created)
├── test_orchestrator.py    # Orchestrator routing (to be created)
└── integration/
    ├── __init__.py
    └── test_pipeline_happy_path.py  # Full pipeline integration
```

## hatch (Build + Publish)

hatch is the build backend and handles PyPI publishing.

### Building

```bash
# Build wheel and sdist
make build

# Build wheel only
make build-wheel

# Build sdist only
make build-sdist
```

### Publishing

```bash
# Publish to PyPI (requires PYPI_TOKEN)
make publish

# Publish to Test PyPI
make test-pypi
```

### Configuration

The `[tool.hatch]` section in `pyproject.toml` configures:
- **version source**: vcs (git tags)
- **build targets**: wheel packages
- **publish**: PyPI index URL and auth

## pyinstaller (Binary Distribution)

pyinstaller creates a standalone single-file binary with no Python installation required.

### Building the Binary

```bash
# Install pyinstaller
pip install pyinstaller

# Build the binary
make dist-binary

# Output: dist/ralph-workflow (macOS universal2 binary)
```

### How It Works

The `ralph-workflow.spec` file configures:
- **Analysis**: Entry point (`ralph/__main__.py`), hidden imports, excluded modules
- **datas**: Policy default TOML files via `collect_data_files("ralph.policy.defaults")`
- **EXE**: One-file binary with `strip=True`, `target_arch="universal2"`

### Binary Contents

The resulting binary includes:
- Python interpreter
- All ralph package modules
- Policy default files (agents.toml, pipeline.toml, artifacts.toml)
- Required third-party libraries (pydantic, rich, typer, httpx, loguru, etc.)

## make verify (Canonical Verification)

The `make verify` target runs the complete verification suite:

```bash
cd ralph-workflow
make verify
```

This executes:
1. `make lint` — ruff check (zero violations required)
2. `make typecheck` — mypy strict (zero type errors required)
3. `make docs` — Sphinx build with warnings treated as errors
4. `make test-cov` — pytest with coverage (80% minimum branch coverage)
5. `make test-subprocess-e2e` — subprocess/network-marked end-to-end checks

If any step fails, `make verify` emits a high-visibility failure banner that cites `AGENTS.md` and `CLAUDE.md` and instructs the active AI agent to fix the failure immediately.

## Distribution Strategy

### Distribution Channels

| Channel | Command | Output |
|---------|---------|--------|
| PyInstaller binary | `make dist-binary` | `dist/ralph-workflow` |
| Homebrew (macOS) | `brew install ./Formula/ralph-workflow.rb` | `ralph` command |
| PyPI wheel | `make dist-pypi` | `dist/*.whl` |
| pipx | `pipx install ralph-workflow` | isolated install |
| uvx | `uvx ralph-workflow` | temporary run |

### Updating the Homebrew Formula

After a new release:

1. Build the binary: `make dist-binary`
2. Rename for Homebrew: `mv dist/ralph-workflow dist/ralph-darwin-universal2`
3. Create tarball: `tar -czvf ralph-darwin-universal2.tar.gz -C dist ralph-darwin-universal2`
4. Compute sha256: `shasum -a 256 ralph-darwin-universal2.tar.gz`
5. Update `Formula/ralph-workflow.rb` with new URL and sha256
6. Test: `brew audit --strict Formula/ralph-workflow.rb`
