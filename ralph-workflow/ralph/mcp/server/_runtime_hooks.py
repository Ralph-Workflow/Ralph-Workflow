"""Runtime lifecycle hook registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

_SHUTDOWN_HOOKS: list[Callable[[], None]] = []  # bounded-accumulator-ok: registration hooks only
_OWNER_LOSS_HOOKS: list[Callable[[], None]] = []  # bounded-accumulator-ok: registration hooks only


def register_shutdown_hook(hook: Callable[[], None]) -> None:
    """Register a callback to run on standalone MCP server shutdown."""
    if hook not in _SHUTDOWN_HOOKS:
        _SHUTDOWN_HOOKS.append(hook)


def register_owner_loss_hook(hook: Callable[[], None]) -> None:
    """Register a callback to run before exit when the owner process is lost."""
    if hook not in _OWNER_LOSS_HOOKS:
        _OWNER_LOSS_HOOKS.append(hook)
