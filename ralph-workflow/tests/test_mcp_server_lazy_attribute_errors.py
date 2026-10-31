"""``ralph.mcp.server``'s lazy attribute hook must not mask a broken submodule.

The package's PEP 562 ``__getattr__`` falls back to importing
``ralph.mcp.server.<name>``. It used to swallow every ``ImportError`` from
that import, so a submodule that EXISTS but fails while importing -- for
instance on a circular import -- surfaced only as ``AttributeError: module
'ralph.mcp.server' has no attribute ...``, and the real cause was lost. Only a
submodule that genuinely does not exist may become ``AttributeError``.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import sys
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.server

if TYPE_CHECKING:
    from collections.abc import Sequence

_BROKEN = "ralph.mcp.server.broken_for_lazy_getattr_test"
_BROKEN_MESSAGE = "cannot import name 'x' from partially initialized module 'y'"


class _BrokenSubmoduleLoader(importlib.abc.Loader):
    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        del spec
        return None

    def exec_module(self, module: ModuleType) -> None:
        raise ImportError(_BROKEN_MESSAGE, name=module.__name__)


class _BrokenSubmoduleFinder(importlib.abc.MetaPathFinder):
    def find_spec(
        self,
        fullname: str,
        path: Sequence[str] | None,
        target: ModuleType | None = None,
    ) -> importlib.machinery.ModuleSpec | None:
        del path, target
        if fullname != _BROKEN:
            return None
        return importlib.machinery.ModuleSpec(fullname, _BrokenSubmoduleLoader())


@pytest.fixture
def broken_submodule(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(sys, "meta_path", [_BrokenSubmoduleFinder(), *sys.meta_path])
    monkeypatch.delitem(sys.modules, _BROKEN, raising=False)
    return _BROKEN.rpartition(".")[2]


def test_existing_submodule_that_fails_to_import_raises_its_import_error(
    broken_submodule: str,
) -> None:
    with pytest.raises(ImportError, match="partially initialized module"):
        getattr(ralph.mcp.server, broken_submodule)


def test_absent_name_still_raises_attribute_error() -> None:
    absent = "no_such_submodule_here"
    with pytest.raises(AttributeError, match=f"has no attribute '{absent}'"):
        getattr(ralph.mcp.server, absent)
