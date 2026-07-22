"""Installation links for bundled coding-agent CLIs."""

from __future__ import annotations

from typing import Final

AGENT_INSTALL_URLS: Final[dict[str, str]] = {
    "claude": "https://docs.claude.com/claude-code",
    "claude-headless": "https://docs.claude.com/claude-code",
    "codex": "https://codex.openai.com",
    "opencode": "https://opencode.ai",
    "nanocoder": "https://docs.nanocollective.org/nanocoder/docs",
    "agy": "https://github.com/google-antigravity/antigravity-cli",
    "pi": "https://pi.dev",
    "cursor": "https://docs.cursor.com/agent",
}


def install_url_for(name: str) -> str | None:
    """Return the installation URL for a built-in agent name, if known."""
    return AGENT_INSTALL_URLS.get(name.lower())
