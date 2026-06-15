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
``./.agents/skills``, etc. as sibling examples. We mirror only the 3
non-canonical USER-GLOBAL entries (claude, codex, agy) as project
siblings. The opencode project sibling is intentionally absent because:
(a) the project canonical ``./.opencode/skills/`` IS the opencode
project root, so it would be a self-symlink (forbidden); (b) the
user-global opencode root at ``~/.config/opencode/skills/`` is already
covered by the canonical user-global install; (c) the project-scope
``./.agents/skills/`` path mentioned in the opencode docs is a
USER-GLOBAL fallback for opencode (already covered by the canonical
claude install at ``~/.claude/skills/`` per the OPENCODE CLAUDE-SKILLS
FALLBACK section above). Therefore adding either ``./.opencode/skills/``
or ``./.agents/skills/`` as a project sibling would either create a
self-symlink or duplicate the user-global coverage. The 3 non-canonical
user-global entries are the right mirror set.
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
    onto the project workspace. The opencode project sibling is intentionally
    absent: it would be a self-symlink to ``./.opencode/skills/`` which is
    the project canonical, and the user-global opencode root is already
    covered by the user-global install. See the MAINTENANCE section above
    for the full scope-down rationale (PA-007).
    """
    _ = workspace_root  # workspace_root is forwarded at resolve() time, not used here
    by_agent: dict[str, AgentSkillRoot] = {entry.agent: entry for entry in AGENT_SKILL_ROOTS}
    agents: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("claude", (".claude", "skills")),
        ("codex", (".codex", "skills")),
        ("agy", (".gemini", "antigravity-cli", "skills")),
    )
    return tuple(
        ProjectAgentSkillRoot(
            agent=agent,
            path_segments=segments,
            source_url=by_agent[agent].source_url,
            is_canonical=False,
            last_verified_iso=by_agent[agent].last_verified_iso,
        )
        for agent, segments in agents
    )


__all__ = [
    "AGENT_SKILL_ROOTS",
    "AgentSkillRoot",
    "ProjectAgentSkillRoot",
    "agent_skill_roots",
    "canonical_agent_skill_root",
    "project_sibling_skill_roots",
    "project_skill_root",
    "sibling_agent_skill_roots",
]
