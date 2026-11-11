"""Shared fixtures for ralph test suite."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from _pytest.unraisableexception import gc_collect_iterations_key

from tests._real_config_guard_support import (
    _REAL_HOME,
    _REAL_XDG_CONFIG_HOME,
    _clear_recorded_read_violations,
    _clear_recorded_write_violations,
    _compute_file_sha256,
    _ConfigPathSnapshot,
    _diff_config_snapshots,
    _get_test_read_violations,
    _install_config_guard_audit_hook,
    _read_violations_count,
    _resolve_guarded_config_dirs,
    _scan_guarded_config_files,
    _set_current_test_node,
)

__all__ = [
    "_REAL_HOME",
    "_REAL_XDG_CONFIG_HOME",
    "_ConfigPathSnapshot",
    "_clear_recorded_read_violations",
    "_compute_file_sha256",
    "_diff_config_snapshots",
    "_get_test_read_violations",
    "_install_config_guard_audit_hook",
    "_read_violations_count",
    "_resolve_guarded_config_dirs",
    "_scan_guarded_config_files",
    "_set_current_test_node",
]

from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, UnifiedConfig

# The isolation helpers below run for EVERY test case, so the modules they
# touch are resolved once here instead of running the ``import`` statement
# machinery on every single test. Each of these modules is imported by the
# suite anyway (the first test used to pay for the import); hoisting only
# removes the repeated per-test import cost, it does not change which module
# object the helpers patch -- ``importlib.reload`` re-executes a module in
# place, so the binding stays valid across a reload.
from ralph.mcp.explore import recovery as _explore_recovery
from ralph.mcp.server._mcp_health_event import clear_host_contention_signals
from ralph.mcp.transport import claude as claude_transport
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
from ralph.process import reset_process_manager
from ralph.test_suites import (
    REQUIRED_AUTO_INTEGRATE_E2E_FILES,
)
from ralph.workspace.memory import MemoryWorkspace
from tests.integration._mock_agent_invoker import MockAgentInvoker

# ``tests._per_test_budget`` charges every test phase (setup, call and
# teardown) against the per-test budget: main-thread CPU plus a wall hang cap.
pytest_plugins = ("ralph.testing.pytest_timeout_plugin", "tests._per_test_budget")
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
def _isolate_test_environment_and_globals(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    """Apply every per-test environment/global isolation in one fixture.

    wt-03-test-budget: this fixture used to be seven separate autouse
    fixtures. pytest charges a fixed price per fixture *setup* (closure
    resolution, ``inspect``-based signature/coroutine checks, finalizer
    registration), and that price is paid once per test: on the ~18.7k
    test suite the seven fixtures cost more CPU than the isolation work
    they perform. The isolation guarantees are unchanged -- the same
    environment variables are set and unset, the same process-global
    snapshot/restore happens, the same caches are cleared -- they are
    simply applied by one fixture instead of seven.

    The host-contention-signal isolation and the default-process-manager
    isolation are applied from this fixture, in the position their own
    fixtures held, so the observable sequence of isolation actions is
    preserved.

    The order below is the order the individual fixtures ran in
    (definition order), and the teardown order is the reverse of it, so
    the observable sequence of isolation actions is preserved:

    setup:   credentials -> recovery drain stub -> recovery queue
             snapshot -> Claude CLI probe stub -> HOME/XDG sandbox ->
             pipeline thread-local reset -> host contention signal
             clear -> default process manager reset
    teardown: default process manager reset -> host contention signal
             clear -> Rich cache clear -> recovery queue restore

    The recovery queue restore runs last so a test still observes the
    queue it found, exactly as when the restore had its own fixture.
    """
    _apply_placeholder_hosted_provider_credentials(monkeypatch)
    _apply_disable_universal_skill_updates(monkeypatch)
    _apply_disable_explore_recovery_drain_thread(monkeypatch)
    with _isolate_explore_recovery_globals():
        _apply_fake_claude_cli_mcp_probe(monkeypatch)
        _apply_isolate_process_home(request, monkeypatch, tmp_path_factory)
        _set_current_test_node(request.node.nodeid)
        _apply_isolate_pipeline_thread_locals()
        _apply_clear_host_contention_signals()
        _apply_reset_default_process_manager()
        try:
            yield
        finally:
            _set_current_test_node(None)
            _apply_reset_default_process_manager()
            _apply_clear_host_contention_signals()
            _reset_rich_style_color_caches()


def _apply_placeholder_hosted_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep fake agent-process tests independent of real provider credentials."""
    for var in (
        "RALPH_MCP_ENDPOINT",
        "RALPH_MCP_RUN_ID",
        "RALPH_MCP_INHERITED_FD",
        "RALPH_MCP_RESPAWN",
    ):
        if var in os.environ:
            del os.environ[var]


def _apply_disable_universal_skill_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the universal-skill updater out of the default test lane.

    The run-start seam starts the updater on a daemon thread, and the
    updater reaches the REAL upstream. The default lane must never make a
    real network request (the network path is covered by the dedicated
    fixture suite with an injected fake client), and the daemon thread's
    asynchronous warnings must not land in another test's console capture.

    The production kill switch is the honest lever: with
    ``RALPH_DISABLE_SKILL_UPDATES`` set, the seam never calls the updater
    at all, so no request is made and no thread is started. The dedicated
    runtime-integration suite deletes the variable again in the cases that
    assert the ENABLED path.
    """
    monkeypatch.setenv("RALPH_DISABLE_SKILL_UPDATES", "1")


def _apply_disable_explore_recovery_drain_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic against the background recovery drain.

    ``enqueue_recovery`` starts a daemon drain thread in production;
    tests assert the pending queue synchronously and drain it via
    ``run_pending_recovery`` themselves, so the thread is stubbed out.
    """
    monkeypatch.setattr(_explore_recovery, "_ensure_drain_thread", lambda: None)


@contextmanager
def _isolate_explore_recovery_globals() -> Iterator[None]:
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
    ``_apply_disable_explore_recovery_drain_thread`` above, so no
    background work can race the snapshot/restore.

    The snapshot and the clear share ONE lock acquisition: the drain
    thread that could interleave with them is stubbed out, so no second
    acquisition can observe a different queue state.
    """
    with _explore_recovery._PENDING_LOCK:
        pending_snapshot = _explore_recovery._PENDING_RECOVERY.copy()
        wakeup_was_set = _explore_recovery._DRAIN_WAKEUP.is_set()
        _explore_recovery._PENDING_RECOVERY.clear()
    _explore_recovery._DRAIN_WAKEUP.clear()
    try:
        yield
    finally:
        with _explore_recovery._PENDING_LOCK:
            _explore_recovery._PENDING_RECOVERY.clear()
            if pending_snapshot:
                _explore_recovery._PENDING_RECOVERY.update(pending_snapshot)
        if wakeup_was_set:
            _explore_recovery._DRAIN_WAKEUP.set()
        else:
            _explore_recovery._DRAIN_WAKEUP.clear()


# RALPH-TEST-REASON: the host-contention-signal isolation is merged into the
# shared autouse isolation fixture, so the same isolation action runs in the
# same position of the isolation sequence while pytest charges the per-test
# fixture setup once instead of three times.
def _apply_clear_host_contention_signals() -> None:
    """Clear the module-level host contention signal deque.

    The clear brackets the test body, so the deque a test observes is
    the one it found.
    """
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


@pytest.fixture(scope="session", autouse=True)
def _guard_real_config_files(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[None, None, None]:
    """Snapshot real ~/.config and $XDG_CONFIG_HOME and verify no test touched them.

    Takes a baseline snapshot of existing `ralph-workflow*` files and directories
    in the real user configuration directories before any tests run. At session
    teardown, verifies that no real configuration files were created, modified,
    chmodded, deleted, or read by the test suite.
    """
    _install_config_guard_audit_hook()
    _clear_recorded_read_violations()
    _clear_recorded_write_violations()
    guarded_dirs = _resolve_guarded_config_dirs()
    before = _scan_guarded_config_files(guarded_dirs)
    session_home = tmp_path_factory.mktemp("session-home")
    session_config = session_home / ".config"
    session_config.mkdir(parents=True)
    orig_home = os.environ.get("HOME")
    orig_xdg = os.environ.get("XDG_CONFIG_HOME")
    os.environ["HOME"] = str(session_home)
    os.environ["XDG_CONFIG_HOME"] = str(session_config)
    try:
        yield
    finally:
        if orig_home is not None:
            os.environ["HOME"] = orig_home
        else:
            os.environ.pop("HOME", None)
        if orig_xdg is not None:
            os.environ["XDG_CONFIG_HOME"] = orig_xdg
        else:
            os.environ.pop("XDG_CONFIG_HOME", None)
        after = _scan_guarded_config_files(guarded_dirs)
        violations = _diff_config_snapshots(before, after)
        for node, p in _clear_recorded_read_violations():
            if node:
                violations.append(f"READ: {p} (by {node})")
            else:
                violations.append(f"READ: {p}")
        for node, p in _clear_recorded_write_violations():
            if node:
                violations.append(f"TRANSIENT MUTATION: {p} (by {node})")
            else:
                violations.append(f"TRANSIENT MUTATION: {p}")
        if violations:
            msg = (
                "Test suite touched real user configuration files in violation of isolation rules:\n"
                + "\n".join(f"  - {v}" for v in violations)
                + "\nAll tests must use isolated environments (e.g. tmp_path, isolated HOME/XDG_CONFIG_HOME)."
            )
            pytest.fail(msg, pytrace=False)


def pytest_unconfigure(config: pytest.Config) -> None:
    del config
    from ralph.mcp.runtime_executors import shutdown_runtime_executors
    from ralph.process import reset_process_manager

    reset_process_manager()
    shutdown_runtime_executors()


# RALPH-TEST-REASON: the default-process-manager isolation is merged into the
# shared autouse isolation fixture, so the same isolation action runs in the
# same position of the isolation sequence while pytest charges the per-test
# fixture setup once instead of three times.
def _apply_reset_default_process_manager() -> None:
    """Clean the default manager while each test's fake process backend is active.

    The reset brackets the test body, so a test that swaps the process
    backend starts and ends from the default manager.
    """
    reset_process_manager()


def _apply_fake_claude_cli_mcp_probe(monkeypatch: pytest.MonkeyPatch) -> None:
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
    """Select the explicit required auto-integrate files for verification.

    wt-04-test-opti: the working directory and the per-directory relative
    path are resolved ONCE instead of once per collected item. The cwd
    cannot change while this hook runs, and every item in a directory
    shares that directory's relative prefix, so re-deriving them per item
    was pure repetition across the ~19.4k collected cases.
    """
    cwd = Path.cwd()
    relative_dirs: dict[Path, str] = {}
    for item in items:
        parent = item.path.parent
        relative_dir = relative_dirs.get(parent)
        if relative_dir is None:
            relative_dir = parent.relative_to(cwd).as_posix()
            relative_dirs[parent] = relative_dir
        relative_path = (
            item.path.name if relative_dir == "." else f"{relative_dir}/{item.path.name}"
        )
        if relative_path in _REQUIRED_AUTO_INTEGRATE_E2E_PATHS:
            item.add_marker("required_auto_integrate_e2e")
        # Most AGY regression coverage is an explicit smoke profile. The
        # sub-second in-memory PTY invocation module remains in the default
        # suite so launch-policy regressions cannot hide behind an opt-in gate.
        if "agy" in item.nodeid.casefold() and relative_path not in _AGY_DEFAULT_TEST_PATHS:
            item.add_marker("smoke")


if TYPE_CHECKING:
    from collections.abc import Generator

    from git import Repo


def _reset_rich_style_color_caches() -> None:
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
    if "rich.style" in sys.modules:
        rich_style = sys.modules["rich.style"]
        rich_style.Style.parse.cache_clear()
        rich_style.Style._add.cache_clear()


def _apply_isolate_process_home(
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

    wt-04-test-opti: build every published path with ONE ``Path`` parse.
    The previous implementation chained ``/`` operators, and each ``/``
    re-parses the accumulated path, so publishing the six redirected
    module globals cost 13 parses per test. The published values are
    unchanged: ``Path(a) / b`` and ``Path(a + "/" + b)`` produce the
    same path for the relative components used here.
    """

    worker_root: str = _autouse_home_base(tmp_path_factory)
    # Use the nodeid directly as the per-test suffix. nodeid is already
    # globally unique within a session (path::name[<param>] for parametrized
    # cases), so the extra SHA-1 round is unnecessary work in the hot path
    # of the autouse fixture.
    unique_suffix: str = request.node.nodeid
    # Skip the second Path concatenation: ``XDG_CONFIG_HOME`` is conventionally
    # ``$HOME/.config``, so an explicit suffix is unnecessary. Reading
    # ``$HOME/.config`` in the production code path yields the same value
    # without paying an extra Path construction + str() per test.
    fake_home: str = worker_root + "/" + unique_suffix + "/home"
    os.environ["HOME"] = fake_home
    os.environ["XDG_CONFIG_HOME"] = fake_home + "/.config"
    for var in (
        "RALPH_UPSTREAM_MCP_CONFIG",
        "RALPH_CYCLE_WARN_EPOCH",
        "RALPH_CYCLE_DEADLINE_EPOCH",
        "RALPH_DEV_WARN_EPOCH",
        "RALPH_DEV_DEADLINE_EPOCH",
    ):
        if var in os.environ:
            del os.environ[var]
    # Dummy provider keys so the credential preflight does not abort
    # agent-invoking tests that fake the process layer. Dedicated preflight
    # tests inject their own ``env_getter`` and are unaffected by these
    # ``os.environ`` defaults.
    if os.environ.get("ANTHROPIC_API_KEY") != "test-dummy-key":
        os.environ["ANTHROPIC_API_KEY"] = "test-dummy-key"
    if os.environ.get("OPENAI_API_KEY") != "test-dummy-key":
        os.environ["OPENAI_API_KEY"] = "test-dummy-key"
    if os.environ.get("CURSOR_API_KEY") != "test-dummy-key":
        os.environ["CURSOR_API_KEY"] = "test-dummy-key"
    config_dir = fake_home + "/.config"
    loader_mod = sys.modules.get("ralph.config.loader")
    if loader_mod is not None:
        loader_mod.GLOBAL_CONFIG_PATH = Path(config_dir + "/ralph-workflow.toml")
        loader_mod.GLOBAL_AGENTS_CONFIG_PATH = Path(config_dir + "/ralph-workflow-agents.toml")
    state_store_mod = sys.modules.get("ralph.skills._state_store")
    if state_store_mod is not None:
        capabilities_path = Path(config_dir + "/ralph-workflow-capabilities.json")
        state_store_mod.DEFAULT_STATE_PATH = capabilities_path
        state_store_mod.default_state_path = lambda: capabilities_path
    agy_log_path = Path(fake_home + "/.gemini/antigravity-cli/cli.log")
    agy_diagnostic_mod = sys.modules.get("ralph.agents._agy_upstream_diagnostic")
    if agy_diagnostic_mod is not None:
        agy_diagnostic_mod._AGY_CLI_LOG_PATH = agy_log_path
    smoke_plumbing_mod = sys.modules.get("ralph.pipeline.plumbing.smoke_plumbing")
    if smoke_plumbing_mod is not None:
        smoke_plumbing_mod._AGY_CLI_LOG_PATH = agy_log_path


#: Per-worker base temp dir cache. ``tmp_path_factory`` is session-scoped
#: and ``getbasetemp()`` is stable for the lifetime of one pytest worker,
#: so a single lookup per worker (rather than per test) is sufficient.
#: The cached value keeps a strong reference to the factory it was derived
#: from, so the factory's ``id()`` can never be recycled by a later
#: session and hand a stale base path to a new worker.
_autouse_home_base_cache: dict[int, tuple[pytest.TempPathFactory, str]] = {}


def _autouse_home_base(tmp_path_factory: pytest.TempPathFactory) -> str:
    cached = _autouse_home_base_cache.get(id(tmp_path_factory))
    if cached is not None:
        return cached[1]
    base = str(tmp_path_factory.getbasetemp() / "autouse-home")
    _autouse_home_base_cache[id(tmp_path_factory)] = (tmp_path_factory, base)
    return base


def _apply_isolate_pipeline_thread_locals() -> None:
    """Reset the pipeline runner's thread-local session/retry-intent slots.

    ``ralph.pipeline._runner_session`` and ``ralph.pipeline.effect_executor``
    keep a per-thread ``threading.local`` for the captured session id and
    the captured retry intent. pytest-xdist reuses one worker process for
    many tests, so the thread-local from a prior test can leak into the
    next one and break the ``state.copy_with`` assertions in
    ``test_pipeline_runner_pipeline_runner_loop_2``. Clearing both slots
    before every test keeps the per-test assertions isolated without
    forcing a process fork per test.

    The reset is intentionally cheap: two attribute writes, no I/O.

    wt-05-test-opti: drop the post-yield teardown. The next test's
    reset runs the same two writes before its body executes,
    so the post-yield work is pure overhead across ~12.8k tests. The
    contract that ``_apply_isolate_pipeline_thread_locals`` guarantees
    -- a cleared slot at the start of every test -- is preserved by the
    reset alone.
    """
    set_last_captured_session_id(None)
    effect_executor._set_last_captured_retry_intent(effect_executor.cleared_agent_retry_intent())


def _configure_repo_identity(repo: Repo) -> None:
    writer = repo.config_writer()
    try:
        writer.set_value("user", "name", "Test User")
        writer.set_value("user", "email", "test@example.com")
        writer.set_value("commit", "gpgSign", "false")
        writer.set_value("core", "logAllRefUpdates", "false")
        writer.set_value("core", "fsync", "none")
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
    fake_home = template_root / "home"
    fake_config = fake_home / ".config"
    fake_config.mkdir(parents=True)
    orig_home = os.environ.get("HOME")
    orig_xdg = os.environ.get("XDG_CONFIG_HOME")
    os.environ["HOME"] = str(fake_home)
    os.environ["XDG_CONFIG_HOME"] = str(fake_config)
    try:
        # Pin the branch: the host's init.defaultBranch must not decide whether the
        # fixture sits on the "main" the integration tests target.
        from git import Repo

        repo = Repo.init(template_root, initial_branch="main")
        try:
            _configure_repo_identity(repo)

            readme = template_root / "README.md"
            readme.write_text("test")
            repo.index.add(["README.md"])
            repo.index.commit("initial commit")
        finally:
            repo.close()
    finally:
        if orig_home is not None:
            os.environ["HOME"] = orig_home
        else:
            os.environ.pop("HOME", None)
        if orig_xdg is not None:
            os.environ["XDG_CONFIG_HOME"] = orig_xdg
        else:
            os.environ.pop("XDG_CONFIG_HOME", None)

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
    from typer.testing import CliRunner

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
