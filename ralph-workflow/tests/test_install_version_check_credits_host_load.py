from __future__ import annotations

from ralph.install_timeouts import snapshot_version_timeout


def test_an_idle_host_keeps_the_base_version_check_timeout() -> None:
    assert snapshot_version_timeout(0.0) == 60.0


def test_a_saturated_host_stretches_the_version_check_timeout() -> None:
    assert snapshot_version_timeout(1.0) == 240.0


def test_the_stretch_grows_with_load() -> None:
    assert (
        snapshot_version_timeout(0.25)
        < snapshot_version_timeout(0.5)
        < snapshot_version_timeout(0.9)
    )
