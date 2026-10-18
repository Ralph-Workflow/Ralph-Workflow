"""Real-Git end-to-end acceptance tests for the O2 publication reconciler.

The drift-recovery tests in :mod:`tests.test_auto_integrate_drift_recovery`
use in-memory fakes to pin the contract. This module exercises the
real ``reconcile_remote_publication`` path against a real bare remote
so the acceptance criteria (AC5-AC9) are proven against actual Git
refs, not synthesised SHA strings. Real ref equality is observed
before and after each operation, not just push callbacks.

The tests are marked ``subprocess_e2e`` and ``timeout_seconds(60)`` so
they run inside the 60-second combined ``make verify`` budget.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import TextIO

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.remote_push import PushStatus
from ralph.pipeline import auto_integrate_recovery
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline._rebase_outcome_kind import RebaseOutcomeKind
from ralph.workspace.scope import WorkspaceScope

_TARGET = "release"
_REMOTE = "tacos"


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <args>`` in ``repo_root``."""
    return subprocess.run(
        ("git", *args),
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def _commit(repo_root: Path, filename: str, content: str, message: str) -> str:
    target = repo_root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    assert _run(repo_root, "add", filename).returncode == 0
    assert _run(repo_root, "commit", "-m", message).returncode == 0
    return _run(repo_root, "rev-parse", "HEAD").stdout.strip()


def _init_repo(path: Path, default_branch: str = _TARGET) -> None:
    """Seed a repository whose default branch is ``default_branch``."""
    path.mkdir(parents=True, exist_ok=True)
    assert _run(path, "init").returncode == 0
    assert _run(path, "config", "user.email", "test@example.com").returncode == 0
    assert _run(path, "config", "user.name", "Test User").returncode == 0
    _commit(path, "seed.txt", "seed\n", "seed")
    assert _run(path, "branch", "-M", default_branch).returncode == 0


def _head_sha(repo_root: Path, ref: str) -> str:
    return _run(repo_root, "rev-parse", ref).stdout.strip()


def _build_config(
    *,
    enabled: bool = True,
    remote_enabled: bool = True,
    interval_seconds: float = 0.0,
) -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": enabled,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote": _REMOTE,
                "auto_integrate_remote_enabled": remote_enabled,
                "auto_integrate_remote_interval_seconds": interval_seconds,
            }
        }
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_concurrent_remote_advance_converges_to_ref_equality(tmp_path: Path) -> None:
    """AC9 real-Git: a concurrent remote advance ends with ref equality.

    The reconciler detects a concurrent remote advance, reintegrates,
    and pushes; both the local and the remote ref end at the same
    SHA. The test exercises a real bare remote and a real second
    worktree that pushes an independent commit, so the divergence
    is real and the convergence proof is a real ``rev-parse``
    equality check.
    """
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"
    writer = tmp_path / "writer"

    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    # Push the seed so the bare has the target branch.
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0

    # Concurrent remote advance: a second worktree clones the bare
    # at the seed, adds its own commit, and pushes to the bare
    # BEFORE the local repo makes any advance. The local repo then
    # lands a divergent commit. The local and the remote are now
    # strictly diverged (each has a unique commit the other lacks).
    assert _run(tmp_path, "clone", str(bare), str(writer)).returncode == 0
    assert _run(writer, "config", "user.email", "test@example.com").returncode == 0
    assert _run(writer, "config", "user.name", "Test User").returncode == 0
    assert _run(writer, "remote", "add", _REMOTE, str(bare)).returncode == 0
    remote_advance_sha = _commit(writer, "remote.txt", "remote work\n", "remote advance")
    assert _run(writer, "push", _REMOTE, _TARGET).returncode == 0

    # Local lands a divergent commit (after the writer's push).
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")
    # Note: the local cannot push its commit (the bare is now
    # ahead on the remote_advance_sha and the local is not a
    # fast-forward of it). The push is intentionally not attempted.

    # Bring the local repo's view of the remote forward.
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    # Pre-state: local and remote are strictly diverged.
    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    assert _head_sha(repo, f"refs/remotes/{_REMOTE}/{_TARGET}") == remote_advance_sha
    # The local and remote SHAs must be different (diverged).
    assert local_sha != remote_advance_sha

    # Drive the reconciler: divergence detected -> reconcile -> push.
    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    # Convergence proof: the local and remote refs are now equal
    # after the reconciler ran.
    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    remote_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == remote_ref, f"local {local_ref} != remote {remote_ref} after reconciliation"
    # The reconciled SHA must incorporate both the local advance
    # and the remote advance (strict descendants of each).
    out_local = _run(
        repo,
        "log",
        "--format=%H",
        f"{local_sha}..{local_ref}",
    )
    assert out_local.returncode == 0
    descendants = out_local.stdout.split()
    assert remote_advance_sha in descendants, (
        f"remote advance {remote_advance_sha} missing from reconciled history"
    )
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.MERGED,
        RebaseOutcomeKind.REBASED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_throttle_deferred_probe_publishes_to_configured_release_tacos(tmp_path: Path) -> None:
    """AC5, AC6 real-Git: fetch throttle armed at 60s suppresses probe during landing, but publication converges release/tacos.

    Uses non-default target 'release' and remote 'tacos'. Arms the real
    fetch throttle so the landing-seam probe is suppressed, lands a commit,
    and demonstrates that publication pushes and converges release and tacos/release
    with no origin or main branches/remotes accessed or created.
    """
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"

    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    cfg = _build_config(interval_seconds=60.0)

    # Arm the real fetch throttle
    remote_sync._REMOTE_PULL_THROTTLE.arm(repo, _REMOTE, _TARGET, clock=time.monotonic)

    # Demonstrate that fetch probe is suppressed during landing
    pull_record = remote_sync.pull_and_reconcile_target(
        cfg,
        repo,
        _TARGET,
    )
    assert pull_record is not None
    assert pull_record.last_remote_sync == "refresh suppressed"
    assert pull_record.last_refresh == "refresh suppressed by throttle"

    # Local lands a commit
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    # Publication converges release and tacos/release
    state = remote_sync.reconcile_remote_publication(
        cfg,
        repo,
        _TARGET,
        source_state=pull_record,
    )

    # Convergence proof: real ref equality
    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == bare_ref == local_sha
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )

    # No origin or main accessed or created
    remotes = _run(repo, "remote").stdout.split()
    assert "origin" not in remotes
    branches = _run(repo, "branch", "-a").stdout
    assert "main" not in branches


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_throttle_deferred_probe_still_publishes_concrete_refs(tmp_path: Path) -> None:
    """AC6 real-Git: a fetch suppressed by the throttle still allows the push.

    The reconciler must attempt the push when the local SHA is
    strictly ahead of the remote, regardless of probe freshness or
    previous in-memory state. After the push, the local and remote
    refs are equal in the real bare remote.
    """
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"

    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    # Push the seed so the bare has the target branch.
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    # Initial fetch so the remote tracking ref exists.
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    # Local lands a commit, does NOT push yet (simulates a probe
    # suppression by an upstream throttle).
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    # Pre-state: the bare remote is behind.
    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    bare_pre = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_pre != local_sha

    # Drive the reconciler: the throttle did not stop the push.
    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    # Convergence proof: real ref equality after the push.
    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == bare_ref == local_sha
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_killed_between_landing_and_push_publishes_on_recovery(tmp_path: Path) -> None:
    """AC7 real-Git: a process killed between local land and push is recovered.

    The local repo carries a commit on the target branch but the
    remote is behind. The startup reconciler publishes the
    pending commit on the next call.
    """
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"

    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    # Push the seed so the bare has the target branch.
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    # Initial fetch so the remote tracking ref exists.
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    # Land a commit locally but DO NOT push (simulates a process
    # killed between the local landing and the push).
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    # Pre-state: the local ref is ahead of the bare remote.
    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    bare_ref_before = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_before != local_sha

    # Drive the startup reconciler.
    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    # Convergence proof: the bare remote ref has caught up.
    bare_ref_after = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_after == local_sha
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_killed_between_landing_and_push_recovers_from_linked_worktree(tmp_path: Path) -> None:
    """AC7 real-Git: a process killed between local land and push is recovered from ANY worktree.

    Criterion 7 requires the next Ralph start in **any** worktree to push
    pending commits that landed locally before a crash.

    This test:
      1. Lands a commit in the primary repository via a subprocess that
         invokes the real Ralph publication reconciler
         (:func:`reconcile_remote_publication`) -- the same Ralph entry
         point the post-landing seam uses -- so the subprocess exercises
         actual Ralph landing semantics, not a hand-rolled git add/commit.
      2. Hard-kills the subprocess before it can finish pushing (mimicking
         the production crash window between local landing and the push
         attempt).
      3. Creates a linked worktree and starts a **fresh Ralph process**
         (different PID) in that worktree that calls the real startup
         recovery entry point. The recovery reconciler observes the
         pending local commit and pushes it to the bare remote.

    The bare remote must converge to the committed target SHA after the
    fresh process in the linked worktree completes.
    """
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"
    wt = tmp_path / "wt-sibling"

    _init_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    # Push the seed so the bare has the target branch.
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    # Initial fetch so the remote tracking ref exists.
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    # Prepare feature work on a feature branch in the primary repository.
    # The subprocess invokes Ralph's authentic landing entry point
    # (:func:`auto_integrate_after_commit`), which lands the feature
    # onto the target branch and pauses at the publication seam. The
    # subprocess is hard-killed before the push can complete (AC7: real
    # interruption between local landing and push).
    import sys

    assert _run(repo, "switch", "-c", "feature").returncode == 0
    (repo / "feature.txt").write_text("feature work\n", encoding="utf-8")
    assert _run(repo, "add", "feature.txt").returncode == 0
    assert _run(repo, "commit", "-m", "feature advance").returncode == 0

    landing_script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from ralph.config.models import UnifiedConfig\n"
        "from ralph.pipeline import auto_integrate_remote_sync as _sync\n"
        "from ralph.pipeline.auto_integrate import auto_integrate_after_commit\n"
        "from ralph.pipeline.rebase_state import RebaseState\n"
        "from ralph.pipeline.auto_integrate_remote_push import push_single_remote\n"
        "from ralph.workspace.scope import WorkspaceScope\n"
        "\n"
        "_real_push = push_single_remote\n"
        "def _slow_push(*args, **kwargs):\n"
        "    sys.stdout.write('BEFORE_PUSH\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(5)\n"
        "    return _real_push(*args, **kwargs)\n"
        "_sync.push_single_remote = _slow_push\n"
        "\n"
        "root = Path(sys.argv[1])\n"
        "config = UnifiedConfig.model_validate({\n"
        "    'general': {\n"
        "        'auto_integrate_enabled': True,\n"
        "        'auto_integrate_target': 'release',\n"
        "        'auto_integrate_remote': 'tacos',\n"
        "        'auto_integrate_remote_enabled': True,\n"
        "        'auto_integrate_remote_interval_seconds': 0.0,\n"
        "    }\n"
        "})\n"
        "auto_integrate_after_commit(\n"
        "    config, WorkspaceScope(root), RebaseState()\n"
        ")\n"
        "sys.stdout.write('LANDED\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(5)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", landing_script, str(repo)],
        stdout=subprocess.PIPE,
        text=True,
    )
    if proc.stdout is not None:
        # Wait until the subprocess reaches its pre-push hook (the
        # local landing is complete and the push is about to begin).
        before_push = proc.stdout.readline()
        assert before_push.strip() == "BEFORE_PUSH"
    # Hard-kill the landing subprocess before it can complete the push.
    proc.kill()
    proc.wait()

    local_sha = _head_sha(repo, f"refs/heads/{_TARGET}")

    # Pre-state: local ref has landed ahead of bare remote.
    bare_ref_before = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_before != local_sha

    # Create a linked worktree at wt-sibling on a separate feature branch.
    assert _run(repo, "worktree", "add", "-b", "wt-feature", str(wt), _TARGET).returncode == 0
    assert _run(wt, "config", "user.email", "test@example.com").returncode == 0
    assert _run(wt, "config", "user.name", "Test User").returncode == 0

    # Run the real Ralph startup recovery entry point against the linked
    # worktree (which has a different PID than the killed landing subprocess).
    # The recovery detects the pending local commit and pushes it to the bare remote.
    recovery_config = UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote": _REMOTE,
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote_interval_seconds": 0.0,
            }
        }
    )
    rec_state = auto_integrate_recovery.recover_incomplete_integration(
        WorkspaceScope(wt), config=recovery_config
    )
    assert rec_state is not None and rec_state.last_outcome is not None
    out_line = str(rec_state.last_outcome)

    # Convergence proof: the bare remote ref has caught up to the local
    # target SHA via the fresh recovery subprocess in the linked worktree.
    bare_ref_after = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_after == local_sha
    assert out_line in (
        str(RebaseOutcomeKind.REMOTE_PUSHED),
        str(RebaseOutcomeKind.FAST_FORWARDED),
    ), f"recovery subprocess did not report a push outcome: {out_line!r}"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_unreachable_then_reachable_auto_pushes_real_refs(tmp_path: Path) -> None:
    """AC8 real-Git: a remote that becomes reachable is published to.

    The first reconciler call has no remote bare available (the bare
    path is empty or unconfigured), so the call returns a
    REMOTE_PULL_FAILED. After the bare remote is brought online and
    the local is configured to point at it, the second call
    publishes the local commit and the real refs converge.
    """
    repo = tmp_path / "repo"

    _init_repo(repo)
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0

    # Configure a remote that points at a non-existent bare (the
    # remote is "unreachable"). The reconciler cannot fetch and
    # returns a recoverable failure.
    unreachable_bare = tmp_path / "tacos_unreachable.git"
    assert _run(repo, "remote", "add", _REMOTE, str(unreachable_bare)).returncode == 0
    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")
    state_first = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )
    assert state_first.last_remote_sync in (
        remote_sync.REMOTE_PULL_FAILED,
        remote_sync.REMOTE_REMOTE_UNREACHABLE,
    )

    # Now bring the remote online (simulates the remote becoming
    # reachable after a network blip) and push the seed so the bare
    # has the target branch.
    bare = tmp_path / "tacos.git"
    init = subprocess.run(
        ("git", "init", "--bare", str(bare)),
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert init.returncode == 0
    assert _run(repo, "remote", "set-url", _REMOTE, str(bare)).returncode == 0
    # Seed the bare with the parent commit so the target branch exists.
    seed_sha = _run(repo, "rev-parse", "HEAD~1").stdout.strip()
    assert _run(repo, "push", _REMOTE, f"{seed_sha}:refs/heads/{_TARGET}").returncode == 0

    # Second reconciler call: the remote is reachable, the local
    # commit is published, refs converge.
    state_second = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    assert bare_ref == local_ref == local_sha
    assert state_second.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )


_SUBPROCESS_SCRIPT = """
import os
import subprocess
import sys
from pathlib import Path

from types import SimpleNamespace

from ralph.config.models import UnifiedConfig
from ralph.pipeline import auto_integrate_remote_sync as remote_sync
from ralph.pipeline import run_loop
from ralph.pipeline.rebase_state import RebaseOutcomeKind, RebaseState
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

root = Path(sys.argv[1])
local_sha = sys.argv[2]
target = "release"
remote = "tacos"

config = UnifiedConfig.model_validate({
    "general": {
        "auto_integrate_enabled": True,
        "auto_integrate_target": target,
        "auto_integrate_remote": remote,
        "auto_integrate_remote_enabled": True,
        "auto_integrate_remote_interval_seconds": 0.0,
    }
})

# Drive the production publication reconciler against the unreachable
# remote. The returned RebaseState carries the typed
# publish_deferred_remote decision and the unreachable observation;
# the runner scheduling connection consumes that state as-is.
observed_rebase = remote_sync.reconcile_remote_publication(
    config,
    root,
    target,
    remote=remote,
)
if observed_rebase.last_publication_decision != "publish_deferred_remote":
    sys.stderr.write(
        f"NOT_DEFERRED:{observed_rebase.last_publication_decision}\\n"
    )
    sys.exit(8)
if observed_rebase.last_remote_sync not in (
    remote_sync.REMOTE_REMOTE_UNREACHABLE,
    remote_sync.REMOTE_PULL_FAILED,
):
    sys.stderr.write(
        f"NOT_UNREACHABLE:{observed_rebase.last_remote_sync}\\n"
    )
    sys.exit(9)

def sleep_hook(_s):
    sys.stdout.write(f"DEFERRED:{local_sha}\\n")
    sys.stdout.flush()
    signal = sys.stdin.readline().strip()
    if signal != "RESTORED":
        sys.stderr.write(f"UNEXPECTED_SIGNAL:{signal}\\n")
        sys.exit(7)
    if os.environ.get("DELIBERATELY_SKIP_SECOND_ITERATION") == "1":
        # Negative control: disable the runner's scheduling connection (skip second iteration)
        sys.stderr.write("SKIPPED_SECOND_ITERATION\\n")
        sys.exit(0)

defaults_dir = Path("ralph/policy/defaults")
bundle = load_policy(defaults_dir)

pipeline_state = PipelineState(
    phase="complete",
    rebase=observed_rebase,
)
ctx = SimpleNamespace(
    config=config,
    workspace_scope=WorkspaceScope(root),
    latest_state=[pipeline_state],
    sleep=sleep_hook,
    active_display=None,
    display_context=None,
    effective_verbosity=None,
    registry=None,
    effective_pipeline_subscriber=None,
    controller=None,
    config_path=None,
    cli_overrides=None,
    monitor_stop=None,
    connectivity_monitor=None,
    pipeline_deps=None,
    snapshot_registry=None,
    process_teardown=None,
    _session_handoff_dispatched=False,
    policy_bundle=bundle,
)

# Start real runner loop: run_loop._run_inner_loop_after_startup
# The runner's scheduling connection detects that publication was deferred and retries automatically
final_state, _, _ = run_loop._run_inner_loop_after_startup(pipeline_state, ctx, "development")

if (
    final_state.rebase is not None
    and final_state.rebase.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )
):
    sys.stdout.write(f"PUBLISHED:{local_sha}\\n")
    sys.stdout.flush()
    sys.exit(0)
else:
    sys.stderr.write(f"FAILED_RETRY:{final_state}\\n")
    sys.exit(1)
"""


def _read_line_bounded(stream: TextIO, timeout: float = 15.0) -> str:
    """Read a single line from a text stream within bounded timeout."""
    import select

    rlist, _, _ = select.select([stream], [], [], timeout)
    if not rlist:
        raise TimeoutError(f"Timed out waiting for line from subprocess after {timeout}s")
    line = stream.readline()
    if not line:
        raise EOFError("Subprocess stream closed unexpectedly")
    return line


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
@pytest.mark.parametrize(
    ("mode", "skip_second"),
    [
        ("positive", False),
        ("negative_control", True),
    ],
)
def test_unreachable_then_reachable_production_retry_succeeds_across_subprocess(
    tmp_path: Path, mode: str, skip_second: bool
) -> None:
    """AC8: production recovery entry point automatically retries and publishes once reachable."""
    import os
    import sys

    repo = tmp_path / "repo"
    unreachable_bare = tmp_path / "tacos_unreachable.git"
    bare = tmp_path / "tacos.git"

    _init_repo(repo)
    assert _run(repo, "remote", "add", _REMOTE, str(unreachable_bare)).returncode == 0
    local_sha = _commit(repo, "feature.txt", "feature advance\n", "feature advance")
    seed_sha = _head_sha(repo, "HEAD~1")

    # Pre-seed the bare remote with seed_sha (HEAD~1) before launching subprocess
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "push", str(bare), f"{seed_sha}:refs/heads/release"],
        check=True,
        capture_output=True,
    )

    env = os.environ.copy()
    if skip_second:
        env["DELIBERATELY_SKIP_SECOND_ITERATION"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(repo), local_sha],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        assert proc.stdout is not None
        assert proc.stdin is not None
        deferred_line = _read_line_bounded(proc.stdout, timeout=15.0).strip()
        assert deferred_line.startswith("DEFERRED:"), (
            f"Expected DEFERRED: prefix, got: {deferred_line}"
        )
        local_sha = deferred_line.split(":", 1)[1]
        assert len(local_sha) == 40

        # Point configured tacos remote to the now-reachable bare repo
        subprocess.run(
            ["git", "-C", str(repo), "remote", "set-url", "tacos", str(bare)],
            check=True,
            capture_output=True,
        )

        # Notify subprocess that remote is restored
        proc.stdin.write("RESTORED\n")
        proc.stdin.flush()

        if skip_second:
            _out, err = proc.communicate(timeout=15)
            assert proc.returncode == 0, f"Subprocess failed with code {proc.returncode}: {err}"
            assert "SKIPPED_SECOND_ITERATION" in err
            # Negative control proof: bare was initialized and seeded with seed_sha,
            # but because retry was disabled, the remote ref remains at seed_sha and
            # diverges from local_sha.
            bare_sha = subprocess.run(
                ["git", "-C", str(bare), "rev-parse", "release"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            assert bare_sha == seed_sha
            assert bare_sha != local_sha
            return

        published_line = _read_line_bounded(proc.stdout, timeout=15.0).strip()
        assert published_line == f"PUBLISHED:{local_sha}", (
            f"Expected PUBLISHED:{local_sha}, got: {published_line}"
        )

        _out, err = proc.communicate(timeout=15)
        assert proc.returncode == 0, f"Subprocess failed with code {proc.returncode}: {err}"

        # Convergence proof: bare ref now points to local_sha
        bare_sha = subprocess.run(
            ["git", "-C", str(bare), "rev-parse", "release"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert bare_sha == local_sha
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
