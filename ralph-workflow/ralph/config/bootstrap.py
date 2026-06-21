"""Bootstrap helpers for creating user-global and project-local config files.

Auto-creates the user-global Ralph config set on first run, including
~/.config/ralph-workflow.toml, ~/.config/ralph-workflow-mcp.toml,
~/.config/ralph-workflow-pipeline.toml, and
~/.config/ralph-workflow-artifacts.toml from bundled templates.
Also supports regenerating configs with .bak backups via --regenerate-config.

Bootstrap creates the standard first-run config set:
  - User-global: ~/.config/ralph-workflow.toml, ~/.config/ralph-workflow-mcp.toml,
                 ~/.config/ralph-workflow-pipeline.toml,
                 ~/.config/ralph-workflow-artifacts.toml
  - Project-local: .agent/ralph-workflow.toml, .agent/mcp.toml,
                   .agent/pipeline.toml, .agent/artifacts.toml
  - Advanced optional: .agent/agents.toml (only regenerated when already present)
  - Batteries-included .gitignore: Ralph-local, Python, Node, Rust, Go, Ruby,
    PHP, Java/Kotlin, .NET, Dart/Flutter, Elixir, Scala, Terraform, IDE,
    and OS metadata patterns (see _DEFAULT_GITIGNORE_PATTERNS)
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from ralph.config.loader import load_toml
from ralph.git.operations import append_to_gitignore

if TYPE_CHECKING:
    from types import ModuleType

_GLOBAL_CONFIG_FILENAME = "ralph-workflow.toml"
_GLOBAL_MCP_FILENAME = "ralph-workflow-mcp.toml"
_GLOBAL_PIPELINE_FILENAME = "ralph-workflow-pipeline.toml"
_GLOBAL_ARTIFACTS_FILENAME = "ralph-workflow-artifacts.toml"
_LOCAL_CONFIG_FILENAME = "ralph-workflow.toml"
_LOCAL_MCP_FILENAME = "mcp.toml"
_LOCAL_POLICY_FILENAMES = ("pipeline.toml", "artifacts.toml")
_GLOBAL_POLICY_FILENAME_MAP = {
    "pipeline.toml": _GLOBAL_PIPELINE_FILENAME,
    "artifacts.toml": _GLOBAL_ARTIFACTS_FILENAME,
}
_ADVANCED_LOCAL_POLICY_FILENAMES = ("agents.toml",)
_LOCAL_CONFIG_SOURCE = "ralph-workflow-local.toml"
_DEFAULT_GITIGNORE_PATTERNS: tuple[str, ...] = (
    # Ralph Workflow local artifacts (existing — DO NOT REORDER)
    ".agent/",
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
# NOTE: ``_agent_internal_paths.py`` is loaded via ``importlib`` rather than
# a normal ``from ... import`` so this module never transitively triggers
# ``ralph.phases.__init__.py``. A normal import would create a cycle
# (``ralph.config`` -> ``bootstrap`` -> ``ralph.phases.__init__`` ->
# ``ralph.policy.loader`` -> ``ralph.phases``), so the leaf module is loaded
# directly by file path. The audit
# (``ralph/testing/audit_agent_internal_paths.py``) uses the same pattern.
def _load_agent_internal_paths_module() -> ModuleType:
    """Load ``_agent_internal_paths`` directly without triggering ``ralph.phases.__init__``.

    Returns:
        The loaded ``_agent_internal_paths`` module.
    """
    module_path = Path(__file__).resolve().parent.parent / "phases" / "_agent_internal_paths.py"
    spec = importlib.util.spec_from_file_location(
        "_ralph_agent_internal_paths_bootstrap_target", module_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot build import spec for {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_agent_internal_paths_module = _load_agent_internal_paths_module()
_AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB: str = (
    _agent_internal_paths_module._AGENT_INTERNAL_COMPLETION_SENTINEL_GLOB
)
AGENT_INTERNAL_DIR_GLOBS: frozenset[str] = _agent_internal_paths_module.AGENT_INTERNAL_DIR_GLOBS
AGENT_INTERNAL_ROOT_BASENAMES: frozenset[str] = (
    _agent_internal_paths_module.AGENT_INTERNAL_ROOT_BASENAMES
)
AGENT_INTERNAL_TOP_LEVEL_BASENAMES: frozenset[str] = (
    _agent_internal_paths_module.AGENT_INTERNAL_TOP_LEVEL_BASENAMES
)


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


@dataclass(frozen=True)
class BootstrapResult:
    """Result of a bootstrap operation.

    Attributes:
        path: Target file path that was acted on.
        action: What happened: created, skipped, or regenerated.
        backup: Path to the .bak file if the original was backed up, else None.
    """

    path: Path
    action: Literal["created", "skipped", "regenerated"]
    backup: Path | None = None


def resolve_global_config_dir(env: Mapping[str, str] | None = None) -> Path:
    """Resolve the user-global config directory.

    Honors XDG_CONFIG_HOME when set; falls back to ~/.config.

    Args:
        env: Environment mapping to read from. Uses os.environ when None.

    Returns:
        Path to the config directory.
    """
    env_map: Mapping[str, str] = os.environ if env is None else env
    xdg = env_map.get("XDG_CONFIG_HOME", "")
    if xdg:
        return Path(xdg)
    return Path.home() / ".config"


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
    append_to_gitignore(repo_root, list(_DEFAULT_GITIGNORE_PATTERNS))


def _ensure_default_git_exclude(repo_root: Path) -> None:
    """Append the canonical engine-internal patterns to ``.git/info/exclude``.

    Uses ``add_to_git_exclude`` from ``ralph.git.commit_cleanup`` which is
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

    from ralph.git.commit_cleanup import add_to_git_exclude

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
        except OSError:
            existing = set()
    missing = [p for p in _DEFAULT_GIT_EXCLUDE_PATTERNS if p not in existing]
    if missing:
        exclude_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(missing)
        if file_existed:
            with exclude_path.open("a", encoding="utf-8") as f:
                f.write("\n")
                f.write(payload)
        else:
            exclude_path.write_text(payload, encoding="utf-8")
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
        except OSError:
            existing = set()
    missing = [p for p in _DEFAULT_GITIGNORE_PATTERNS if p not in existing]
    if missing:
        append_to_gitignore(repo_root, missing)
    return list(missing)


def _regenerate_existing_advanced_local_configs(agent_dir: Path) -> list[BootstrapResult]:
    """Regenerate advanced local configs only when they already exist."""
    results: list[BootstrapResult] = []
    for policy_filename in _ADVANCED_LOCAL_POLICY_FILENAMES:
        target = agent_dir / policy_filename
        if target.exists():
            results.append(
                _copy_with_backup(
                    _get_bundled_defaults_dir() / policy_filename,
                    target,
                    True,
                )
            )
    return results


def regenerate_all(
    *,
    global_dir: Path | None = None,
    agent_dir: Path | None = None,
) -> list[BootstrapResult]:
    """Regenerate all configs from bundled defaults, backing up existing files.

    Args:
        global_dir: Override the global config directory. Defaults to resolve_global_config_dir().
        agent_dir: The .agent directory to regenerate local configs in. Skipped when None.

    Returns:
        Flat list of BootstrapResult for every file touched.
    """
    results: list[BootstrapResult] = [
        ensure_global_config(global_dir, force=True),
        ensure_global_mcp_config(global_dir, force=True),
        *ensure_global_policy_configs(global_dir, force=True),
    ]
    if agent_dir is not None:
        results.extend(ensure_local_configs(agent_dir, force=True))
        results.extend(_regenerate_existing_advanced_local_configs(agent_dir))
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

    data = cast("Mapping[str, object]", load_toml(target))
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
    shutil.copy2(str(target), str(backup))
    target.write_text(text[:insert_at] + insert_lines + text[insert_at:], encoding="utf-8")
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

    backup: Path | None = None
    if pre_existed and force:
        backup = _backup_path(target)
        if backup.exists():
            backup.unlink()
        shutil.move(str(target), str(backup))

    shutil.copy2(str(source), str(target))
    action: Literal["created", "skipped", "regenerated"] = (
        "regenerated" if pre_existed else "created"
    )
    return BootstrapResult(target, action, backup)
