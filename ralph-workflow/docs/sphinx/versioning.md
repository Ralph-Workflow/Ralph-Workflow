# Release & Versioning

This maintainer-facing page describes how to bump, build, validate, and publish a new Ralph Workflow release.

## 1. Bump the version

Edit `ralph/__init__.py` and update `__version__`:

```python
__version__ = "1.2.3"   # set new semver here
```

The version is declared in `ralph/__init__.py` and read dynamically by Hatch via
`[tool.hatch.version] source = "code"` in `pyproject.toml`. Do **not** edit
`[project].version` in `pyproject.toml` — it is set to `dynamic = ["version"]`
and Hatch manages it automatically from the source file.

Ralph Workflow follows [Semantic Versioning](https://semver.org/):
- **PATCH** (`1.2.x`): backward-compatible bug fixes.
- **MINOR** (`1.x.0`): new backward-compatible features.
- **MAJOR** (`x.0.0`): breaking API or behavior changes.

## 2. Build distribution artifacts

```bash
cd ralph-workflow
uv run hatch build
```

This produces `dist/ralph_workflow-<version>-py3-none-any.whl` and
`dist/ralph_workflow-<version>.tar.gz`.

## 3. Validate metadata

Before uploading, verify the built artifacts are well-formed:

```bash
uv run python -m twine check dist/*
```

A clean run prints `PASSED` for each file. Fix any warnings before proceeding.

## 4. Publish to Test PyPI (safe first step)

Upload to [Test PyPI](https://test.pypi.org/) to verify the release before
touching the production index:

```bash
uv run --with twine python -m twine upload --repository testpypi dist/*
# or: make twine-upload-testpypi
```

Credentials are read from `~/.pypirc`. A minimal configuration:

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
username = __token__
password = pypi-<your-production-token>

[testpypi]
repository = https://upload.test.pypi.org/legacy/
username = __token__
password = pypi-<your-test-token>
```

Prefer API tokens over passwords; never commit credentials to version control.

## 5. Publish to Production PyPI

After confirming the Test PyPI release looks correct:

```bash
uv run --with twine python -m twine upload dist/*
# or: make twine-upload
```

## 6. Tag the release

After publishing, generate and commit the release tag using Ralph Workflow itself:

```bash
ralph --generate-commit
git tag v<version>
git push origin v<version>
```

The tag triggers any configured CI release workflows.

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough and install
- [CLI Reference](cli.md) — `--generate-commit` and other flags
- [Concepts](concepts.md) — pipeline phases and artifact types
