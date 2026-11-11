
from __future__ import annotations

__all__ = ["app"]


def __getattr__(name: str) -> object:
    if name == "app":
        from ralph.cli.main import app as cli_app

        return cli_app
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
