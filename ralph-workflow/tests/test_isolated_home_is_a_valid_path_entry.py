from __future__ import annotations

import os

import pytest


@pytest.mark.parametrize("variant", ["plain", "with:colon"])
def test_the_isolated_home_can_be_placed_on_path(variant: str) -> None:
    del variant
    home = os.environ["HOME"]

    assert os.pathsep not in home, home
    assert "[" not in home, home
    assert "]" not in home, home


def test_the_isolated_config_home_can_be_placed_on_path() -> None:
    assert os.pathsep not in os.environ["XDG_CONFIG_HOME"]
