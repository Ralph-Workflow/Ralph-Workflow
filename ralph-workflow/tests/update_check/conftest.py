from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def _isolate_update_check_user_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    home = tmp_path / "update-check-home"
    config = home / ".config"
    cache = home / ".cache"
    config.mkdir(parents=True)
    cache.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    monkeypatch.setattr(Path, "home", lambda _home=home: _home)

    from ralph.skills import state_store

    capabilities = config / "ralph-workflow-capabilities.json"
    monkeypatch.setattr(state_store, "DEFAULT_STATE_PATH", capabilities)
    monkeypatch.setattr(state_store, "default_state_path", lambda: capabilities)
    yield
