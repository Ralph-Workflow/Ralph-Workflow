from __future__ import annotations

import os
from collections.abc import Generator

import pytest

from tests._scenario_git import SCENARIO_GIT_IDENTITY_ENV


@pytest.fixture(autouse=True)
def _scenario_git_identity() -> Generator[None, None, None]:
    saved = {key: os.environ.get(key) for key in SCENARIO_GIT_IDENTITY_ENV}
    os.environ.update(SCENARIO_GIT_IDENTITY_ENV)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
