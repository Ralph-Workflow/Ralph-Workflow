# Ralph Workflow (Python)

Python 3.12+ implementation of the Ralph Workflow CLI.

## Installation

### pip (from PyPI)

```bash
pip install ralph-workflow
ralph --help
```

### pipx (recommended for CLI tools)

[pipx](https://pypa.github.io/pipx/) installs CLI tools in isolated environments, keeping your system clean.

```bash
# Install pipx if you don't have it
python -m pip install pipx
python -m pipx ensurepath

# Install ralph using pipx
pipx install ralph-workflow

# Verify installation
ralph --help
```

### pipx with git repository (latest development version)

```bash
pipx install git+https://codeberg.org/RalphWorkflow/Ralph-Workflow.git#subdirectory=ralph-python
```

### Development

Install development dependencies:

```bash
python -m pip install -e ".[dev]"
ralph --version
```

Run verification:

```bash
ruff check ralph/ tests/
pytest tests/ -v --cov=ralph --cov-report=term-missing --cov-report=html

rm -rf dist
hatch build
python -m twine check dist/*
```

This package exposes the `ralph` CLI via `ralph.cli.main:app`.
