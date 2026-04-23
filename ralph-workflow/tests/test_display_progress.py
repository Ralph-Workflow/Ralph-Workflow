from __future__ import annotations

import sys
from io import TextIOBase
from types import ModuleType, SimpleNamespace

import pytest

from ralph.display import progress

VALUE_SENTINEL = 123
EXPECTED_COLUMN_COUNT = 7
EXPECTED_TASK_ID = 88
TASK_PARENT = 7
TASK_TOTAL = 8
COMPLETED_VALUE = 20


def _ipython_object() -> object:
    return object()


class _DummyProgressProto:
    def __init__(self):
        self.add_calls: list[dict[str, object | None]] = []
        self.update_calls: list[dict[str, object | None]] = []

    def add_task(
        self,
        description: str,
        *,
        parent: int | None = None,
        total: int | None = None,
        completed: int = 0,
    ) -> int:
        self.add_calls.append(
            {
                "description": description,
                "parent": parent,
                "total": total,
                "completed": completed,
            }
        )
        return 88

    def __enter__(self) -> _DummyProgressProto:
        return self

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> bool | None:
        return None

    def update(
        self,
        *,
        task_id: progress.TaskID,
        completed: int | None = None,
        advance: int | None = None,
        description: str | None = None,
    ) -> None:
        self.update_calls.append(
            {
                "task_id": task_id,
                "completed": completed,
                "advance": advance,
                "description": description,
            }
        )


class _DummyTqdm:
    def __init__(self):
        self.n = 0
        self.updated: list[int] = []
        self.refresh_calls = 0

    def update(self, n: int = 1):
        self.updated.append(n)
        self.n += n

    def close(self) -> None:
        return None

    def refresh(self) -> None:
        self.refresh_calls += 1


class _DummyRichProgress:
    def __init__(self):
        self.entered = False
        self.exited = False

    def __enter__(self) -> _DummyRichProgress:
        self.entered = True
        return self

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> bool | None:
        self.exited = True
        return None


class _DummyTqdmBar:
    def __init__(self):
        self.closed_calls = 0
        self.closed = False

    def update(self, n: int = 1) -> None:
        return None

    def close(self) -> None:
        self.closed_calls += 1
        self.closed = True

    def refresh(self) -> None:
        return None


class _DummyTTY(TextIOBase):
    def isatty(self) -> bool:
        return True


def test_module_attr_returns_attribute_and_none():
    dummy = ModuleType("dummy")
    dummy.__dict__["value"] = VALUE_SENTINEL
    assert progress._module_attr(dummy, "value") == VALUE_SENTINEL
    assert progress._module_attr(dummy, "missing") is None


def test_load_rich_components_returns_factories(monkeypatch):
    console_module = SimpleNamespace(Console=lambda *args, **kwargs: "console")
    progress_module = SimpleNamespace(
        Progress=lambda *args, **kwargs: "progress",
        SpinnerColumn=lambda: "spinner",
        TextColumn=lambda *args, **kwargs: "text",
        BarColumn=lambda: "bar",
        TaskProgressColumn=lambda: "task-progress",
        MofNCompleteColumn=lambda: "mofn",
        TimeElapsedColumn=lambda: "elapsed",
        TimeRemainingColumn=lambda: "remaining",
    )

    def fake_import(name: str):
        if name == "rich.console":
            return console_module
        if name == "rich.progress":
            return progress_module
        raise ImportError

    monkeypatch.setattr(progress, "import_module", fake_import)

    result = progress._load_rich_components()
    assert result is not None
    console_factory, progress_factory, columns = result
    assert console_factory(stderr=True) == "console"
    assert progress_factory("spinner") == "progress"
    assert len(columns) == EXPECTED_COLUMN_COUNT


def test_load_rich_components_returns_none_when_missing(monkeypatch):
    monkeypatch.setattr(progress, "import_module", lambda *_: (_ for _ in ()).throw(ImportError()))
    assert progress._load_rich_components() is None


def test_load_tqdm_factory_returns_factory(monkeypatch):
    tqdm_module = SimpleNamespace(tqdm=lambda **kwargs: "bar")
    monkeypatch.setattr(
        progress,
        "import_module",
        lambda name: tqdm_module if name == "tqdm" else SimpleNamespace(),
    )
    factory = progress._load_tqdm_factory()
    assert factory is not None
    assert factory(file=sys.stderr) == "bar"


def test_load_tqdm_factory_returns_none_on_import_error(monkeypatch):
    monkeypatch.setattr(progress, "import_module", lambda *_: (_ for _ in ()).throw(ImportError()))
    assert progress._load_tqdm_factory() is None


def test_load_get_ipython_various_behaviors(monkeypatch):
    # Missing module
    monkeypatch.setattr(progress, "import_module", lambda *_: (_ for _ in ()).throw(ImportError()))
    assert progress._load_get_ipython() is None

    # Module exists but attribute missing
    def import_with_missing(name: str):
        module = SimpleNamespace()
        return module

    monkeypatch.setattr(progress, "import_module", import_with_missing)
    assert progress._load_get_ipython() is None

    # Module provides non-callable
    def import_with_value(name: str):
        module = SimpleNamespace(get_ipython=123)
        return module

    monkeypatch.setattr(progress, "import_module", import_with_value)
    assert progress._load_get_ipython() is None

    # Module returns callable that yields object
    def fake_get_ipython():
        return object()

    def import_with_callable(name: str):
        module = SimpleNamespace(get_ipython=fake_get_ipython)
        return module

    monkeypatch.setattr(progress, "import_module", import_with_callable)
    assert progress._load_get_ipython() is not None


def test_ralph_progress_check_jupyter(monkeypatch):
    monkeypatch.setattr(progress, "_IPYTHON_AVAILABLE", True)
    monkeypatch.setattr(progress, "_GET_IPYTHON", _ipython_object)
    rp = progress.RalphProgress()
    assert rp._check_jupyter()

    # When IPython returns None
    monkeypatch.setattr(progress, "_GET_IPYTHON", lambda: None)
    rp = progress.RalphProgress()
    assert not rp._check_jupyter()

    # When IPython accessor raises
    def raising():
        raise RuntimeError

    monkeypatch.setattr(progress, "_GET_IPYTHON", raising)
    rp = progress.RalphProgress()
    assert not rp._check_jupyter()


def test_ralph_progress_is_tty_considers_rich_and_stderr(monkeypatch):
    monkeypatch.setattr(progress, "_RICH_AVAILABLE", True)
    monkeypatch.setattr(sys, "stderr", _DummyTTY())
    assert progress.RalphProgress()._is_tty()

    monkeypatch.setattr(progress, "_RICH_AVAILABLE", False)
    assert not progress.RalphProgress()._is_tty()


def test_rich_progress_context_manager_sets_state(monkeypatch):
    dummy = _DummyRichProgress()

    def console_factory(*, stderr: bool, theme: object = None) -> str:
        assert stderr
        return "console"

    def progress_factory(
        *args: object, console: object | None = None, **kwargs: object
    ) -> _DummyRichProgress:
        assert console == "console"
        assert kwargs.get("transient") is False
        assert kwargs.get("auto_refresh") is True
        return dummy

    def make_column(index: int):
        return lambda *args, **kwargs: f"column-{index}"

    columns = tuple(make_column(i) for i in range(7))
    monkeypatch.setattr(
        progress,
        "_load_rich_components",
        lambda: (console_factory, progress_factory, columns),
    )

    rp = progress.RalphProgress()
    with rp._rich_progress() as manager:
        assert manager is dummy
        assert rp._progress is dummy
        assert rp._console == "console"

    assert dummy.exited
    assert rp._progress is None
    assert rp._console is None


def test_rich_progress_context_manager_raises_when_missing(monkeypatch):
    monkeypatch.setattr(progress, "_load_rich_components", lambda: None)
    rp = progress.RalphProgress()
    with pytest.raises(RuntimeError, match="rich is unavailable"), rp._rich_progress():
        pass


def test_tqdm_progress_context_manager_sets_and_clears(monkeypatch):
    bar = _DummyTqdmBar()
    monkeypatch.setattr(progress, "_load_tqdm_factory", lambda: lambda **kwargs: bar)

    rp = progress.RalphProgress()
    with rp._tqdm_progress() as manager:
        assert manager is bar
        assert rp._tqdm is bar

    assert bar.closed
    assert bar.closed_calls == 1
    assert rp._tqdm is None


def test_tqdm_progress_context_manager_raises_when_missing(monkeypatch):
    monkeypatch.setattr(progress, "_load_tqdm_factory", lambda: None)
    rp = progress.RalphProgress()
    with pytest.raises(RuntimeError, match="tqdm is unavailable"), rp._tqdm_progress():
        pass


def test_phase_manager_with_and_without_progress():
    rp = progress.RalphProgress()
    stub = _DummyProgressProto()
    rp._progress = stub
    with rp.phase(12, "Phase") as phase_ctx:
        assert phase_ctx is stub
    description = stub.add_calls[0]["description"]
    assert isinstance(description, str)
    assert description.strip().endswith("Phase")

    rp._progress = None
    with rp.phase(5, "NoProgress") as phase_ctx:
        assert phase_ctx is None


def test_add_task_respects_progress_and_dummy_fallback():
    rp = progress.RalphProgress()
    stub = _DummyProgressProto()
    rp._progress = stub
    task_id = rp.add_task("Task", total=TASK_TOTAL, completed=2, parent=TASK_PARENT)
    assert task_id == EXPECTED_TASK_ID
    last_call = stub.add_calls[-1]
    assert last_call["description"] == "Task"
    assert last_call["parent"] == TASK_PARENT
    assert last_call["total"] == TASK_TOTAL

    rp._progress = None
    assert rp.add_task("NoRich") == 0


def test_update_uses_rich_when_available(monkeypatch):
    rp = progress.RalphProgress()
    stub = _DummyProgressProto()
    rp._progress = stub
    rp.update(task=1, completed=5, advance=3, description="desc")
    assert stub.update_calls == [
        {
            "task_id": 1,
            "completed": 5,
            "advance": 3,
            "description": "desc",
        }
    ]


def test_update_uses_tqdm_fallback():
    rp = progress.RalphProgress()
    rp._progress = None
    rp._tqdm = _DummyTqdm()
    rp.update(task=TASK_PARENT, completed=COMPLETED_VALUE, advance=4)
    assert rp._tqdm.updated == [4]
    assert rp._tqdm.n == COMPLETED_VALUE
    assert rp._tqdm.refresh_calls == 1


def test_get_progress_singleton(monkeypatch):
    monkeypatch.setattr(progress._ProgressSingleton, "_instance", None)
    first = progress.get_progress()
    second = progress.get_progress()
    assert first is second
