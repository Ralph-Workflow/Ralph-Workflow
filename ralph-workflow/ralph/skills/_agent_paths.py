"""Canonical user-global skill-discovery roots for every supported agent.

SCOPE: this registry covers the USER-GLOBAL skill root for each agent
(typically under $HOME). It does NOT cover project-local roots such as
./.opencode/skills/ or ./.claude/skills/. Project-local skill discovery
varies by agent and by cwd; intentionally out of scope for the initial
implementation. Future maintainers who want to add project-local roots
must add an explicit test that documents the new invariant.

OPENCODE CLAUDE-SKILLS FALLBACK
===============================
OpenCode's documented skill-discovery fallback includes
``~/.claude/skills/`` in addition to its canonical
``~/.config/opencode/skills/`` path. The canonical Claude install at
``~/.claude/skills/`` already covers the OpenCode fallback, so no
separate ``AgentSkillRoot`` entry is required. The current entry for
``opencode`` below symlinks into ``~/.config/opencode/skills/`` and the
``~/.claude/skills/`` portion is satisfied by the Claude install.

OUT OF SCOPE
============
- Nanocoder: no skill system; uses commands under
  ``.nanocoder/commands/``. Confirmed by reading Nanocoder docs.
- Gemini CLI bare: no skill system. (The shipped ``agy`` entry covers
  Google's Antigravity CLI which IS a different product.)

PI SCOPE SPLIT (pi.dev)
======================
The ``pi`` built-in agent is split across the two scopes:

  - **User-global** (``AGENT_SKILL_ROOTS``): ``pi`` is deliberately
    absent.  The pi.dev documentation does NOT document a user-global
    skill discovery root analogous to ``~/.claude/skills/``,
    ``~/.codex/skills/``, or ``~/.config/opencode/skills/``; the only
    documented user-global skill loader is the per-invocation
    ``--skill <path>`` flag, which is a transient argv shape rather
    than a persistent discovery root.  Sources verified at audit
    time: ``https://pi.dev/docs/latest/usage`` (per-invocation
    ``--skill <path>`` and ``--no-skills`` flag table) and
    ``https://pi.dev/docs/latest/json``.

  - **Project-local** (``project_sibling_skill_roots``): ``pi`` is
    present at the documented ``.agents/skills`` project folder.
    The pi docs explicitly state (re-fetched 2026-06-21 from
    ``https://pi.dev/docs/latest/usage``): "On interactive startup,
    pi asks before trusting a project folder that contains
    project-local settings, resources, or project
    ``.agents/skills``" -- so ``.agents/skills/`` is a documented
    project-scope skill resource for pi, even though no user-global
    root exists.  The project-scope entry is added at
    ``(``.agents``, ``skills``)`` with the same ``source_url`` +
    ``last_verified_iso`` research contract as the user-global
    entries; the source URL points at the pi.dev usage page that
    documents the ``.agents/skills`` trust flow.

Adding a pi entry to ``AGENT_SKILL_ROOTS`` (user-global) requires a
live-verified upstream source URL documenting a user-global root;
none exists today.  The two skills-paths research tests
(``tests/test_skills_agent_paths_research.py`` and
``tests/test_skills_project_paths_research.py``) pin this scope
split: pi is absent from the user-global registry AND present in
the project-scope registry.  See
``ralph-workflow/.agent/pi-research-notes.md`` and
``ralph-workflow/.agent/tmp/pi_drift_audit.md`` for the
audit-of-record.

MAINTENANCE
===========
Each ``AgentSkillRoot`` entry carries a ``last_verified_iso`` field in
``YYYY-MM-DD`` format. The date MUST be bumped in the same commit that
changes ``source_url`` or ``path_segments`` for the entry, citing the
re-verification source in the commit message. A future maintainer who
notices a date is more than 180 days stale must either re-verify (bump
the date with evidence) or remove the entry from the live registry per
the path-fallback policy.

Project-scope entries (project_sibling_skill_roots) carry the same
source_url and last_verified_iso contract as user-global entries. The
project canonical ``.opencode/skills/`` is intentionally NOT included
in the project siblings; it is the fan-out source managed by
install_project_baseline_skills.

SCOPE-DOWN RATIONALE (PA-007): The prompt mentions ``./.claude/skills/``,
``./.agents/skills``, etc. as sibling examples. We mirror the 3
non-canonical USER-GLOBAL entries (claude, codex, agy) as project
siblings, plus the one project-scope-only entry that the upstream
pi.dev documentation proves is documented: ``./.agents/skills/`` for
pi.  The opencode project sibling is intentionally absent because:
(a) the project canonical ``./.opencode/skills/`` IS the opencode
project root, so it would be a self-symlink (forbidden); (b) the
user-global opencode root at ``~/.config/opencode/skills/`` is already
covered by the canonical user-global install.  The pi project
sibling at ``./.agents/skills/`` is added because the pi docs prove
it is a documented project-scope skill resource; the user-global
fallback for opencode at ``./.agents/skills/`` mentioned in the
opencode docs is still satisfied by the canonical claude install at
``~/.claude/skills/`` per the OPENCODE CLAUDE-SKILLS FALLBACK section
above.  The 4 project siblings (claude, codex, agy, pi) are the
right mirror set.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ralph.skills._project_paths import ProjectAgentSkillRoot

# A skill-discovery root is the directory an agent scans for `skills/<name>/SKILL.md`.
# Sources (research-confirmed at plan time, last-verified dates populated per entry):
#   - Claude Code:    ~/.claude/skills/<name>/SKILL.md
#                     https://docs.claude.com/claude-code
#   - Codex:          $CODEX_HOME/skills/<name>/SKILL.md  (default ~/.codex/skills/)
#                     https://github.com/openai/codex/blob/main/codex-rs/utils/home-dir/src/lib.rs
#   - OpenCode:       ~/.config/opencode/skills/<name>/SKILL.md
#                     https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/skills.mdx
#                     OpenCode ALSO scans ~/.claude/skills/ and ~/.agents/skills/ (covered
#                     by the Claude install) AND a project-local ./.opencode/skills/ root
#                     (OUT OF SCOPE for this registry).
#   - AGY (Antigravity CLI): ~/.gemini/antigravity-cli/skills/<name>/SKILL.md
#                     https://medium.com/google-cloud/configuring-mcp-servers-and-skills-for-antigravity-cli-and-ide-a938c7eebb78


@dataclass(frozen=True)
class AgentSkillRoot:
    agent: str
    # e.g. (".claude", "skills") -- joined onto Path.home() at call time
    path_segments: tuple[str, ...]
    source_url: str  # authoritative doc URL for this mapping
    is_canonical: bool  # True for the single root we materialize; sibling roots get symlinks
    # YYYY-MM-DD of the last upstream re-verification of path_segments.
    # Optional in the dataclass so test fixtures can omit it; shipped
    # entries in AGENT_SKILL_ROOTS populate it. Maintenance contract: bump
    # this in the same commit that changes source_url or path_segments.
    last_verified_iso: str = ""

    def resolve(self) -> Path:
        """Build the absolute path under the current Path.home().

        Called every time, so test code that monkeypatches Path.home re-resolves
        these paths correctly. NEVER cache the resolved Path on the dataclass.
        """
        return Path.home().joinpath(*self.path_segments)


# Re-verified source URLs (2026-06-09, see _AGENT_PATH_LAST_VERIFIED):
#   - claude:   https://code.claude.com/docs/en/skills
#               (formerly https://docs.claude.com/claude-code; updated to the
#                canonical Claude Code skills page)
#   - codex:    https://github.com/openai/codex/blob/main/codex-rs/utils/home-dir/src/lib.rs
#               (re-verified; documents ~/.codex as the default home dir)
#   - opencode: https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/skills.mdx
#               (re-verified; documents ~/.config/opencode/skills/ + Claude-compatible fallback)
#   - agy:      https://medium.com/google-cloud/configuring-mcp-servers-and-skills-for-antigravity-cli-and-ide-a938c7eebb78
#               (re-verified; documents ~/.gemini/antigravity-cli/skills/ as the CLI global path)
#
# Canonical (materialize-here) root must come first so installers can use it as the
# symlink target. Sibling roots after it.
#
# last_verified_iso: set to the date the upstream documentation was last
# confirmed for this entry. Bump on every path or source_url change.
_AGENT_PATH_LAST_VERIFIED = "2026-06-09"

AGENT_SKILL_ROOTS: tuple[AgentSkillRoot, ...] = (
    AgentSkillRoot(
        agent="claude",
        path_segments=(".claude", "skills"),
        source_url="https://code.claude.com/docs/en/skills",
        is_canonical=True,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
    AgentSkillRoot(
        agent="codex",
        path_segments=(".codex", "skills"),
        source_url=("https://github.com/openai/codex/blob/main/codex-rs/utils/home-dir/src/lib.rs"),
        is_canonical=False,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
    AgentSkillRoot(
        agent="opencode",
        path_segments=(".config", "opencode", "skills"),
        source_url=(
            "https://github.com/anomalyco/opencode/blob/dev/packages/web/src/content/docs/skills.mdx"
        ),
        is_canonical=False,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
    AgentSkillRoot(
        agent="agy",
        path_segments=(".gemini", "antigravity-cli", "skills"),
        source_url=(
            "https://medium.com/google-cloud/"
            "configuring-mcp-servers-and-skills-for-antigravity-cli-and-ide-a938c7eebb78"
        ),
        is_canonical=False,
        last_verified_iso=_AGENT_PATH_LAST_VERIFIED,
    ),
)


def agent_skill_roots() -> tuple[AgentSkillRoot, ...]:
    """Return the registered user-global AgentSkillRoot entries."""
    return AGENT_SKILL_ROOTS


def canonical_agent_skill_root() -> AgentSkillRoot:
    for entry in AGENT_SKILL_ROOTS:
        if entry.is_canonical:
            return entry
    msg = "No canonical AgentSkillRoot registered"
    raise RuntimeError(msg)


def sibling_agent_skill_roots() -> tuple[AgentSkillRoot, ...]:
    return tuple(entry for entry in AGENT_SKILL_ROOTS if not entry.is_canonical)


# Project-scope helpers --------------------------------------------------------
# Project scope is computed at call time from an explicit workspace_root. The
# user-global AGENT_SKILL_ROOTS registry is intentionally NOT polluted with
# project entries (test_skills_agent_paths.py forbids project-local segments
# there) and ProjectAgentSkillRoot is a separate dataclass so the two scopes
# never get cross-wired in resolve().

_PROJECT_CANONICAL_DIR_SEGMENTS: tuple[str, ...] = (".opencode", "skills")

# Canonical FIVE project-scope skill-root prefix strings.
#
# This is the single source of truth consumed by:
#   * ralph.skills._auto_commit.commit_skill_updates -- restrictive allowlist
#     for the auto-commit diff so the agent's own commit never inherits
#     skill-tree changes (the "invisible to the agent" property).
#   * ralph.git.commit_cleanup.untrack_engine_internal_files -- early-skip
#     optimization for the symlink-WARNING noise on tracked skill symlinks.
#   * ralph.testing.audit_skill_auto_commit -- AST regression pin.
#
# The five roots are EXACTLY the project-scope discovery roots documented in
# the upstream skills-configuration doc (the FIVE canonical + project-scope
# pair):
#   * .opencode/skills/            -- canonical project-scope root
#   * .agents/skills/              -- pi project-scope root (PA-007)
#   * .claude/skills/              -- Claude Code project-scope root
#   * .codex/skills/               -- Codex project-scope root
#   * .gemini/antigravity-cli/skills/ -- AGY project-scope root
#
# Adding or removing a root MUST update this constant AND
# ralph.testing.audit_skill_auto_commit in the same commit -- the audit
# pins the literal-string equality of this set.
_SKILL_ROOT_PREFIXES: frozenset[str] = frozenset(
    {
        ".opencode/skills/",
        ".agents/skills/",
        ".claude/skills/",
        ".codex/skills/",
        ".gemini/antigravity-cli/skills/",
    }
)


def project_skill_root(workspace_root: Path) -> Path:
    """Return the project-canonical skill directory for the given workspace.

    The canonical is the single source of truth — sibling symlinks fan out
    to entries below it. ALWAYS computed at call time so test code that
    passes a per-test tmp_path re-resolves correctly.
    """
    return workspace_root.joinpath(*_PROJECT_CANONICAL_DIR_SEGMENTS)


def project_sibling_skill_roots(
    workspace_root: Path,
) -> tuple[ProjectAgentSkillRoot, ...]:
    """Return project-scope sibling skill roots (fan-out targets).

    Mirrors the 3 non-canonical USER-GLOBAL entries (claude, codex, agy)
    onto the project workspace, plus the one project-scope-only entry
    that the upstream pi.dev documentation proves is documented:
    ``./.agents/skills/`` for pi.

    The opencode project sibling is intentionally absent: it would be a
    self-symlink to ``./.opencode/skills/`` which is the project
    canonical, and the user-global opencode root is already covered by
    the user-global install.

    The pi project sibling is present even though pi is absent from
    ``AGENT_SKILL_ROOTS`` (no documented user-global root). The pi docs
    explicitly state (re-fetched 2026-06-21 from
    ``https://pi.dev/docs/latest/usage``) that pi loads project-local
    ``.agents/skills`` from the trusted project folder; this is a
    documented project-scope skill resource for pi, not a user-global
    root. See the PI SCOPE SPLIT (pi.dev) section above for the full
    scope-decision rationale and the MAINTENANCE section for the
    full scope-down rationale (PA-007).
    """
    _ = workspace_root  # workspace_root is forwarded at resolve() time, not used here
    by_agent: dict[str, AgentSkillRoot] = {entry.agent: entry for entry in AGENT_SKILL_ROOTS}
    # pi has NO documented user-global root, so it does not appear in
    # ``AGENT_SKILL_ROOTS`` and we cannot use ``by_agent[agent].source_url``
    # to populate its project-scope entry. The pi source URL points at the
    # pi.dev usage page that documents the ``.agents/skills`` project
    # trust flow.
    pi_source_url = "https://pi.dev/docs/latest/usage"
    pi_last_verified_iso = _AGENT_PATH_LAST_VERIFIED
    agents: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
        (
            "claude",
            (".claude", "skills"),
            by_agent["claude"].source_url,
            by_agent["claude"].last_verified_iso,
        ),
        (
            "codex",
            (".codex", "skills"),
            by_agent["codex"].source_url,
            by_agent["codex"].last_verified_iso,
        ),
        (
            "agy",
            (".gemini", "antigravity-cli", "skills"),
            by_agent["agy"].source_url,
            by_agent["agy"].last_verified_iso,
        ),
        ("pi", (".agents", "skills"), pi_source_url, pi_last_verified_iso),
    )
    return tuple(
        ProjectAgentSkillRoot(
            agent=agent,
            path_segments=segments,
            source_url=source_url,
            is_canonical=False,
            last_verified_iso=last_verified_iso,
        )
        for agent, segments, source_url, last_verified_iso in agents
    )


__all__ = [
    "AGENT_SKILL_ROOTS",
    "_SKILL_ROOT_PREFIXES",
    "AgentSkillRoot",
    "ProjectAgentSkillRoot",
    "agent_skill_roots",
    "canonical_agent_skill_root",
    "project_sibling_skill_roots",
    "project_skill_root",
    "sibling_agent_skill_roots",
]
