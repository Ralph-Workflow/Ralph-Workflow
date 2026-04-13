# Python Verification (before PR/completion)

This guide covers verification for the Ralph Python conversion project located in `ralph-python/`.

## Quick Verification

```bash
cd ralph-python

# Install dependencies
pip install -e ".[dev]"

# Run linting
ruff check ralph/

# Run type checking
mypy ralph/

# Run tests
pytest tests/ -v --cov=ralph --cov-report=term-missing
```

## Detailed Verification Steps

### 1. Linting (ruff)

```bash
cd ralph-python
ruff check ralph/ --fix
```

The ruff configuration in `pyproject.toml` enforces:
- E, F, W (errors, Pyflakes, warnings)
- I (isort)
- N (naming conventions)
- UP (pyupgrade)
- ANN (annotations)
- B (flake8-bugbear)
- C4 (flake8-comprehensions)
- SIM (simpler)
- RUF (ruff-specific)

### 2. Type Checking (mypy)

```bash
cd ralph-python
mypy ralph/ --strict
```

The mypy configuration enforces strict mode with pydantic plugin.

### 3. Testing

```bash
cd ralph-python
pytest tests/ -v --cov=ralph --cov-report=term-missing
```

Target: 80%+ branch coverage for the `ralph` package.

### 4. All Checks Combined

```bash
cd ralph-python
ruff check ralph/ && mypy ralph/ && pytest tests/ -v
```

## Python-Specific Rules

### No `type: ignore` Suppressions

With few exceptions (external library compatibility like `tomli`/`tomllib`), avoid `# type: ignore` comments. Fix the underlying type issue instead.

### No Broad Exception Catches

Avoid catching `Exception` broadly. Catch specific exceptions like `json.JSONDecodeError` or `ValueError`.

### Docstring Requirements

- All public modules, classes, and functions must have docstrings
- Use Google-style docstrings with Args, Returns, Raises sections

### Import Organization

Standard library → third-party → local, with `isort` enforcing alphabetical order within groups.

## Common Issues and Fixes

### Pydantic Models

- Use `model_config = ConfigDict(frozen=True)` for immutable models
- Use `Field(default_factory=...)` for mutable default values
- Use `model_validate_json()` for JSON deserialization

### Phase Constants

Use `PHASE_*` constants from `ralph.config.enums`:
- `PHASE_PLANNING`
- `PHASE_DEVELOPMENT`
- `PHASE_DEVELOPMENT_ANALYSIS`
- `PHASE_DEVELOPMENT_COMMIT`
- `PHASE_REVIEW`
- `PHASE_REVIEW_ANALYSIS`
- `PHASE_FIX`
- `PHASE_REVIEW_COMMIT`
- `PHASE_COMPLETE`
- `PHASE_FAILED`

Do NOT use `PipelinePhase.DEVELOPMENT` - `PipelinePhase` is a type alias to `str`, not an enum.

### Workspace Protocol

All file I/O should go through the `Workspace` protocol to enable test doubles:
- `workspace.read(path)` - read file contents
- `workspace.write(path, content)` - write file contents
- `workspace.exists(path)` - check if file exists
- `workspace.list_dir(path)` - list directory contents

### TOML Policy Loading

Use `ralph.policy.loader.load_policy()` to load policy from TOML files. The loader handles Python version compatibility for `tomllib`/`tomli`.

## Distribution Verification

### PyInstaller

```bash
cd ralph-python
pyinstaller ralph.spec
```

### Homebrew

Formula is in `Formula/ralph-workflow.rb`. Update SHA256 and URL before release.

### PyPI

```bash
cd ralph-python
pip install build
python -m build
```
