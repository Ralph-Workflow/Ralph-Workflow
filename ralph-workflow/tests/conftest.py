"""Shared fixtures for ralph test suite."""

from __future__ import annotations

import os
import shutil
import signal
import sys
import tempfile
import threading
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from _pytest.unraisableexception import gc_collect_iterations_key
from git import Repo
from typer.testing import CliRunner

from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.pipeline import effect_executor
from ralph.pipeline._runner_session import set_last_captured_session_id
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    RecoveryPolicy,
)
from ralph.runtime import (
    DEFAULT_TEST_TIMEOUT_SECONDS,
    TEST_TIMEOUT_ENV,
    timeout_seconds_from_env,
)
from ralph.test_suites import (
    REQUIRED_AUTO_INTEGRATE_E2E_FILES,
)
from ralph.workspace.memory import MemoryWorkspace
from tests.integration._mock_agent_invoker import MockAgentInvoker

pytest_plugins = ("ralph.testing.pytest_timeout_plugin",)
_REQUIRED_AUTO_INTEGRATE_E2E_PATHS = frozenset(REQUIRED_AUTO_INTEGRATE_E2E_FILES) | {
    # wt-18 WU-3: the ladder adapter's real-git regression (config-first
    # argv ordering, compare-and-swap land) must stay on the default
    # ``make test`` profile so the fail-closed probe cannot rot behind
    # the opt-in subprocess gate.
    "tests/test_integration_ladder_real_git.py",
}
_AGY_DEFAULT_TEST_PATHS = frozenset(
    {
        "tests/test_agy_pty_invocation.py",
        "tests/test_agy_runtime_home_auth.py",
    }
)


@pytest.fixture(autouse=True)
def _placeholder_hosted_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep fake agent-process tests independent of real provider credentials."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-placeholder")
    monkeypatch.setenv("OPENAI_API_KEY", "test-placeholder")
    monkeypatch.delenv("RALPH_MCP_ENDPOINT", raising=False)
    monkeypatch.delenv("RALPH_MCP_RUN_ID", raising=False)
    monkeypatch.delenv("RALPH_MCP_INHERITED_FD", raising=False)
    monkeypatch.delenv("RALPH_MCP_RESPAWN", raising=False)


@pytest.fixture(autouse=True)
def _disable_explore_recovery_drain_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic against the background recovery drain.

    ``enqueue_recovery`` starts a daemon drain thread in production;
    tests assert the pending queue synchronously and drain it via
    ``run_pending_recovery`` themselves, so the thread is stubbed out.
    """
    from ralph.mcp.explore import recovery

    monkeypatch.setattr(recovery, "_ensure_drain_thread", lambda: None)


@pytest.fixture(autouse=True)
def _isolate_explore_recovery_globals() -> Generator[None, None, None]:
    """Snapshot and restore the process-global recovery queue per test.

    ``recovery._PENDING_RECOVERY`` is a module-level dict shared across
    every test in the process. Earlier tests enqueue entries (via
    ``handle_ralph_index_status`` cold-detection and the fault-matrix
    suites) without clearing them, so a later test that calls
    ``_drain_pending_recoveries_once`` sees every queued workspace
    drained together (the S-2 batch failure: expected ``attempted == 1``,
    observed 25). Snapshot the dict and event state before each test,
    clear them, then restore on teardown so each test observes an
    isolated queue. The drain thread itself is already stubbed by
    ``_disable_explore_recovery_drain_thread`` above, so no background
    work can race the snapshot/restore.
    """
    from ralph.mcp.explore import recovery

    with recovery._PENDING_LOCK:
        pending_snapshot = recovery._PENDING_RECOVERY.copy()
    wakeup_was_set = recovery._DRAIN_WAKEUP.is_set()
    with recovery._PENDING_LOCK:
        recovery._PENDING_RECOVERY.clear()
    recovery._DRAIN_WAKEUP.clear()
    try:
        yield
    finally:
        with recovery._PENDING_LOCK:
            recovery._PENDING_RECOVERY.clear()
            recovery._PENDING_RECOVERY.update(pending_snapshot)
        if wakeup_was_set:
            recovery._DRAIN_WAKEUP.set()
        else:
            recovery._DRAIN_WAKEUP.clear()


@pytest.fixture(autouse=True)
def _isolate_host_contention_signals() -> Generator[None, None, None]:
    """Isolate the module-level host contention signal deque per test."""
    from ralph.mcp.server._mcp_health_event import clear_host_contention_signals

    clear_host_contention_signals()
    try:
        yield
    finally:
        clear_host_contention_signals()


@pytest.fixture(scope="session", autouse=True)
def _warm_pygments_python_lexer() -> Generator[None, None, None]:
    """Compile the Pygments Python lexer once, outside any timed test.

    The first ``rich.syntax`` render in a process pays Pygments' one-time
    lexer construction (hundreds of regex compiles). Under a loaded host
    that cold start can exceed the 1 s per-test budget, so whichever test
    happened to render first failed for a reason that had nothing to do
    with what it asserts. Warming the lexer here charges the one-time
    cost to the session, where it belongs, and leaves each test's budget
    measuring the assertion itself.
    """
    from pygments.lexers import get_lexer_by_name

    get_lexer_by_name("python")
    yield


@pytest.fixture(scope="session", autouse=True)
def _fake_agy_models_probe() -> Generator[None, None, None]:
    """Keep default tests independent of the locally installed AGY binary."""
    from ralph.agents import registry

    original_probe = registry._default_agy_models_probe
    registry._default_agy_models_probe = lambda: "\n".join(registry._AGY_MODELS)
    try:
        yield
    finally:
        registry._default_agy_models_probe = original_probe


def pytest_unconfigure(config: pytest.Config) -> None:
    del config
    from ralph.mcp.runtime_executors import shutdown_runtime_executors
    from ralph.process import reset_process_manager

    reset_process_manager()
    shutdown_runtime_executors()


@pytest.fixture(autouse=True)
def _isolate_default_process_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Clean the default manager while each test's fake process backend is active."""
    from ralph.process import reset_process_manager

    reset_process_manager()
    try:
        yield
    finally:
        reset_process_manager()


@pytest.fixture(autouse=True)
def _fake_claude_cli_mcp_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep default tests off the locally installed `claude` binary.

    ``report_claude_mcp_servers_ralph_cannot_proxy`` shells out to
    ``claude mcp list`` once per run to find the MCP servers
    ``--strict-mcp-config`` strips but Ralph cannot proxy back. That is a real
    subprocess with real network health checks -- far outside the per-test
    budget -- and every Claude command-building test would trip it. Stub the
    lister to "could not be consulted" and clear the per-run memo so each test
    starts from a known state; tests that exercise the report install their own
    lister on top.
    """
    from ralph.mcp.transport import claude as claude_transport

    claude_transport.reset_claude_mcp_proxy_report()
    monkeypatch.setattr(claude_transport, "claude_cli_mcp_server_lister", lambda: None)


def pytest_configure(config: pytest.Config) -> None:
    """Disable pytest's unraisableexception teardown gc passes.

    The unraisableexception plugin runs ``gc_collect_harder(5)`` (five full
    ``gc.collect`` passes) in every xdist worker at session teardown. On this
    ~17.4k-test suite that was measured at 5.7s+ per worker, accounting for
    ~11.5s of the 48.87s baseline ``make test`` wall-clock. Setting the
    plugin's stash key to 0 keeps the unraisable hook fully active while
    skipping only those end-of-session gc passes.
    """
    config.stash[gc_collect_iterations_key] = 0
    _bypass_macos_git_shim()


_MACOS_GIT_SHIM = "/usr/bin/git"
_XCODE_SELECT_LINK = Path("/var/db/xcode_select_link")


def _bypass_macos_git_shim() -> None:
    """Put the real git binary ahead of macOS's ``/usr/bin/git`` xcrun shim.

    The shim re-resolves the active developer directory on every spawn. That
    nearly doubled the real-git tests' wall clock (the four heaviest real-git
    files measured 26.9s through the shim versus 14.2s direct). Exposing the
    exact binary the shim would exec keeps git's behaviour identical. Child
    processes inherit the adjusted ``PATH``; xdist workers and nested pytest
    runs find the bypass already in place and leave it alone.
    """
    if sys.platform != "darwin" or shutil.which("git") != _MACOS_GIT_SHIM:
        return
    developer_dir = os.environ.get("DEVELOPER_DIR")
    if not developer_dir and _XCODE_SELECT_LINK.is_symlink():
        developer_dir = os.path.realpath(_XCODE_SELECT_LINK)
    if not developer_dir:
        return
    real_git = Path(developer_dir) / "usr" / "bin" / "git"
    if not real_git.is_file():
        return
    bypass_dir = Path(tempfile.gettempdir()) / f"ralph-pytest-git-{os.getuid()}"
    bypass_dir.mkdir(mode=0o700, exist_ok=True)
    link = bypass_dir / "git"
    if not (link.is_symlink() and link.resolve() == real_git.resolve()):
        # Concurrent shards race here; an atomic rename keeps ``link`` valid.
        staging = bypass_dir / f"git.{os.getpid()}"
        staging.unlink(missing_ok=True)
        staging.symlink_to(real_git)
        staging.replace(link)
    os.environ["PATH"] = f"{bypass_dir}{os.pathsep}{os.environ.get('PATH', '')}"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Select the explicit required auto-integrate files for verification."""
    for item in items:
        relative_path = item.path.relative_to(Path.cwd()).as_posix()
        if relative_path in _REQUIRED_AUTO_INTEGRATE_E2E_PATHS:
            item.add_marker("required_auto_integrate_e2e")
        # Most AGY regression coverage is an explicit smoke profile. The
        # sub-second in-memory PTY invocation module remains in the default
        # suite so launch-policy regressions cannot hide behind an opt-in gate.
        if "agy" in item.nodeid.casefold() and relative_path not in _AGY_DEFAULT_TEST_PATHS:
            item.add_marker("smoke")


if TYPE_CHECKING:
    from collections.abc import Generator
    from types import FrameType


@pytest.fixture(autouse=True)
def _reset_rich_style_color_caches() -> Generator[None, None, None]:
    """Clear Rich's process-global Style/Color LRU caches after each test.

    Rich memoizes ``Style.parse`` / ``Style._add`` (among others) as
    module-level ``functools.lru_cache``-wrapped callables, keyed by style
    content -- shared across every ``Console`` in the process. A test that
    renders through a non-default ``color_system`` (e.g.
    ``Console(color_system="standard")``, used to exercise ANSI-16
    downgrading -- see ``tests/pipeline/test_run_loop_status_bar_wiring.py``)
    can leave a shared cached ``Style``/``Color`` object resolved for that
    reduced colour depth. A LATER test's full-truecolour render for the
    *same* style string then reuses the stale cached value instead of
    resolving fresh for its own console, producing escape codes for the
    wrong colour depth -- this was measured to make
    ``tests/display/test_generated_scene_renderer.py::test_generated_scene_colours_every_named_semantic_category``
    fail only when it runs after that status-bar test in the same process
    (confirmed by bisection), never in isolation. A real Ralph process only
    ever constructs one Console at one colour depth for its entire
    lifetime, so Rich's cross-Console cache sharing is never observed in
    production -- this is purely a multi-Console test-isolation artifact.
    Clearing the caches after every test keeps each test's rendering
    assertions independent of execution order without touching any
    production code path.
    """
    yield
    import rich.color
    import rich.style

    for cached_callable in (
        rich.style.Style.parse,
        rich.style.Style._add,
    ):
        cached_callable.cache_clear()


class TestExecutionTimeoutError(BaseException):
    """The per-test SIGALRM budget ran out.

    A ``BaseException``, like ``KeyboardInterrupt``, so code under test
    cannot swallow it. As a ``TimeoutError`` it was an ``OSError``: an
    ``except OSError: continue`` around a file read turned an overrun into
    a silently skipped file (a fresh audit scan then reported phantom
    baseline drift), and ``except Exception`` blocks hid overruns entirely.
    """


#: The per-test wall-clock HANG cap, as a multiple of the per-test budget
#: (and at most ``_WALL_HANG_MAX_EXTRA_SECONDS`` above it). The budget itself
#: is charged in CPU seconds -- see :func:`pytest_runtest_call`.
_WALL_HANG_FACTOR = 5.0
_WALL_HANG_MAX_EXTRA_SECONDS = 60.0


def _wall_hang_cap(budget_seconds: float) -> float:
    return min(budget_seconds * _WALL_HANG_FACTOR, budget_seconds + _WALL_HANG_MAX_EXTRA_SECONDS)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, None]:
    """Enforce the per-test budget: CPU seconds, plus a wall-clock hang cap.

    The budget (``timeout_seconds`` marker, else the 1 s default) is charged
    against the CPU this process spends (``ITIMER_PROF``): the work the test
    controls. ``make test`` saturates every core, so a wall-clock budget
    measured the host instead -- a 0.1 s test was seen taking 1 s of wall
    clock. A test that waits rather than works (a sleep, a blocked read, a
    hang) uses no CPU, so a wall-clock cap (``ITIMER_REAL``) of
    :func:`_wall_hang_cap` still fails it. Both raise the uncatchable
    :class:`TestExecutionTimeoutError`.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    timeout_marker = item.get_closest_marker("timeout_seconds")
    if timeout_marker is not None and timeout_marker.args:
        timeout_seconds = float(timeout_marker.args[0])
    else:
        timeout_seconds = timeout_seconds_from_env(TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS)
    wall_cap_seconds = _wall_hang_cap(timeout_seconds)

    # ``_completed`` is mutated by the handlers and by the ``finally`` block
    # below to make the per-test timer contract race-safe. Once the test body
    # returns, a handler must not raise even if its signal was already pending
    # in the kernel queue while we cancelled the timer. A single-element list
    # keeps the closure scoped to this test invocation; a plain ``bool`` would
    # capture by value and could not be mutated from the nested handlers.
    _completed = [False]

    # A signal can be delivered just AFTER its timer was cancelled (seen on
    # macOS for ITIMER_PROF). A handler whose own timer still has time left
    # is therefore looking at a stale signal from an earlier test and ignores
    # it; an expired one-shot timer reads back as 0.
    def _handle_cpu_budget(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        if _completed[0] or signal.getitimer(signal.ITIMER_PROF)[0] > 0:
            return
        raise TestExecutionTimeoutError(
            f"test used more than {timeout_seconds} CPU seconds: {item.nodeid}"
        )

    def _handle_wall_hang(signum: int, frame: FrameType | None) -> None:
        del signum, frame
        if _completed[0] or signal.getitimer(signal.ITIMER_REAL)[0] > 0:
            return
        raise TestExecutionTimeoutError(
            f"test exceeded the {wall_cap_seconds} s wall-clock hang cap "
            f"({timeout_seconds} s budget): {item.nodeid}"
        )

    timers = (
        (signal.SIGPROF, signal.ITIMER_PROF, timeout_seconds, _handle_cpu_budget),
        (signal.SIGALRM, signal.ITIMER_REAL, wall_cap_seconds, _handle_wall_hang),
    )
    previous_handlers = [signal.getsignal(signum) for signum, *_rest in timers]
    for signum, which, seconds, handler in timers:
        signal.signal(signum, handler)
        signal.setitimer(which, seconds)
    try:
        yield
    finally:
        # Cancel both timers, then drain any signal already queued in the
        # kernel: block the signals, point them at ``SIG_IGN`` so a pending
        # one is discarded on delivery instead of reaching the previous
        # handler (``SIG_DFL`` for SIGALRM/SIGPROF terminates the worker),
        # unblock, and only then restore the previous handlers.
        # ``pthread_sigmask`` is POSIX-only; elsewhere this degrades to a
        # best-effort cancel without the mask.
        _completed[0] = True
        signums = [signum for signum, *_rest in timers]
        for _signum, which, _seconds, _handler in timers:
            signal.setitimer(which, 0)
        if hasattr(signal, "pthread_sigmask"):
            signal.pthread_sigmask(signal.SIG_BLOCK, signums)
            for signum in signums:
                signal.signal(signum, signal.SIG_IGN)
            signal.pthread_sigmask(signal.SIG_UNBLOCK, signums)
        for signum, previous in zip(signums, previous_handlers, strict=True):
            restored = signal.SIG_DFL if previous is None else previous
            if signum == signal.SIGPROF and restored == signal.SIG_DFL:
                # Nothing else uses SIGPROF, and its default action terminates
                # the process: a late delivery after this point (see above)
                # killed a whole shard with exit -27. Leave it ignored.
                restored = signal.SIG_IGN
            signal.signal(signum, restored)


@pytest.fixture(autouse=True)
def _isolate_process_home(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    """Redirect user-level config lookups to a unique lightweight sandbox per test.

    The sandbox path is derived from the test node id under the worker's base temp dir,
    but the directories are not created eagerly. That keeps per-test isolation for HOME /
    XDG config lookups without paying the cost of materializing a fresh tmp_path tree for
    every test in the full parallel suite.

    wt-05-test-opti: cache the per-worker base temp dir lookup so the
    autouse fixture does not rebuild ``Path(...) / request.node.nodeid``
    objects from scratch on every test (the previous implementation paid
    ~0.3-0.5 ms per test across ~12.8k tests). ``tmp_path_factory`` is
    session-scoped internally, so a single ``getbasetemp()`` call is
    sufficient for the lifetime of one pytest worker.
    """

    worker_root = _autouse_home_base(tmp_path_factory)
    # Use the nodeid directly as the per-test suffix. nodeid is already
    # globally unique within a session (path::name[<param>] for parametrized
    # cases), so the extra SHA-1 round is unnecessary work in the hot path
    # of the autouse fixture.
    unique_suffix = request.node.nodeid
    # Skip the second Path concatenation: ``XDG_CONFIG_HOME`` is conventionally
    # ``$HOME/.config``, so an explicit suffix is unnecessary. Reading
    # ``$HOME/.config`` in the production code path yields the same value
    # without paying an extra Path construction + str() per test.
    fake_home = worker_root + "/" + unique_suffix + "/home"
    monkeypatch.setenv("HOME", fake_home)
    monkeypatch.setenv("XDG_CONFIG_HOME", fake_home + "/.config")
    monkeypatch.delenv("RALPH_UPSTREAM_MCP_CONFIG", raising=False)
    # Dummy provider keys so the credential preflight does not abort
    # agent-invoking tests that fake the process layer. Dedicated preflight
    # tests inject their own ``env_getter`` and are unaffected by these
    # ``os.environ`` defaults.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-dummy-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-dummy-key")
    monkeypatch.setenv("CURSOR_API_KEY", "test-dummy-key")
    # Cycle-deadline epochs are published per-invocation by the pipeline
    # runner and withdrawn when the invocation ends. A leaked value (a
    # crashed harness, an outer live run driving this test suite as its
    # verification step) would silently turn every artifact-validation
    # test into a warned-cycle test, rejecting honest partial results
    # with a deadline requirement the test never configured. Strip both
    # names so each test starts with "no deadline published" and only
    # fixtures that deliberately publish one (e.g.
    # tests/mcp/test_warned_gate_reads_runtime_state.py) see a cycle.
    monkeypatch.delenv("RALPH_CYCLE_WARN_EPOCH", raising=False)
    monkeypatch.delenv("RALPH_CYCLE_DEADLINE_EPOCH", raising=False)
    monkeypatch.delenv("RALPH_DEV_WARN_EPOCH", raising=False)
    monkeypatch.delenv("RALPH_DEV_DEADLINE_EPOCH", raising=False)


#: Per-worker base temp dir cache. ``tmp_path_factory`` is session-scoped
#: and ``getbasetemp()`` is stable for the lifetime of one pytest worker,
#: so a single lookup per worker (rather than per test) is sufficient.
#: Cleared by pytest between test sessions so stale paths never leak
#: across the canonical ``make test`` -> ``make test-subprocess-e2e``
#: invocation sequence.
_autouse_home_base_cache: dict[int, str] = {}


def _autouse_home_base(tmp_path_factory: pytest.TempPathFactory) -> str:
    cached = _autouse_home_base_cache.get(id(tmp_path_factory))
    if cached is not None:
        return cached
    base = str(tmp_path_factory.getbasetemp() / "autouse-home")
    _autouse_home_base_cache[id(tmp_path_factory)] = base
    return base


@pytest.fixture(autouse=True)
def _isolate_pipeline_thread_locals() -> None:
    """Reset the pipeline runner's thread-local session/retry-intent slots.

    ``ralph.pipeline._runner_session`` and ``ralph.pipeline.effect_executor``
    keep a per-thread ``threading.local`` for the captured session id and
    the captured retry intent. pytest-xdist reuses one worker process for
    many tests, so the thread-local from a prior test can leak into the
    next one and break the ``state.copy_with`` assertions in
    ``test_pipeline_runner_pipeline_runner_loop_2``. Clearing both slots
    before every test keeps the per-test assertions isolated without
    forcing a process fork per test.

    The fixture is intentionally cheap: two attribute writes, no I/O.

    wt-05-test-opti: drop the post-yield teardown. The next test's
    autouse setup runs the same two writes before its body executes,
    so the post-yield work is pure overhead across ~12.8k tests. The
    contract that ``_isolate_pipeline_thread_locals`` guarantees \u2014 a
    cleared slot at the start of every test \u2014 is preserved by the
    pre-yield reset alone.
    """
    set_last_captured_session_id(None)
    effect_executor._set_last_captured_retry_intent(effect_executor.cleared_agent_retry_intent())
    yield


def _configure_repo_identity(repo: object) -> None:
    writer = repo.config_writer()
    try:
        writer.set_value("user", "name", "Test User")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("core", "logAllRefUpdates", "false")
        writer.set_value("gc", "auto", "0")
    finally:
        writer.release()
    exclude_path = Path(repo.git_dir) / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    with exclude_path.open("a", encoding="utf-8") as f:
        f.write(".agent/\n")


@pytest.fixture(scope="session")
def _git_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Create a reusable template git repository for fast per-test clones."""
    template_root = tmp_path_factory.mktemp("git-template")
    repo = Repo.init(template_root)
    try:
        _configure_repo_identity(repo)

        readme = template_root / "README.md"
        readme.write_text("test")
        repo.index.add(["README.md"])
        repo.index.commit("initial commit")
    finally:
        repo.close()

    return template_root


@pytest.fixture
def tmp_git_repo(tmp_path: Path, _git_repo_template: Path) -> Path:
    """Create a temporary git repository for testing.

    Creating a fresh repository with GitPython for every test costs several
    seconds of real subprocess/git I/O. The session-scoped template already
    contains an identical initial commit and config, so copying it gives the
    same isolated starting state much faster.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the temporary git repository.
    """
    repo_path = tmp_path / "repo"
    shutil.copytree(_git_repo_template, repo_path)
    return repo_path


@pytest.fixture
def sample_config_dict() -> dict[str, object]:
    """Sample configuration dictionary for testing.

    Returns:
        Dictionary with sample config values.
    """
    return {
        "general": {
            "verbosity": 2,
            "developer_context": 1,
            "checkpoint_enabled": True,
        },
        "agents": {
            "claude": {
                "cmd": "claude",
                "output_flag": "--output-format=stream-json",
                "yolo_flag": "--permission-mode auto",
                "json_parser": "claude",
            },
            "opencode": {
                "cmd": "opencode",
                "output_flag": "--json-stream",
                "json_parser": "opencode",
            },
        },
        "agent_chains": {
            "development": ["claude", "opencode"],
            "review": ["claude"],
        },
    }


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace directory for testing.

    Args:
        tmp_path: Pytest temporary path fixture.

    Returns:
        Path to the workspace directory.
    """
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    return workspace_dir


@pytest.fixture
def minimal_agents_policy() -> AgentsPolicy:
    """Create a minimal agents policy for testing.

    Returns:
        AgentsPolicy with planning and development chains/drains.
    """
    return AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["claude"], max_retries=2),
            "development": AgentChainConfig(agents=["claude", "opencode"], max_retries=3),
            "development_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
            "development_commit": AgentChainConfig(agents=["claude"], max_retries=2),
            "review": AgentChainConfig(agents=["claude"], max_retries=3),
            "review_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
            "fix": AgentChainConfig(agents=["claude"], max_retries=3),
            "review_commit": AgentChainConfig(agents=["claude"], max_retries=2),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
            "development_analysis": AgentDrainConfig(chain="development_analysis"),
            "development_commit": AgentDrainConfig(chain="development_commit"),
            "review": AgentDrainConfig(chain="review"),
            "review_analysis": AgentDrainConfig(chain="review_analysis"),
            "fix": AgentDrainConfig(chain="fix"),
            "review_commit": AgentDrainConfig(chain="review_commit"),
        },
    )


@pytest.fixture
def minimal_pipeline_policy() -> PipelinePolicy:
    """Create a minimal pipeline policy for testing.

    Returns:
        PipelinePolicy with standard phase routing.
    """
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="review",
                    on_failure="failed_terminal",
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="review_commit",
                    on_loopback="fix",
                ),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(on_success="review"),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure="failed_terminal",
                ),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="complete",
                ),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


@pytest.fixture
def memory_workspace() -> MemoryWorkspace:
    """Create an in-memory workspace for testing.

    Returns:
        MemoryWorkspace instance with a default prompt file.
    """
    ws = MemoryWorkspace()
    ws.write("PROMPT.md", "# Test Prompt\n\nThis is a test prompt.")
    return ws


@pytest.fixture
def mock_agent_invoker(
    memory_workspace: MemoryWorkspace,
) -> MockAgentInvoker:
    """Create a mock agent invoker for testing.

    Args:
        memory_workspace: In-memory workspace fixture.

    Returns:
        MockAgentInvoker instance.
    """
    return MockAgentInvoker(memory_workspace)


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    """Provide a temporary workspace directory for integration tests."""
    return tmp_path


@pytest.fixture
def initial_state() -> PipelineState:
    """Create a default initial pipeline state for integration tests."""
    return PipelineState(
        phase="planning",
        policy_entry_phase="planning",
        budget_caps={"iteration": 5},
    )


@pytest.fixture
def default_policy() -> tuple[AgentsPolicy, PipelinePolicy, ArtifactsPolicy]:
    """Create a default policy tuple for integration tests."""
    agents_policy = AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["claude"], max_retries=2),
            "development": AgentChainConfig(agents=["claude"], max_retries=3),
            "development_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
            "development_commit": AgentChainConfig(agents=["claude"], max_retries=2),
            "planning_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
            "development_analysis": AgentDrainConfig(chain="development_analysis"),
            "development_commit": AgentDrainConfig(chain="development_commit"),
            "planning_analysis": AgentDrainConfig(chain="planning_analysis"),
        },
    )
    pipeline_policy = PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="development", on_loopback="planning"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure="failed_terminal",
                ),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )
    return (agents_policy, pipeline_policy, ArtifactsPolicy())


@pytest.fixture
def cli_runner() -> object:
    """Provide a Typer CLI test runner."""
    return CliRunner()


@pytest.fixture
def workspace_root(tmp_path: Path) -> Path:
    """Return a temporary workspace root."""
    return tmp_path


@pytest.fixture
def config_with_test_agent() -> UnifiedConfig:
    """Return a UnifiedConfig with one interactive test agent."""
    return UnifiedConfig(
        agents={
            "test-agent": AgentConfig(
                cmd="claude",
                transport=AgentTransport.CLAUDE_INTERACTIVE,
            )
        },
    )
