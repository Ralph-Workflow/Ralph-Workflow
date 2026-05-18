from __future__ import annotations

import sys
from io import StringIO, TextIOBase
from types import ModuleType, SimpleNamespace

import pytest
from rich.console import Console

from ralph.display import progress
from ralph.display.context import make_display_context
from ralph.display.theme import RALPH_THEME

VALUE_SENTINEL = 123
EXPECTED_COLUMN_COUNT = 7
EXPECTED_TASK_ID = 88
TASK_PARENT = 7
TASK_TOTAL = 8
COMPLETED_VALUE = 20


def _ipython_object() -> object:
    return object()


class _DummyTTY(TextIOBase):

    class _DummyProgressProto:
        def __init__(self) -> None:
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
        def __init__(self) -> None:
            self.n = 0
            self.updated: list[int] = []
            self.refresh_calls = 0

        def update(self, n: int = 1) -> None:
            self.updated.append(n)
            self.n += n

        def close(self) -> None:
            return None

        def refresh(self) -> None:
            self.refresh_calls += 1

    class _DummyRichProgress:
        def __init__(self) -> None:
            self.entered = False
            self.exited = False

        def __enter__(self) -> _DummyRichProgress:
            self.entered = True
            return self

        def __exit__(self, exc_type: object, exc: object, _tb: object) -> bool | None:
            self.exited = True
            return None

    class _DummyTqdmBar:
        def __init__(self) -> None:
            self.closed_calls = 0
            self.closed = False

        def update(self, n: int = 1) -> None:
            return None

        def close(self) -> None:
            self.closed_calls += 1
            self.closed = True

        def refresh(self) -> None:
            return None

    def isatty(self) -> bool:
        return True


_DummyProgressProto = _DummyTTY._DummyProgressProto
_DummyTqdm = _DummyTTY._DummyTqdm
_DummyRichProgress = _DummyTTY._DummyRichProgress
_DummyTqdmBar = _DummyTTY._DummyTqdmBar


def test_module_attr_returns_attribute_and_none() -> None:
    dummy = ModuleType("dummy")
    dummy.__dict__["value"] = VALUE_SENTINEL
    assert progress._module_attr(dummy, "value") == VALUE_SENTINEL
    assert progress._module_attr(dummy, "missing") is None


def test_load_rich_components_returns_factories(monkeypatch: pytest.MonkeyPatch) -> object:
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

    def fake_import(name: str) -> object:
        if name == "rich.console":
            return console_module
        if name == "rich.progress":
            return progress_module
        raise ImportError

    monkeypatch.setattr(progress, "import_module", fake_import)

    result = progress.load_rich_components()
    assert result is not None
    console_factory, progress_factory, columns = result
    assert console_factory(stderr=True) == "console"
    assert progress_factory("spinner") == "progress"
    assert len(columns) == EXPECTED_COLUMN_COUNT


def test_load_rich_components_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "import_module", lambda *_: (_ for _ in ()).throw(ImportError()))
    assert progress.load_rich_components() is None


def test_load_tqdm_factory_returns_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    tqdm_module = SimpleNamespace(tqdm=lambda **kwargs: "bar")
    monkeypatch.setattr(
        progress,
        "import_module",
        lambda name: tqdm_module if name == "tqdm" else SimpleNamespace(),
    )
    factory = progress.load_tqdm_factory()
    assert factory is not None
    assert factory(file=sys.stderr) == "bar"


def test_load_tqdm_factory_returns_none_on_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "import_module", lambda *_: (_ for _ in ()).throw(ImportError()))
    assert progress.load_tqdm_factory() is None


def test_load_get_ipython_various_behaviors(monkeypatch: pytest.MonkeyPatch) -> object:
    # Missing module
    monkeypatch.setattr(progress, "import_module", lambda *_: (_ for _ in ()).throw(ImportError()))
    assert progress.load_get_ipython() is None

    # Module exists but attribute missing
    def import_with_missing(name: str) -> object:
        module = SimpleNamespace()
        return module

    monkeypatch.setattr(progress, "import_module", import_with_missing)
    assert progress.load_get_ipython() is None

    # Module provides non-callable
    def import_with_value(name: str) -> object:
        module = SimpleNamespace(get_ipython=123)
        return module

    monkeypatch.setattr(progress, "import_module", import_with_value)
    assert progress.load_get_ipython() is None

    # Module returns callable that yields object
    def fake_get_ipython() -> object:
        return object()

    def import_with_callable(name: str) -> object:
        module = SimpleNamespace(get_ipython=fake_get_ipython)
        return module

    monkeypatch.setattr(progress, "import_module", import_with_callable)
    assert progress.load_get_ipython() is not None


def test_ralph_progress_check_jupyter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "IPYTHON_AVAILABLE", True)
    monkeypatch.setattr(progress, "GET_IPYTHON", _ipython_object)
    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
    assert rp._check_jupyter()

    # When IPython returns None
    monkeypatch.setattr(progress, "GET_IPYTHON", lambda: None)
    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
    assert not rp._check_jupyter()

    # When IPython accessor raises
    def raising() -> None:
        raise RuntimeError

    monkeypatch.setattr(progress, "GET_IPYTHON", raising)
    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
    assert not rp._check_jupyter()


def test_ralph_progress_is_tty_considers_rich_and_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "RICH_AVAILABLE", True)
    monkeypatch.setattr(sys, "stderr", _DummyTTY())
    ctx = make_display_context()
    assert progress.RalphProgress(ctx)._is_tty()

    monkeypatch.setattr(progress, "RICH_AVAILABLE", False)
    assert not progress.RalphProgress(ctx)._is_tty()


def test_rich_progress_context_manager_sets_state(monkeypatch: pytest.MonkeyPatch) -> object:
    """Test that _rich_progress uses context console and correct progress settings."""
    dummy = _DummyRichProgress()
    captured_args: dict[str, object] = {}

    def progress_factory(
        *args: object, console: object | None = None, **kwargs: object
    ) -> _DummyRichProgress:
        captured_args["console"] = console
        captured_args["kwargs"] = kwargs
        assert kwargs.get("transient") is False
        assert kwargs.get("auto_refresh") is True
        return dummy

    def make_column(index: int) -> object:
        return lambda *args, **kwargs: f"column-{index}"

    columns = tuple(make_column(i) for i in range(7))
    monkeypatch.setattr(
        progress,
        "load_rich_components",
        lambda: (None, progress_factory, columns),
    )

    # Create a context with a specific console
    buf = StringIO()
    shared_console = Console(file=buf, force_terminal=False, width=120, theme=RALPH_THEME)
    ctx = make_display_context(console=shared_console, env={})

    rp = progress.RalphProgress(ctx)
    with rp._rich_progress() as manager:
        assert manager is dummy
        assert rp._progress is dummy
        # The console should be the one from the context
        assert captured_args["console"] is shared_console

    assert dummy.exited
    assert rp._progress is None
    assert rp._console is None


def test_rich_progress_uses_injected_console_when_context_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> object:
    """When a context is supplied, its console is reused rather than creating a new one."""
    buf = StringIO()
    shared_console = Console(file=buf, force_terminal=False, width=120, theme=RALPH_THEME)
    ctx = make_display_context(console=shared_console, env={"COLUMNS": "120"})

    captured_console: list[object] = []

    def progress_factory(
        *args: object, console: object | None = None, **kwargs: object
    ) -> _DummyRichProgress:
        captured_console.append(console)
        dummy = _DummyRichProgress()
        return dummy

    def make_column(index: int) -> object:
        return lambda *args, **kwargs: f"column-{index}"

    columns = tuple(make_column(i) for i in range(7))
    monkeypatch.setattr(
        progress,
        "load_rich_components",
        lambda: (None, progress_factory, columns),
    )

    rp = progress.RalphProgress(context=ctx)
    with rp._rich_progress():
        pass

    assert len(captured_console) == 1
    assert captured_console[0] is shared_console


def test_rich_progress_context_manager_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "load_rich_components", lambda: None)
    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
    with pytest.raises(RuntimeError, match="rich is unavailable"), rp._rich_progress():
        pass


def test_tqdm_progress_context_manager_sets_and_clears(monkeypatch: pytest.MonkeyPatch) -> None:
    bar = _DummyTqdmBar()
    monkeypatch.setattr(progress, "load_tqdm_factory", lambda: lambda **kwargs: bar)

    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
    with rp._tqdm_progress() as manager:
        assert manager is bar
        assert rp._tqdm is bar

    assert bar.closed
    assert bar.closed_calls == 1
    assert rp._tqdm is None


def test_tqdm_progress_context_manager_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress, "load_tqdm_factory", lambda: None)
    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
    with pytest.raises(RuntimeError, match="tqdm is unavailable"), rp._tqdm_progress():
        pass


def test_phase_manager_with_and_without_progress() -> None:
    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
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


def test_add_task_respects_progress_and_dummy_fallback() -> None:
    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
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


def test_update_uses_rich_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
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


def test_update_uses_tqdm_fallback() -> None:
    ctx = make_display_context()
    rp = progress.RalphProgress(ctx)
    rp._progress = None
    rp._tqdm = _DummyTqdm()
    rp.update(task=TASK_PARENT, completed=COMPLETED_VALUE, advance=4)
    assert rp._tqdm.updated == [4]
    assert rp._tqdm.n == COMPLETED_VALUE
    assert rp._tqdm.refresh_calls == 1


def test_get_progress_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(progress.ProgressSingleton, "instances", {})
    ctx = make_display_context()
    first = progress.get_progress(ctx)
    second = progress.get_progress(ctx)
    assert first is second


def test_get_progress_different_context_yields_different_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Different contexts (by console identity) produce separate instances."""
    monkeypatch.setattr(progress.ProgressSingleton, "instances", {})
    buf1 = StringIO()
    buf2 = StringIO()
    console1 = Console(file=buf1, force_terminal=False, width=120, theme=RALPH_THEME)
    console2 = Console(file=buf2, force_terminal=False, width=120, theme=RALPH_THEME)
    ctx1 = make_display_context(console=console1, env={"COLUMNS": "120"})
    ctx2 = make_display_context(console=console2, env={"COLUMNS": "120"})
    p1 = progress.get_progress(ctx1)
    p2 = progress.get_progress(ctx2)
    p1_again = progress.get_progress(ctx1)
    assert p1 is not p2
    assert p1 is p1_again
