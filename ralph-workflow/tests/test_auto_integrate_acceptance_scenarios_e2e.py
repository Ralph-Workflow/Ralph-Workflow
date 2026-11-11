from __future__ import annotations

import functools
import shutil
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
_COPY_IGNORE = shutil.ignore_patterns("hooks", "*.sample", "description", "branches")


def _fast_branch_name(root: Path) -> str | None:
    git_dir = root / ".git" if (root / ".git").is_dir() else root
    head_file = git_dir / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
        if head_content.startswith("ref: refs/heads/"):
            return head_content.removeprefix("ref: refs/heads/")
    except OSError:
        pass
    return None


def _fast_rev_parse(root: Path, ref_name: str) -> str | None:
    git_dir = root / ".git" if (root / ".git").is_dir() else root
    try:
        if ref_name == "HEAD":
            head_file = git_dir / "HEAD"
            head_content = head_file.read_text(encoding="utf-8").strip()
            if head_content.startswith("ref: refs/heads/"):
                target_ref = git_dir / head_content.removeprefix("ref: ")
                if target_ref.is_file():
                    return target_ref.read_text(encoding="utf-8").strip()
            elif len(head_content) == 40:
                return head_content
        else:
            ref_file = git_dir / "refs" / "heads" / ref_name
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8").strip()
            packed_refs_file = git_dir / "packed-refs"
            if packed_refs_file.is_file():
                for line in packed_refs_file.read_text(encoding="utf-8").splitlines():
                    if line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split()
                    if len(parts) == 2 and parts[1].endswith(f"/{ref_name}"):
                        return parts[0]
    except OSError:
        pass
    return None


def _run(repo_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    if args == ("branch", "--show-current"):
        name = _fast_branch_name(repo_root)
        if name is not None:
            return subprocess.CompletedProcess(
                args=("git", *args), stdout=name + "\n", stderr="", returncode=0
            )
    if len(args) == 2 and args[0] == "rev-parse" and not args[1].startswith("-"):
        sha = _fast_rev_parse(repo_root, args[1])
        if sha is not None:
            return subprocess.CompletedProcess(
                args=("git", *args), stdout=sha + "\n", stderr="", returncode=0
            )
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
    from git import Repo

    repo = Repo(repo_root)
    repo.index.add([filename])
    commit = repo.index.commit(message)
    assert commit.hexsha
    return commit.hexsha


def _init_repo(path: Path, default_branch: str = _TARGET) -> None:
    path.mkdir(parents=True, exist_ok=True)
    from git import Repo

    repo = Repo.init(path)
    with repo.config_writer() as cfg:
        cfg.set_value("user", "email", "test@example.com")
        cfg.set_value("user", "name", "Test User")
        cfg.set_value("core", "logAllRefUpdates", "false")
        cfg.set_value("commit", "gpgSign", "false")
        cfg.set_value("gc", "auto", "0")
        cfg.set_value("core", "fsync", "none")
    repo.close()
    _commit(path, "seed.txt", "seed\n", "seed")
    assert _run(path, "branch", "-M", default_branch).returncode == 0


_SEED_TEMPLATE: list[Path] = []


@pytest.fixture(scope="session", autouse=True)
def _seed_repo_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    template_root = tmp_path_factory.mktemp("acceptance-seed")
    _init_repo(template_root)
    _SEED_TEMPLATE.append(template_root)
    return template_root


def _seed_repo(path: Path, default_branch: str = _TARGET) -> None:
    template = _SEED_TEMPLATE[0] if _SEED_TEMPLATE else None
    if template is None:
        _init_repo(path, default_branch)
        return
    shutil.copytree(template, path, ignore=_COPY_IGNORE)
    if default_branch != _TARGET:
        assert _run(path, "branch", "-M", default_branch).returncode == 0


def _head_sha(repo_root: Path, ref: str) -> str:
    return _run(repo_root, "rev-parse", ref).stdout.strip()


@functools.lru_cache(maxsize=16)
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
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"
    writer = tmp_path / "writer"

    _seed_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0

    assert _run(tmp_path, "clone", str(bare), str(writer)).returncode == 0
    assert _run(writer, "config", "user.email", "test@example.com").returncode == 0
    assert _run(writer, "config", "user.name", "Test User").returncode == 0
    assert _run(writer, "remote", "add", _REMOTE, str(bare)).returncode == 0
    remote_advance_sha = _commit(writer, "remote.txt", "remote work\n", "remote advance")
    assert _run(writer, "push", _REMOTE, _TARGET).returncode == 0

    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    assert _run(repo, "fetch", _REMOTE).returncode == 0

    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    assert _head_sha(repo, f"refs/remotes/{_REMOTE}/{_TARGET}") == remote_advance_sha
    assert local_sha != remote_advance_sha

    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    remote_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == remote_ref, f"local {local_ref} != remote {remote_ref} after reconciliation"
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
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"

    _seed_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    cfg = _build_config(interval_seconds=60.0)

    remote_sync._REMOTE_PULL_THROTTLE.arm(repo, _REMOTE, _TARGET, clock=time.monotonic)

    pull_record = remote_sync.pull_and_reconcile_target(
        cfg,
        repo,
        _TARGET,
    )
    assert pull_record is not None
    assert pull_record.last_remote_sync == "refresh suppressed"
    assert pull_record.last_refresh == "refresh suppressed by throttle"

    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    state = remote_sync.reconcile_remote_publication(
        cfg,
        repo,
        _TARGET,
        source_state=pull_record,
    )

    local_ref = _head_sha(repo, f"refs/heads/{_TARGET}")
    bare_ref = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert local_ref == bare_ref == local_sha
    assert state.last_push_status == PushStatus.PUSHED
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )

    remotes = _run(repo, "remote").stdout.split()
    assert "origin" not in remotes
    branches = _run(repo, "branch", "-a").stdout
    assert "main" not in branches


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_throttle_deferred_probe_still_publishes_concrete_refs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"

    _seed_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    bare_pre = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_pre != local_sha

    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

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
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"

    _seed_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    local_sha = _commit(repo, "local.txt", "local work\n", "local advance")

    assert _head_sha(repo, f"refs/heads/{_TARGET}") == local_sha
    bare_ref_before = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_before != local_sha

    state = remote_sync.reconcile_remote_publication(
        _build_config(),
        repo,
        _TARGET,
        remote=_REMOTE,
    )

    bare_ref_after = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_after == local_sha
    assert state.last_outcome in (
        RebaseOutcomeKind.REMOTE_PUSHED,
        RebaseOutcomeKind.FAST_FORWARDED,
    )


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_killed_between_landing_and_push_recovers_from_linked_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    bare = tmp_path / "tacos.git"
    wt = tmp_path / "wt-sibling"

    _seed_repo(repo)
    assert _run(tmp_path, "clone", "--bare", str(repo), str(bare)).returncode == 0
    assert _run(repo, "remote", "add", _REMOTE, str(bare)).returncode == 0
    assert _run(repo, "push", _REMOTE, _TARGET).returncode == 0
    assert _run(repo, "fetch", _REMOTE).returncode == 0

    import sys

    assert _run(repo, "switch", "-c", "feature").returncode == 0
    _commit(repo, "feature.txt", "feature work\n", "feature advance")

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
        before_push = proc.stdout.readline()
        assert before_push.strip() == "BEFORE_PUSH"
    proc.kill()
    proc.wait()

    local_sha = _head_sha(repo, f"refs/heads/{_TARGET}")

    bare_ref_before = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_before != local_sha

    assert _run(repo, "worktree", "add", "-b", "wt-feature", str(wt), _TARGET).returncode == 0
    assert _run(wt, "config", "user.email", "test@example.com").returncode == 0
    assert _run(wt, "config", "user.name", "Test User").returncode == 0

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

    bare_ref_after = _run(bare, "rev-parse", _TARGET).stdout.strip()
    assert bare_ref_after == local_sha
    assert out_line in (
        str(RebaseOutcomeKind.REMOTE_PUSHED),
        str(RebaseOutcomeKind.FAST_FORWARDED),
    ), f"recovery subprocess did not report a push outcome: {out_line!r}"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_unreachable_then_reachable_auto_pushes_real_refs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"

    _seed_repo(repo)
    assert _run(repo, "config", "user.email", "test@example.com").returncode == 0
    assert _run(repo, "config", "user.name", "Test User").returncode == 0

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
    seed_sha = _run(repo, "rev-parse", "HEAD~1").stdout.strip()
    assert _run(repo, "push", _REMOTE, f"{seed_sha}:refs/heads/{_TARGET}").returncode == 0

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
    },
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

attempt = 0

def sleep_hook(_s):
    global attempt
    attempt += 1
    sys.stdout.write(f"DEFERRED:{attempt}:{local_sha}\\n")
    sys.stdout.flush()
    signal = sys.stdin.readline().strip()
    if signal == "RETRY":
        if os.environ.get("DELIBERATELY_SKIP_SECOND_ITERATION") == "1":
            # Negative control: disable the runner's scheduling connection (skip second iteration)
            sys.stderr.write("SKIPPED_SECOND_ITERATION\\n")
            sys.exit(0)
        return
    elif signal == "RESTORED":
        return
    else:
        sys.stderr.write(f"UNEXPECTED_SIGNAL:{signal}\\n")
        sys.exit(7)

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


def _read_line_bounded(stream: TextIO, timeout: float = 30.0) -> str:
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
    import os
    import sys
    import tempfile

    repo = tmp_path / "repo"
    unreachable_bare = tmp_path / "tacos_unreachable.git"
    bare = tmp_path / "tacos.git"

    _seed_repo(repo)
    assert _run(repo, "remote", "add", _REMOTE, str(unreachable_bare)).returncode == 0
    local_sha = _commit(repo, "feature.txt", "feature advance\n", "feature advance")
    seed_sha = _head_sha(repo, "HEAD~1")

    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "push", str(bare), f"{seed_sha}:refs/heads/release"],
        check=True,
        capture_output=True,
    )

    env = os.environ.copy()
    if skip_second:
        env["DELIBERATELY_SKIP_SECOND_ITERATION"] = "1"

    with tempfile.TemporaryFile(mode="w+") as stderr_file:
        proc = subprocess.Popen(
            [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(repo), local_sha],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
        )

        try:
            assert proc.stdout is not None
            assert proc.stdin is not None

            for attempt_expected in (1, 2, 3):
                line = _read_line_bounded(proc.stdout, timeout=30.0).strip()
                assert line == f"DEFERRED:{attempt_expected}:{local_sha}", (
                    f"Expected DEFERRED:{attempt_expected}:{local_sha}, got: {line}"
                )
                if skip_second:
                    proc.stdin.write("RETRY\n")
                    proc.stdin.flush()
                    _out, _ = proc.communicate(timeout=30)
                    stderr_file.seek(0)
                    err = stderr_file.read()
                    assert proc.returncode == 0, (
                        f"Subprocess failed with code {proc.returncode}: {err}"
                    )
                    assert "SKIPPED_SECOND_ITERATION" in err
                    bare_sha = subprocess.run(
                        ["git", "-C", str(bare), "rev-parse", "release"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.strip()
                    assert bare_sha == seed_sha
                    assert bare_sha != local_sha
                    return

                if attempt_expected < 3:
                    proc.stdin.write("RETRY\n")
                    proc.stdin.flush()
                else:
                    subprocess.run(
                        ["git", "-C", str(repo), "remote", "set-url", "tacos", str(bare)],
                        check=True,
                        capture_output=True,
                    )
                    proc.stdin.write("RESTORED\n")
                    proc.stdin.flush()

            published_line = _read_line_bounded(proc.stdout, timeout=30.0).strip()
            assert published_line == f"PUBLISHED:{local_sha}", (
                f"Expected PUBLISHED:{local_sha}, got: {published_line}"
            )

            _out, _ = proc.communicate(timeout=15)
            stderr_file.seek(0)
            err = stderr_file.read()
            assert proc.returncode == 0, f"Subprocess failed with code {proc.returncode}: {err}"

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
