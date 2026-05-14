"""Shared MCP protocol plumbing.

This sub-package contains transport, session, and capability-mapping code
used by *both* the Ralph MCP server (Ralph → agents) and the upstream
client (Ralph → external MCPs). Keeping these in a neutral location avoids
import cycles between server/ and upstream/.
"""

from __future__ import annotations
