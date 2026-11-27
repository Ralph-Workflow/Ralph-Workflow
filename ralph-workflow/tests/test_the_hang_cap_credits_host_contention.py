from __future__ import annotations

from tests._per_test_budget import contended_wall_cap


def test_an_idle_host_keeps_the_plain_hang_cap() -> None:
    assert contended_wall_cap(5.0, load=4.0, cpus=16) == 5.0


def test_a_contended_host_stretches_the_hang_cap_by_its_load_per_core() -> None:
    assert contended_wall_cap(5.0, load=64.0, cpus=16) == 20.0


def test_the_stretch_is_bounded_so_a_real_hang_is_still_caught() -> None:
    assert contended_wall_cap(5.0, load=1000.0, cpus=16) == 40.0
