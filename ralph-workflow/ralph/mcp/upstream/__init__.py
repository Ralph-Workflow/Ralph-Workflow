"""Upstream MCP client and validation.

This sub-package handles Ralph's outbound MCP traffic: Ralph acts as an MCP
*client* when talking to user-defined upstream servers in ``mcp.toml``.
Contains the HTTP/stdio client, server registry, validation handshake, and
per-agent transport probe.
"""

from __future__ import annotations
