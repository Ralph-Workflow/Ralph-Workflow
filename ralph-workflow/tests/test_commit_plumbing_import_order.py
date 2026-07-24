"""Clean import-order regression for commit plumbing."""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from typing import Protocol, cast

_CLI_MODULE = "ralph.cli.commands.commit"
_PLUMBING_MODULE = "ralph.pipeline.plumbing.commit_plumbing"


class _CommitPlumbingModule(Protocol):
    CommitAgentResult: type[object]


def _loaded_module(name: str) -> ModuleType | None:
    module = sys.modules.get(name)
    return module if isinstance(module, ModuleType) else None


def test_commit_plumbing_is_directly_importable() -> None:
    """The pipeline module must not require its CLI facade to be imported first."""
    plumbing_package = importlib.import_module("ralph.pipeline.plumbing")
    cli_package = importlib.import_module("ralph.cli.commands")
    saved_modules = {
        name: module
        for name in (_CLI_MODULE, _PLUMBING_MODULE)
        if (module := _loaded_module(name)) is not None
    }

    try:
        sys.modules.pop(_CLI_MODULE, None)
        sys.modules.pop(_PLUMBING_MODULE, None)
        imported = cast(
            "_CommitPlumbingModule",
            importlib.import_module(_PLUMBING_MODULE),
        )
        result_type = imported.CommitAgentResult
        assert result_type.__name__ == "CommitAgentResult"
    finally:
        sys.modules.pop(_CLI_MODULE, None)
        sys.modules.pop(_PLUMBING_MODULE, None)
        for name, module in saved_modules.items():
            sys.modules[name] = module
        for package, attribute, module_name in (
            (plumbing_package, "commit_plumbing", _PLUMBING_MODULE),
            (cli_package, "commit", _CLI_MODULE),
        ):
            saved_module = saved_modules.get(module_name)
            if saved_module is None:
                if hasattr(package, attribute):
                    delattr(package, attribute)
            else:
                setattr(package, attribute, saved_module)
