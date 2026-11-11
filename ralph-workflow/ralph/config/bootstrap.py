"""Bootstrap helpers for creating user-global and project-local config files.

Auto-creates the user-global Ralph config set on first run, including
~/.config/ralph-workflow.toml, ~/.config/ralph-workflow-agents.toml,
~/.config/ralph-workflow-mcp.toml, ~/.config/ralph-workflow-pipeline.toml,
and ~/.config/ralph-workflow-artifacts.toml from bundled templates.
Also supports regenerating configs with .bak backups via --regenerate-config.

Bootstrap creates the standard user-global config set:
  - ~/.config/ralph-workflow.toml
  - ~/.config/ralph-workflow-agents.toml
  - ~/.config/ralph-workflow-mcp.toml
  - ~/.config/ralph-workflow-pipeline.toml
  - ~/.config/ralph-workflow-artifacts.toml

Project-local overrides are created only by the explicit local-config flow.
General regeneration refreshes only local TOMLs that already exist, including
.agent/ralph-workflow.toml, .agent/mcp.toml, .agent/pipeline.toml,
.agent/artifacts.toml, and the optional advanced .agent/agents.toml.

The agents configuration and the config-write commit routing it shares with
the other config files live in :mod:`ralph.config.agents_bootstrap`.

The explicit local-config flow also seeds a batteries-included .gitignore with
Ralph-local, Python, Node, Rust, Go, Ruby, PHP, Java/Kotlin, .NET, Dart/Flutter,
Elixir, Scala, Terraform, IDE, and OS metadata patterns (see
_DEFAULT_GITIGNORE_PATTERNS).
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from git import InvalidGitRepositoryError, NoSuchPathError, Repo
from loguru import logger

from ralph.config._agent_internal_globs import (
    _AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB,
    AGENT_INTERNAL_DIR_GLOBS,
    AGENT_INTERNAL_ROOT_BASENAMES,
    AGENT_INTERNAL_TOP_LEVEL_BASENAMES,
)
from ralph.config._config_filenames import (
    _ADVANCED_LOCAL_POLICY_FILENAMES,
    _GLOBAL_AGENTS_FILENAME,
    _GLOBAL_CONFIG_FILENAME,
    _GLOBAL_MCP_FILENAME,
    _GLOBAL_POLICY_FILENAME_MAP,
    _LOCAL_CONFIG_FILENAME,
    _LOCAL_CONFIG_SOURCE,
    _LOCAL_MCP_FILENAME,
    _LOCAL_POLICY_FILENAMES,
)
from ralph.config._paths import resolve_global_config_dir as _resolve_global_config_dir
from ralph.config.agents_bootstrap import (
    BootstrapResult,
    _commit_config_write,
    _prepare_config_commit,
    write_agents_with_backup,
)
from ralph.config.agents_file_writer import write_agents_toml
from ralph.config.loader import load_toml
from ralph.config.unreadable_config_finding import (
    UnreadableConfigFinding,
    collect_unreadable_config_files,
)
from ralph.git.operations import _atomic_append_text, create_commit, stage_files
from ralph.git.scoped_auto_commit import (
    ScopedCommitStatus,
    classify_target_for_commit,
    commit_deterministic_writes,
)
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import write_text_if_changed

if TYPE_CHECKING:
    from types import ModuleType

__all__ = [
    "BootstrapResult",
    "Repo",
    "ScopedCommitStatus",
    "UnreadableConfigFinding",
    "auto_seed_default_git_exclude",
    "auto_seed_default_gitignore",
    "classify_target_for_commit",
    "collect_unreadable_config_files",
    "commit_deterministic_writes",
    "create_commit",
    "ensure_global_agents_config",
    "ensure_global_config",
    "ensure_global_mcp_config",
    "ensure_global_policy_configs",
    "ensure_local_configs",
    "ensure_local_main_config",
    "ensure_local_support_configs",
    "regenerate_all",
    "resolve_global_config_dir",
    "stage_files",
    "write_agents_toml",
]


def resolve_global_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the user-global config directory."""
    return _resolve_global_config_dir(env)


_DEFAULT_GITIGNORE_PATTERNS: tuple[str, ...] = (
    # Ralph Workflow local artifacts (existing — DO NOT REORDER).
    # The first entry ``.agent/`` already covers the
    # ``.agent/ralph-explore/`` disposable cache by virtue of its
    # trailing slash. The explicit child rule below is added to
    # satisfy the prompt's "managed gitignore child entry"
    # requirement (AC-05) without reordering the protected entries
    # above: ``auto_seed_default_gitignore`` only appends missing
    # patterns, so the parent ``.agent/`` rule is preserved and
    # the explicit child rule is added next to the parent.
    ".agent/",
    ".agent/ralph-explore/",
    "/PROMPT*",
    "wt-*/",
    "/checkpoint.json",
    # Python
    "__pycache__/",
    "*.py[codz]",
    "*$py.class",
    ".venv/",
    "venv/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".coverage",
    "htmlcov/",
    "dist/",
    "build/*",
    "!build/.gitkeep",
    "*.egg-info/",
    # Python extras
    ".tox/",
    ".nox/",
    ".pdm-build/",
    "*.pyo",
    ".ipynb_checkpoints/",
    "pip-wheel-metadata/",
    # Node
    "node_modules/",
    ".next/",
    ".nuxt/",
    # Node extras
    "coverage/",
    "*.tsbuildinfo",
    ".parcel-cache/",
    ".cache/",
    ".env",
    ".env.local",
    ".env.*.local",
    # Rust (Cargo.lock intentionally NOT ignored — Rust apps check it in)
    "target/*",
    "!target/.gitkeep",
    "**/*.rs.bk",
    # Go (vendor/ is opt-in; go.sum intentionally NOT ignored)
    "vendor/*",
    # Ruby (note: vendor/bundle/.gitkeep cannot be allowlisted here because
    # the vendor/* pattern above also matches it. A user who tracks
    # vendor/bundle/.gitkeep can add a one-line !vendor/bundle/.gitkeep
    # to their project-local gitignore — the appended default cannot honor
    # both the Go "ignore all of vendor/" and the Ruby "track this
    # marker in vendor/bundle/" semantics simultaneously).
    "vendor/bundle/*",
    ".bundle/",
    "log/",
    "tmp/",
    "*.gem",
    # PHP
    ".phpunit.cache/",
    "/storage/*.key",
    "composer.phar",
    # Java/Kotlin extras (paired with existing .idea/ below)
    ".gradle/*",
    "!.gradle/.gitkeep",
    "*.class",
    "*.jar",
    # .NET / Visual Studio
    "bin/*",
    "!bin/.gitkeep",
    "obj/*",
    "!obj/.gitkeep",
    "*.user",
    "*.suo",
    "*.userosscache",
    "*.sln.docstates",
    "[Dd]ebug/",
    "[Rr]elease/",
    "x64/",
    "x86/",
    "*.dll",
    "*.exe",
    "*.pdb",
    # Dart / Flutter
    ".dart_tool/",
    ".flutter-plugins",
    ".flutter-plugins-dependencies",
    ".packages",
    ".pub-cache/",
    ".pub/",
    # Elixir
    "_build/*",
    "!_build/.gitkeep",
    "deps/*",
    "!deps/.gitkeep",
    ".elixir_ls/",
    "cover/",
    "doc/",
    "fetch.*.exs",
    # Scala / Metals / BSP
    ".bsp/",
    ".metals/",
    "project/target/",
    "project/project/",
    # Terraform (note: *.tfvars is in the positive list; *.tfvars.example
    # is intentionally NOT allowlisted — the user must explicitly add
    # tracked example files).
    ".terraform/",
    "*.tfstate",
    "*.tfstate.*",
    ".terraform.lock.hcl",
    "terraform.tfvars",
    "crash.log",
    "crash.*.log",
    "*.tfvars",
    # Editors / IDEs extra
    ".fleet/",
    ".cursor/",
    ".windsurf/",
    ".idea_modules/",
    "*.iml",
    # Editors / IDEs (NOTE: .vscode/ intentionally NOT included —
    # the upstream repository has tracked files under .vscode/ that
    # a blanket ignore would hide.)
    ".idea/",
    "*.swp",
    "*.swo",
    # OS metadata
    ".DS_Store",
    "Thumbs.db",
    # OS metadata extras
    "Desktop.ini",
    "ehthumbs.db",
    "$RECYCLE.BIN/",
)


# Machine-local exclude patterns for ``.git/info/exclude``. These never enter
# the repo -- they keep the user-level state out of every clone.
#
# Every pattern here is the canonical on-disk form (NOT the Python abstraction
# identifier). The completion-sentinel glob is ``completion_seen_*.json``
# (the canonical on-disk filename pattern -- confirmed against
# ``COMPLETION_SENTINEL_RELPATHFMT`` in ``ralph.mcp.tools.coordination``).
# Root-anchored ``/checkpoint.json`` is used (NOT bare ``checkpoint.json``
# which would silently match every nested directory).
#
# The engine-internal globs come from ``ralph.config._agent_internal_globs``,
# which loads the canonical leaf module by file path so this module never
# transitively triggers ``ralph.phases.__init__.py``.
_DEFAULT_GIT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    # Engine-internal directories under .agent/ -- everything inside is engine-owned.
    *tuple(f".agent/{dir_name}/" for dir_name in sorted(AGENT_INTERNAL_DIR_GLOBS)),
    # Completion sentinels -- on-disk filename glob, NOT Python abstraction identifier.
    f".agent/{_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB}",
    # Engine-internal top-level files under .agent/.
    *tuple(f".agent/{name}" for name in sorted(AGENT_INTERNAL_TOP_LEVEL_BASENAMES)),
    # Root-anchored root basenames (only checkpoint.json today). The leading
    # slash matches only at the repo root -- bare ``checkpoint.json`` would
    # silently match every nested directory (PA-002).
    *tuple(f"/{name}" for name in sorted(AGENT_INTERNAL_ROOT_BASENAMES)),
)


def _module_attr_or_none(module: ModuleType, attribute: str) -> object | None:
    namespace = cast("dict[str, object]", module.__dict__)
    return namespace.get(attribute)


def _get_bundled_defaults_dir() -> Path:
    """Return the path to the bundled default policy files.

    Computed lazily to avoid circular import: ralph.policy.loader imports
    ralph.phases which imports ralph.pipeline which imports ralph.config.
    """
    policy_module = import_module("ralph.policy")
    policy_file = _module_attr_or_none(policy_module, "__file__")
    if not isinstance(policy_file, str):
        raise RuntimeError("ralph.policy module has no __file__")
    return Path(policy_file).parent / "defaults"


def ensure_global_config(global_dir: Path | None = None, *, force: bool = False) -> BootstrapResult:
    """Ensure ~/.config/ralph-workflow.toml exists, creating it from the bundled template.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        force: When True, overwrite an existing file (backs it up to <name>.bak first).

    Returns:
        BootstrapResult describing the action taken.
    """
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    target = global_dir / _GLOBAL_CONFIG_FILENAME
    source = _get_bundled_defaults_dir() / _GLOBAL_CONFIG_FILENAME
    result = _copy_with_backup(source, target, force)
    if result.action == "skipped":
        migrated = _migrate_legacy_global_config(target)
        if migrated is not None:
            return migrated
    return result


def ensure_global_agents_config(
    global_dir: Path | None = None,
    *,
    force: bool = False,
    create_if_missing: bool = False,
) -> BootstrapResult:
    """Handle the user-global agents file without implicit first-run creation.

    Agent CLI definitions are transport plumbing (binary, flags, output
    parser), so they live apart from the main config, which opens on the
    ``[agent_chains]`` operators actually edit.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        force: When True, overwrite an existing file (backs it up to <name>.bak first).
        create_if_missing: Create a missing file for the explicit ``ralph --init`` route.

    Returns:
        BootstrapResult describing the action taken.
    """
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    target = global_dir / _GLOBAL_AGENTS_FILENAME
    source = _get_bundled_defaults_dir() / _GLOBAL_AGENTS_FILENAME
    if not target.exists() and not (force or create_if_missing):
        return BootstrapResult(target, "skipped", None)
    return write_agents_with_backup(
        source,
        target,
        force,
        trigger="ralph --regenerate-config" if force else "ralph --init",
    )


def ensure_global_mcp_config(
    global_dir: Path | None = None, *, force: bool = False
) -> BootstrapResult:
    """Ensure ~/.config/ralph-workflow-mcp.toml exists, creating it from the bundled template.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        force: When True, overwrite an existing file (backs it up to <name>.bak first).

    Returns:
        BootstrapResult describing the action taken.
    """
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    target = global_dir / _GLOBAL_MCP_FILENAME
    source = _get_bundled_defaults_dir() / "mcp.toml"
    return _copy_with_backup(source, target, force)


def ensure_global_policy_configs(
    global_dir: Path | None = None, *, force: bool = False
) -> list[BootstrapResult]:
    """Ensure the user-global policy defaults exist.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        force: When True, overwrite existing files (backs them up first).

    Returns:
        List of BootstrapResult, one per global policy file.
    """
    if global_dir is None:
        global_dir = resolve_global_config_dir()
    return [
        _copy_with_backup(
            _resolve_global_policy_source(global_dir, policy_filename, force=force),
            global_dir / _global_policy_target_name(policy_filename),
            force,
        )
        for policy_filename in _LOCAL_POLICY_FILENAMES
    ]


def ensure_local_main_config(agent_dir: Path, *, force: bool = False) -> BootstrapResult:
    """Ensure the project-local main override exists.

    Args:
        agent_dir: The .agent directory to write configs into.
        force: When True, overwrite an existing file (backing it up first).

    Returns:
        BootstrapResult describing the action taken for `.agent/ralph-workflow.toml`.
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    global_source = resolve_global_config_dir() / _GLOBAL_CONFIG_FILENAME
    source = (
        global_source
        if global_source.exists()
        else _get_bundled_defaults_dir() / _LOCAL_CONFIG_SOURCE
    )
    return _copy_with_backup(
        source,
        agent_dir / _LOCAL_CONFIG_FILENAME,
        force,
    )


def ensure_local_support_configs(agent_dir: Path, *, force: bool = False) -> list[BootstrapResult]:
    """Ensure the standard project-local policy and MCP files exist.

    This scaffolds the `.agent/` files Ralph needs for project-local runtime behavior
    without creating the optional project-local main override.

    Args:
        agent_dir: The .agent directory to write configs into.
        force: When True, overwrite existing files (backs them up first).

    Returns:
        List of BootstrapResult, one per support file.
    """
    agent_dir.mkdir(parents=True, exist_ok=True)
    global_dir = resolve_global_config_dir()
    global_mcp_source = global_dir / _GLOBAL_MCP_FILENAME
    mcp_source = (
        global_mcp_source
        if global_mcp_source.exists()
        else _get_bundled_defaults_dir() / "mcp.toml"
    )
    results: list[BootstrapResult] = [
        _copy_with_backup(
            mcp_source,
            agent_dir / _LOCAL_MCP_FILENAME,
            force,
        )
    ]
    results.extend(
        _copy_with_backup(
            _resolve_global_policy_source(global_dir, policy_filename, force=False),
            agent_dir / policy_filename,
            force,
        )
        for policy_filename in _LOCAL_POLICY_FILENAMES
    )
    _ensure_default_gitignore(agent_dir.parent)
    _ensure_default_git_exclude(agent_dir.parent)
    return results


def ensure_local_configs(agent_dir: Path, *, force: bool = False) -> list[BootstrapResult]:
    """Ensure the full project-local config set exists.

    Args:
        agent_dir: The .agent directory to write configs into.
        force: When True, overwrite existing files (backs them up first).

    Returns:
        List of BootstrapResult, one per config file.
    """
    return [
        ensure_local_main_config(agent_dir, force=force),
        *ensure_local_support_configs(agent_dir, force=force),
    ]


def _ensure_default_gitignore(repo_root: Path) -> None:
    """Append missing default .gitignore patterns and commit the change.

    wt-012 DA-001/DA-008: this used to append via ``append_to_gitignore``
    and return without committing, leaving a deterministic write dirty.
    It now routes through :func:`auto_seed_default_gitignore`, which
    commits via the producer-level
    :func:`ralph.git.scoped_auto_commit.commit_deterministic_writes`
    isolation primitive (and stays a safe no-op append in a non-git
    workspace).
    """
    auto_seed_default_gitignore(repo_root)


def _ensure_default_git_exclude(repo_root: Path) -> None:
    """Append the canonical engine-internal patterns to ``.git/info/exclude``.

    Uses ``add_to_git_exclude`` from ``ralph.git.commit_review`` which is
    idempotent and preserves user-added entries. Idempotent on its own --
    the helper will simply return ``[]`` when every default pattern is
    already present.

    Graceful no-op when ``repo_root`` is not a git repository: the
    underlying ``add_to_git_exclude`` calls ``Repo(repo_root)`` which
    raises ``InvalidGitRepositoryError`` if there is no ``.git`` directory.
    Bootstrap is called from non-git working trees (e.g. ``ensure_local_configs``
    in unit tests that pass a bare ``tmp_path``); failing here would break
    those tests for no real benefit.

    Raises:
        OSError: When the underlying filesystem operation fails.
    """
    from git import InvalidGitRepositoryError

    from ralph.git.commit_review import add_to_git_exclude

    try:
        add_to_git_exclude(repo_root, list(_DEFAULT_GIT_EXCLUDE_PATTERNS))
    except InvalidGitRepositoryError:
        # Non-git working tree -- nothing to seed. The gitignore path
        # (``_ensure_default_gitignore``) also does not require a git repo.
        return


def _resolve_git_exclude_path(repo_root: Path) -> Path | None:
    """Return the resolved path to ``info/exclude`` for ``repo_root``.

    Works for normal repositories AND git worktrees / separate-git-dir
    layouts. In a worktree the top-level ``.git`` is a *file* containing
    ``gitdir: <real-gitdir>``; the real ``info/exclude`` lives in
    ``<real-gitdir>/info/exclude``, NOT in ``repo_root/.git/info/exclude``.
    Resolving via GitPython's ``Repo.git_dir`` works for both layouts.

    Returns ``None`` when ``repo_root`` is not a git repository. In that
    case the caller falls back to the simple ``repo_root/.git/info/exclude``
    path so the helper still seeds the file when invoked in a project
    that has not yet been ``git init``-ed (Ralph creates the project-local
    config set on every invocation).
    """
    try:
        repo = Repo(repo_root, search_parent_directories=False)
    except (InvalidGitRepositoryError, NoSuchPathError):
        return None
    try:
        git_dir = Path(repo.git_dir)
    finally:
        repo.close()
    return git_dir / "info" / "exclude"


def auto_seed_default_git_exclude(repo_root: Path) -> list[str]:
    """Auto-seed ``.git/info/exclude`` on a normal ``ralph`` run.

    Mirrors ``auto_seed_default_gitignore`` but for the per-user
    ``.git/info/exclude`` file. Reads the existing file (if any), computes
    the patterns from ``_DEFAULT_GIT_EXCLUDE_PATTERNS`` that are not
    already present, appends the missing patterns to the resolved
    exclude file, and returns the list of patterns that were actually
    appended.

    The git directory is resolved via ``Repo(repo_root).git_dir`` so the
    helper works for normal repositories, git worktrees, and
    separate-git-dir layouts. In a worktree the top-level ``.git`` is a
    gitfile pointing at the real gitdir; blindly using
    ``repo_root / '.git' / 'info' / 'exclude'`` would call ``mkdir`` on a
    file and fail with ``NotADirectoryError``. Falls back to the
    repo-root layout only when ``repo_root`` is not a git repository.

    Idempotent: a second call with the same ``repo_root`` returns ``[]``
    when every default pattern is already present. Does NOT clobber
    user-added entries.

    Tolerates a missing git dir: when ``repo_root`` is not a git
    repository (e.g. first-run bootstrap before ``git init``), the helper
    writes ``.git/info/exclude`` directly into the filesystem, creating
    the parent dirs as needed.

    Args:
        repo_root: Path to the repository (or project) root.

    Returns:
        List of patterns that were appended on this call. Empty when the
        existing file already covered every default pattern.
    """
    exclude_path = _resolve_git_exclude_path(repo_root)
    if exclude_path is None:
        # Non-git working tree -- seed the conventional repo_root/.git/info/exclude.
        exclude_path = repo_root / ".git" / "info" / "exclude"
    existing: set[str] = set()
    file_existed = exclude_path.exists()
    if file_existed:
        try:
            existing = set(exclude_path.read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            # Non-fatal: best-effort seeding must not block the run, but
            # silently assuming "the file is empty" would cause every
            # default pattern to be appended (potential duplicates on
            # retry once the read problem is fixed). Warn so the
            # operator can investigate the read failure without
            # breaking the run.
            logger.warning(
                "Could not read existing git exclude at {}: {}. "
                "Proceeding as if the file were empty; this may append "
                "duplicate patterns on a later run. Check file permissions "
                "and ownership to remove the warning.",
                exclude_path,
                exc,
            )
            existing = set()
    missing = [p for p in _DEFAULT_GIT_EXCLUDE_PATTERNS if p not in existing]
    if missing:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(missing) + "\n"
        _atomic_append_text(exclude_path, payload)
    return list(missing)


def auto_seed_default_gitignore(repo_root: Path) -> list[str]:
    """Auto-seed the batteries-included .gitignore on a normal `ralph` run.

    Reads the existing ``.gitignore`` (if any), computes the patterns from
    ``_DEFAULT_GITIGNORE_PATTERNS`` that are not already present, appends
    them via ``append_to_gitignore`` (which filters duplicates by line), and
    returns the list of patterns that were actually appended.

    Idempotent: a second call with the same ``repo_root`` returns ``[]``
    when every default pattern is already present (the underlying
    ``append_to_gitignore`` short-circuits when nothing is missing). Does
    NOT clobber user-added lines — user-customized patterns are preserved.

    Handles the no-git case: the helper just touches ``.gitignore`` in
    ``repo_root``; it does not require a ``.git`` directory to exist.

    wt-012: when the seed actually appended new patterns, this helper
    commits the ``.gitignore`` change via the producer-level
    :func:`ralph.git.scoped_auto_commit.commit_deterministic_writes`
    isolation primitive. The pre-write content hash is recorded BEFORE
    the append so a user mid-edit on ``.gitignore`` is never swept into
    the deterministic commit. A non-git workspace (or any git error)
    is logged at DEBUG and never blocks the run.

    Args:
        repo_root: Path to the repository (or project) root.

    Returns:
        List of patterns that were appended on this call. Empty when the
        existing ``.gitignore`` already covered every default pattern.
    """
    gitignore_path = repo_root / ".gitignore"
    existing: set[str] = set()
    if gitignore_path.exists():
        try:
            existing = set(gitignore_path.read_text(encoding="utf-8").splitlines())
        except OSError as exc:
            # Non-fatal: best-effort seeding must not block the run, but
            # silently assuming "the file is empty" would cause every
            # default pattern to be appended (potential duplicates on
            # retry once the read problem is fixed). Warn so the
            # operator can investigate the read failure without
            # breaking the run.
            logger.warning(
                "Could not read existing .gitignore at {}: {}. "
                "Proceeding as if the file were empty; this may append "
                "duplicate patterns on a later run. Check file permissions "
                "and ownership to remove the warning.",
                gitignore_path,
                exc,
            )
            existing = set()
    missing = [p for p in _DEFAULT_GITIGNORE_PATTERNS if p not in existing]
    appended: list[str] = []
    if missing:
        # Record the pre-write content hash BEFORE the append so the
        # deterministic auto-commit can detect a user mid-edit on
        # ``.gitignore`` and SKIP the path (it stays in the agent /
        # user commit flow). A non-git workspace returns ``None`` for
        # the pre-write hash; the commit helper handles that as
        # NOT_REPO and returns a NOOP, so the seed is still safe to
        # leave the file dirty in a non-git project.
        from ralph.git.operations import (
            create_commit,
        )
        from ralph.git.scoped_auto_commit import (
            ScopedCommitStatus,
            capture_pre_write_contents,
            commit_deterministic_writes,
        )

        pre_contents = capture_pre_write_contents(repo_root, [".gitignore"])
        gitignore_path = repo_root / ".gitignore"
        payload = "\n".join(missing) + "\n"
        _atomic_append_text(gitignore_path, payload)
        appended = list(missing)
        try:
            from ralph.git.operations import stage_files as _stage_files

            result = commit_deterministic_writes(
                repo_root,
                paths=[".gitignore"],
                pre_contents=pre_contents,
                subject="chore(gitignore): seed ralph defaults",
                create_commit_fn=create_commit,
                stage_fn=_stage_files,
            )
            if result.status is ScopedCommitStatus.CREATED and result.sha:
                logger.debug(".gitignore auto-seed committed: {}", result.sha[:8])
            elif result.status is ScopedCommitStatus.SKIPPED and result.skipped_paths:
                logger.warning(
                    ".gitignore auto-seed skipped: .gitignore was already dirty at HEAD "
                    "(user mid-edit?); left for the user flow"
                )
            elif result.status is ScopedCommitStatus.FAILED:
                logger.warning(".gitignore auto-seed commit failed (non-fatal): {}", result.error)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(".gitignore auto-seed commit failed (non-fatal): {}", exc)
    return appended


def _regenerate_existing_local_configs(
    agent_dir: Path, *, global_dir: Path
) -> list[BootstrapResult]:
    """Regenerate project-local configs only when they already exist."""
    sources = {
        _LOCAL_CONFIG_FILENAME: _get_bundled_defaults_dir() / _LOCAL_CONFIG_SOURCE,
        _LOCAL_MCP_FILENAME: _get_bundled_defaults_dir() / "mcp.toml",
        **{
            policy_filename: _resolve_global_policy_source(global_dir, policy_filename, force=False)
            for policy_filename in _LOCAL_POLICY_FILENAMES
        },
        **{
            policy_filename: _get_bundled_defaults_dir() / policy_filename
            for policy_filename in _ADVANCED_LOCAL_POLICY_FILENAMES
        },
    }
    return [
        write_agents_with_backup(source, target, True, trigger="ralph --regenerate-config")
        if filename in _ADVANCED_LOCAL_POLICY_FILENAMES
        else _copy_with_backup(source, target, True)
        for filename, source in sources.items()
        if (target := agent_dir / filename).exists()
    ]


def regenerate_all(
    *,
    global_dir: Path | None = None,
    agent_dir: Path | None = None,
) -> list[BootstrapResult]:
    """Regenerate global configs and refresh existing local overrides.

    Missing global files are created. Project-local files are rewritten only
    when already present; use :func:`ensure_local_configs` to create them.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        agent_dir: The .agent directory whose existing local configs may be refreshed.

    Returns:
        Flat list of BootstrapResult for every file touched.
    """
    resolved_global_dir = global_dir or resolve_global_config_dir()
    results: list[BootstrapResult] = [
        ensure_global_config(resolved_global_dir, force=True),
        ensure_global_agents_config(resolved_global_dir, force=True),
        ensure_global_mcp_config(resolved_global_dir, force=True),
        *ensure_global_policy_configs(resolved_global_dir, force=True),
    ]
    if agent_dir is not None:
        results.extend(
            _regenerate_existing_local_configs(agent_dir, global_dir=resolved_global_dir)
        )
    return results


def _backup_path(target: Path) -> Path:
    return target.with_suffix(target.suffix + ".bak")


def _global_policy_target_name(local_policy_filename: str) -> str:
    return _GLOBAL_POLICY_FILENAME_MAP.get(local_policy_filename, local_policy_filename)


def _resolve_global_policy_source(
    global_dir: Path, local_policy_filename: str, *, force: bool
) -> Path:
    if force:
        return _get_bundled_defaults_dir() / local_policy_filename

    preferred_global_path = global_dir / _global_policy_target_name(local_policy_filename)
    if preferred_global_path.exists():
        return preferred_global_path

    legacy_global_path = global_dir / local_policy_filename
    if legacy_global_path.exists():
        return _get_bundled_defaults_dir() / local_policy_filename

    return _get_bundled_defaults_dir() / local_policy_filename


def _migrate_legacy_global_config(target: Path) -> BootstrapResult | None:
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        data = cast("Mapping[str, object]", load_toml(target))
    except Exception:
        return None
    raw_drains_obj: object = data.get("agent_drains")
    if not isinstance(raw_drains_obj, Mapping):
        return None
    raw_drains = cast("Mapping[str, object]", raw_drains_obj)

    drains: dict[str, object] = {
        key: value for key, value in raw_drains.items() if isinstance(key, str)
    }
    missing: list[tuple[str, str]] = []
    analysis_chain: object = drains.get("analysis")
    commit_chain: object = drains.get("commit")
    if isinstance(analysis_chain, str):
        missing.extend(
            (drain_name, analysis_chain)
            for drain_name in ("planning_analysis", "development_analysis")
            if drain_name not in drains
        )
    if isinstance(commit_chain, str) and "development_commit" not in drains:
        missing.append(("development_commit", commit_chain))

    section_start = text.find("[agent_drains]")
    if not missing or section_start == -1:
        return None

    next_section = text.find("\n[", section_start + len("[agent_drains]"))
    insert_at = len(text) if next_section == -1 else next_section + 1
    insert_lines = "".join(f'{name} = "{chain}"\n' for name, chain in missing)
    if insert_at == len(text) and not text.endswith("\n"):
        insert_lines = "\n" + insert_lines

    backup = _backup_path(target)
    if backup.exists():
        backup.unlink()
    # filesystem-write-ok: migration backup preserves existing user configuration metadata
    shutil.copy2(str(target), str(backup))
    write_text_if_changed(
        DEFAULT_FILE_BACKEND,
        target,
        text[:insert_at] + insert_lines + text[insert_at:],
        encoding="utf-8",
    )
    return BootstrapResult(target, "regenerated", backup)


def _copy_with_backup(source: Path, target: Path, force: bool) -> BootstrapResult:
    """Copy source to target, optionally backing up an existing target first.

    The backup (.bak) is always in the same directory as target, so a
    cross-device move is impossible and shutil.move is safe.

    Args:
        source: Bundled template to copy.
        target: Destination path.
        force: When True, overwrite target (with backup). When False, skip if target exists.

    Returns:
        BootstrapResult describing what happened.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    pre_existed = target.exists()

    if pre_existed and not force:
        return BootstrapResult(target, "skipped", None)

    # The target is classified BEFORE any write so the commit routing
    # decision uses the pre-write state.
    plan = _prepare_config_commit(target)

    backup: Path | None = None
    if pre_existed and force:
        backup = _backup_path(target)
        if backup.exists():
            backup.unlink()
        # The forced-regeneration backup move is part of the Ralph-authored
        # config write routed through commit_deterministic_writes below
        # (wt-12). The audit's helper-following check accepts the write
        # as routed.
        shutil.move(  # filesystem-write-ok: forced-regeneration backup preserves the prior config bytes before reinstall
            str(target), str(backup)
        )

    # Shipped-template install write, committed by the routing below
    # (wt-12). The audit's helper-following check accepts the write as
    # routed.
    shutil.copy2(  # filesystem-write-ok: shipped-template install committed by the scoped config routing below
        str(source), str(target)
    )
    action: Literal["created", "skipped", "regenerated"] = (
        "regenerated" if pre_existed else "created"
    )
    _commit_config_write(target, plan)
    return BootstrapResult(target, action, backup)
