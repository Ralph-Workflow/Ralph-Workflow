# Ralph Workflow

Ralph Workflow is a Python CLI for unattended, multi-agent software delivery loops. The maintained implementation lives in `ralph-workflow/`; this repository also keeps legacy design notes from the retired Rust implementation and a vendored Rust/Tauri GUI under `vendor/ralph-workflow-gui/`.

## What is current

- **Current product**: `ralph-workflow/`
- **Package name**: `ralph-workflow`
- **CLI entry points**: `ralph`, `ralph-mcp`
- **Primary toolchain**: Python 3.12+, `ruff`, `mypy`, `pytest`, `hatch`

## Install

### From PyPI

```bash
pip install ralph-workflow
ralph --help
```

### With pipx

```bash
python -m pip install pipx
python -m pipx ensurepath
pipx install ralph-workflow
ralph --help
```

### From this repository

```bash
cd ralph-workflow
python -m pip install -e ".[dev]"
ralph --version
```

## Quick start

```bash
cd /path/to/your/project
ralph --init feature-spec
# edit PROMPT.md
ralph
```

## Verification

```bash
cd ralph-workflow
make verify
```

That runs the current Python verification path:

- `ruff check ralph/ tests/`
- `mypy ralph/`
- `pytest tests/ -v --cov=ralph --cov-report=term-missing --cov-report=html`

## Repository map

- `ralph-workflow/README.md` — package install, development, and API overview
- `ralph-workflow/CONTRIBUTING.md` — Python contributor workflow
- `docs/README.md` — current vs legacy documentation map
- `vendor/ralph-workflow-gui/` — vendored Rust GUI work; not the canonical CLI implementation

## Legacy documentation status

Large parts of `docs/`, `CODE_STYLE.md`, and older plans/RFCs were written for the retired Rust implementation. They are kept for migration history and background context, not as the source of truth for the Python package unless a document explicitly says it has been refreshed for Python.

For current behavior, prefer:

1. `ralph-workflow/README.md`
2. `docs/agents/verification.md`
3. the Python source/docstrings under `ralph-workflow/ralph/`

## License

Licensed under AGPL-3.0-or-later.