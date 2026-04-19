# Workspace Protocol (CRITICAL)

ALL filesystem operations MUST go through the `Workspace` protocol. Direct `open()`, `Path.read_text()`, `Path.write_text()`, or any other `pathlib`/`os` filesystem call is **FORBIDDEN** in production pipeline and phase code.

If you need deeper architecture context (how `FsWorkspace` wraps the real filesystem and where direct `pathlib` use is allowed at the CLI bootstrap layer), see the module docstring in `ralph/workspace/__init__.py`.

## Forbidden vs. Required

| FORBIDDEN | REQUIRED |
|-----------|----------|
| `open(path).read()` | `workspace.read(path)` |
| `Path(path).write_text(content)` | `workspace.write(path, content)` |
| `Path(path).read_text()` | `workspace.read(path)` |
| `Path(path).exists()` | `workspace.exists(path)` |
| `os.listdir(path)` / `Path(path).iterdir()` | `workspace.list_dir(path)` |
| `Path(path).mkdir(parents=True)` | `workspace.create_dir(path)` |
| `Path(path).unlink()` | `workspace.remove(path)` |
| `Path(path).is_file()` | `workspace.is_file(path)` |
| `Path(path).is_dir()` | `workspace.is_dir(path)` |

## The Protocol

`Workspace` is a Python `Protocol` defined in `ralph.workspace.protocol`. Production code accepts it via dependency injection; tests substitute `MemoryWorkspace`.

```python
from ralph.workspace import Workspace

def save_prompt_context(workspace: Workspace, content: str) -> None:
    """Write rendered prompt to the standard location."""
    workspace.write(".agent/prompt.md", content)

def load_checkpoint_if_exists(workspace: Workspace) -> dict | None:
    """Return checkpoint dict or None if no checkpoint exists."""
    if not workspace.exists(".agent/checkpoint.json"):
        return None
    return json.loads(workspace.read(".agent/checkpoint.json"))
```

## Implementations

| Class | Import | Purpose |
|-------|--------|---------|
| `FsWorkspace` | `ralph.workspace.fs` | Production — wraps the real filesystem |
| `MemoryWorkspace` | `ralph.workspace.memory` | Tests — all operations stored in a `dict` |
| `WorkspaceScope` | `ralph.workspace.scope` | Scoped view — restricts access to a subtree |

## Testing with MemoryWorkspace

```python
from ralph.workspace.memory import MemoryWorkspace

def test_save_prompt_context_writes_to_correct_path() -> None:
    # Arrange
    ws = MemoryWorkspace()

    # Act
    save_prompt_context(ws, "# Feature\n\nBuild the thing.")

    # Assert
    assert ws.exists(".agent/prompt.md")
    assert "Build the thing." in ws.read(".agent/prompt.md")


def test_load_checkpoint_returns_none_when_missing() -> None:
    ws = MemoryWorkspace()

    result = load_checkpoint_if_exists(ws)

    assert result is None


def test_load_checkpoint_returns_parsed_json() -> None:
    ws = MemoryWorkspace()
    ws.write(".agent/checkpoint.json", '{"phase": "development", "iteration": 2}')

    result = load_checkpoint_if_exists(ws)

    assert result is not None
    assert result["phase"] == "development"
```

Pre-populate the workspace with `ws.write(path, content)` in Arrange. Assert on observable state — file presence, content — not on internal `MemoryWorkspace` attributes.

## Fixtures

`conftest.py` provides a `memory_workspace` fixture pre-populated with a default `PROMPT.md`:

```python
@pytest.fixture
def memory_workspace() -> MemoryWorkspace:
    ws = MemoryWorkspace()
    ws.write("PROMPT.md", "# Test Prompt\n\nThis is a test prompt.")
    return ws
```

Use this fixture for tests that need a workspace with a valid prompt. Build your own `MemoryWorkspace()` inline for tests that control exact file contents.

## Documented Exceptions

The following specific uses of direct `pathlib`/`os` filesystem access are acceptable:

| Location | Reason |
|----------|--------|
| `ralph/workspace/fs.py` (`FsWorkspace`) | This IS the production filesystem implementation |
| `ralph/cli/main.py` (root discovery) | Bootstrap code that locates the repo root before `Workspace` is constructed |
| `ralph/config/loader.py` (TOML loading) | Config loading runs before the workspace is available |
| `ralph/policy/loader.py` (policy loading) | Policy loading runs at startup before workspace is available |
| `tests/` using `tmp_path` | Git system tests that require a real on-disk repository |

All other production code MUST use the `Workspace` protocol.

**When you see `open()` or `Path(...).read_text()` in production code outside these exceptions, it MUST be refactored to use `Workspace`.**
