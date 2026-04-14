# Contributing to Ralph Workflow

Thank you for your interest in contributing to Ralph Workflow.

## Development Setup

Clone the repository and install dependencies:

```bash
git clone https://github.com/RalphWithReviewer/Ralph-Workflow.git
cd Ralph-Workflow/ralph-python
python -m pip install -e ".[dev]"
```

Run the checks that are currently used for Python package validation before submitting changes:

```bash
ruff check ralph/ tests/
pytest tests/ -v --cov=ralph --cov-report=term-missing --cov-report=html
```

For release work, also validate the built artifacts:

```bash
rm -rf dist
hatch build
python -m twine check dist/*
```

## Code Style

- Follow existing patterns in the codebase
- Add type annotations to all function signatures
- Write tests for new functionality (see `tests/` directory)
- Run `ruff check ralph/ tests/` before committing
- `mypy ralph/` currently reports repository-wide existing issues and is not a reliable release gate yet

## Release Process

PyPI releases are built with `hatch` and published by the repository-level GitHub Actions workflow `.github/workflows/publish-python-package.yml` using PyPI trusted publishing.

### Prepare a Release

1. Update `ralph/__init__.py` to the target version.
2. Build and validate the distribution from `ralph-python/`:

```bash
python -m pip install hatch twine
rm -rf dist
hatch build
python -m twine check dist/*
```

3. Smoke-test the built package in an isolated environment:

```bash
python -m venv .venv-release-check
. .venv-release-check/bin/activate
python -m pip install --upgrade pip
python -m pip install dist/*.whl
ralph --version
deactivate
rm -rf .venv-release-check
```

### Publish to PyPI

1. Commit the version bump.
2. Create and push a tag from the repository root using the package-specific format:

```bash
git tag ralph-python-v<major.minor.patch>
git push origin ralph-python-v<major.minor.patch>
```

3. GitHub Actions builds `ralph-python/dist/`, runs `twine check`, and publishes to PyPI from the trusted `pypi` environment.

PyPI must be configured to trust this repository and workflow before the first release.

## Testing

Run tests with pytest:

```bash
pytest tests/ -v
```

Run with coverage:

```bash
pytest tests/ -v --cov=ralph --cov-report=term-missing
```

## Submitting Changes

1. Fork the repository
2. Create a feature branch
3. Make your changes with tests
4. Run the validation commands above
5. Open a pull request

## Questions

For questions or discussions, open an issue on the repository.
