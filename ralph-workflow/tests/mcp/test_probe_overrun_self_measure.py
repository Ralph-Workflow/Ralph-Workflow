"""WU-Probe overrun self-measurement: probe exceeding deadline + slack is C3 contention.

These tests use the bridge's ``clock_fn`` injection seam so each
``_run_health_probe`` call advances the fake clock by the simulated
probe latency. A 30-iteration deferral-ceiling cycle completes in
microseconds of real time instead of 33 real seconds.

The probe function itself returns normally after a bounded amount of
"fake" work; the bridge's own clock injection advances the monotonic
clock by the simulated latency so the overrun arithmetic exercises the
production code path without spinning a CPU.
"""

from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Callable
from datetime import timedelta

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_health_event import (
    _clear_host_contention_signals,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server.lifecycle import (
    _DEFERRAL_COUNT_CEILING,
    _PROBE_DEADLINE_SLACK_S,
)

pytestmark = [pytest.mark.timeout_seconds(10)]


class _FakeClock:
    """Mutable monotonic-clock substitute used by the probe-overrun tests.

    Each :meth:`advance` call moves the clock forward by the supplied
    delta. The bridge's ``_run_health_probe`` calls ``clock_fn()`` twice
    per call (start and end), so a single ``advance`` between those
    reads is the smallest expression of "the probe took <delta> seconds".
    ``time.monotonic`` would normally be the production default; this
    fake replaces it so the tests run in microseconds.
    """

    def __init__(self, *, start: float = 1000.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> float:
        self._now += delta
        return self._now


class _FakeProcess:
    def __init__(self, poll_result: int | None = None, pid: int = 9999) -> None:
        self._poll_result = poll_result
        self._pid = pid

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        return None


def _make_inner(poll_result: int | None = None) -> lifecycle.StandaloneMcpProcess:
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=_FakeProcess(poll_result=poll_result),
        session_file=session_file,
    )


def _make_overrun_clock_probe(
    clock: _FakeClock,
    *,
    simulated_latency_s: float,
    raise_after: bool = True,
) -> Callable[[str, timedelta], None]:
    """Return a probe that advances the fake clock then either returns or raises.

    Real production probes raise (e.g. ``TimeoutError`` from
    ``urllib.request.urlopen(..., timeout=...)``) when their bounded
    timeout fires. The bridge only computes ``overrun`` in the
    ``except Exception`` arm so the raising variant is what exercises
    the overrun arithmetic. Setting ``raise_after=False`` returns
    normally instead (used by tests that exercise the success branch).
    """

    def probe(endpoint: str, timeout: timedelta) -> None:
        clock.advance(simulated_latency_s)
        if raise_after:
            raise RuntimeError("probe timed out")

    return probe


def _make_raising_overrun_clock_probe(
    clock: _FakeClock,
    *,
    simulated_latency_s: float,
) -> Callable[[str, timedelta], None]:
    """Same as :func:`_make_overrun_clock_probe` but raises after the advance.

    Real-world production probes raise (``TimeoutError`` from
    ``urllib.request.urlopen(..., timeout=...)``) when their bounded
    timeout fires. This variant mirrors that contract so the bridge's
    ``except Exception as exc`` arm sees a real overrun exception
    instead of an empty success-return that would short-circuit the
    overrun classification.
    """

    def probe(endpoint: str, timeout: timedelta) -> None:
        clock.advance(simulated_latency_s)
        raise RuntimeError("probe timed out")

    return probe


@pytest.fixture(autouse=True)
def _isolate_contention_signals():
    _clear_host_contention_signals()
    yield
    _clear_host_contention_signals()


# ----------------------------------------------------------------------
# Test 1: probe that completes past deadline + slack yields overrun=True
# ----------------------------------------------------------------------
def test_probe_overrun_returns_true_when_probe_exceeds_deadline_plus_slack():
    """A probe whose fake-elapsed time exceeds deadline + slack has overrun=True."""
    base_deadline = 0.5  # seconds
    slack = _PROBE_DEADLINE_SLACK_S
    simulated_latency_s = base_deadline + slack + 1.0  # 2.0s fake elapsed
    clock = _FakeClock()
    # raise_after=True mirrors the real production behaviour
    # where the probe raises on overrun (urllib.request.urlopen with
    # timeout=... raises TimeoutError). The bridge's overrun
    # arithmetic only fires in the except arm, so a non-raising
    # probe is treated as "server alive, no overrun" even at the
    # boundary.
    probe = _make_overrun_clock_probe(
        clock, simulated_latency_s=simulated_latency_s, raise_after=True
    )
    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe,
        probe_timeout_fn=lambda: timedelta(seconds=base_deadline),
        preflight_timeout_fn=lambda: timedelta(seconds=10.0),
        clock_fn=clock,
    )

    probe_failed, _pd, infra_error, overrun = bridge._run_health_probe()

    assert probe_failed is True
    assert infra_error is False
    assert overrun is True, (
        f"expected overrun=True when fake-elapsed probe time "
        f"({simulated_latency_s:.2f}s) exceeds deadline+slack "
        f"({base_deadline + slack}s), got overrun={overrun}"
    )
    assert _pd == pytest.approx(simulated_latency_s)


# ----------------------------------------------------------------------
# Test 2: probe that fails within deadline has overrun=False
# ----------------------------------------------------------------------
def test_probe_returns_overrun_false_when_failing_fast():
    """A probe that raises quickly (within deadline) has overrun=False."""
    base_deadline = 2.0
    clock = _FakeClock()

    def fast_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("fast fail")

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=fast_fail_probe,
        probe_timeout_fn=lambda: timedelta(seconds=base_deadline),
        preflight_timeout_fn=lambda: timedelta(seconds=10.0),
        clock_fn=clock,
    )

    probe_failed, _pd, infra_error, overrun = bridge._run_health_probe()

    assert probe_failed is True
    assert infra_error is False
    assert overrun is False


# ----------------------------------------------------------------------
# Test 3: deferral counter increments when overrun probe has contention signal
# ----------------------------------------------------------------------
def test_deferral_counter_increments_on_overrun_with_contention_signal():
    """A raising overrun probe with a contention signal increments the deferred counter."""
    base_deadline = 0.5
    slack = _PROBE_DEADLINE_SLACK_S
    simulated_latency_s = base_deadline + slack + 0.1
    clock = _FakeClock()
    probe = _make_raising_overrun_clock_probe(clock, simulated_latency_s=simulated_latency_s)
    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe,
        probe_timeout_fn=lambda: timedelta(seconds=base_deadline),
        preflight_timeout_fn=lambda: timedelta(seconds=10.0),
        contention_signal_producer=lambda: True,
        clock_fn=clock,
    )

    for i in range(1, 4):
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == i, (
            f"after {i} overrun probe(s) with contention signal, "
            f"deferred count should be {i}, got "
            f"{bridge._consecutive_probe_failures_deferred}"
        )

    assert bridge._restart_count == 0


# ----------------------------------------------------------------------
# Test 4: E6.2 -- probe overrun is contention, and the deferral ceiling is
# the backstop that stops a genuinely hung server from being deferred forever
# ----------------------------------------------------------------------
def test_e62_probe_overrun_is_contention_and_deferral_ceiling_is_the_backstop():
    """E6.2 behaviourally: an overrun probe is CONTENTION, never a server verdict.

    Both halves of the catalogue entry are proved here, in one run:

    * **Overrun is contention, not a server failure.** A probe whose elapsed
      time exceeds ``timeout + _PROBE_DEADLINE_SLACK_S`` is classified with
      ``overrun=True`` and ``infra_error=False`` -- the bridge records a
      *timing* verdict, not a *server is broken* verdict -- and, while the child
      is alive and host contention is signalled, ``check_health_and_restart_if_needed``
      DEFERS instead of restarting. Without the slack band a slow-but-healthy
      server would be torn down on a scheduling hiccup.

    * **A genuinely hung server is NOT contention forever.** The simulated
      child is alive in the kernel (``poll()`` is ``None`` -- the same shape as
      the SIGSTOPped child of E5.3) but never answers a probe: the hung-server
      shape, not a transient hiccup. ``_DEFERRAL_COUNT_CEILING`` is therefore
      the backstop that keeps the slack band from becoming an immortal
      exemption: once the ceiling is reached the next failure forces a restart
      even though the contention signal is still fresh and still the child is
      still "alive".
    """
    base_deadline = 0.5
    slack = _PROBE_DEADLINE_SLACK_S
    ceiling = _DEFERRAL_COUNT_CEILING
    simulated_latency_s = base_deadline + slack + 0.1
    clock = _FakeClock()
    probe = _make_raising_overrun_clock_probe(clock, simulated_latency_s=simulated_latency_s)
    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe,
        probe_timeout_fn=lambda: timedelta(seconds=base_deadline),
        preflight_timeout_fn=lambda: timedelta(seconds=10.0),
        contention_signal_producer=lambda: True,
        clock_fn=clock,
    )

    # Half 1: the overrun is classified as contention, NOT as a server failure.
    probe_failed, _pd, infra_error, overrun = bridge._run_health_probe()
    assert probe_failed is True
    assert overrun is True, (
        f"probe elapsed {simulated_latency_s:.2f}s past a {base_deadline}s deadline "
        f"(+{slack}s slack) must be classified as an overrun"
    )
    assert infra_error is False, (
        "an overrun is a timing/contention verdict, never an infrastructure "
        f"(server-failure) verdict; got infra_error={infra_error}"
    )

    # A hung-but-alive child under an overrun probe defers instead of restarting.
    for _ in range(ceiling):
        assert bridge.check_health_and_restart_if_needed() is False, (
            "an overrun probe on an alive child must defer (contention), not restart"
        )
    assert bridge._consecutive_probe_failures_deferred == ceiling
    assert bridge._restart_count == 0, (
        f"the slack band restarted a hung-but-alive server after {ceiling} overruns; it must defer"
    )

    # Half 2: the ceiling is the backstop -- contention does not defer forever.
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True, (
        f"expected restart=True when deferral count hits ceiling ({ceiling}), got {restarted}"
    )
    assert bridge._restart_count == 1
    assert bridge._consecutive_probe_failures_deferred == 0
