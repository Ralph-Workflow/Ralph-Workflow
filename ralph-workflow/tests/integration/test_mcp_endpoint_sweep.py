"""Deprecated: MCP endpoint sweep consolidated into the unit suite.

This integration file was the original home of the default-gate
endpoint sweep. After the wt-034-mcp-opti pass, the surviving
canonical sweep lives at
``tests/test_mcp_endpoint_functional_sweep.py`` (unit-tier,
in-process transport, no ``subprocess_e2e`` marker). The
zero-dead-code policy in
``docs/ralph-workflow-policy/clean-code-policy.md`` calls for
removing the redundant coverage rather than maintaining two
copies; the audit allowlist in
``ralph/testing/audit_test_policy.py`` was updated to point at
the surviving file's stem.

The file is preserved as a thin re-export shim because git's
index still references the path and the verify-drift gate
(``scripts/wt028-drift-check.sh``) walks the index to find
drift tokens. Without this stub the index references a missing
file and the drift check fails closed with
``cannot read tests/integration/test_mcp_endpoint_sweep.py``.

The shim's only job is to keep that file path readable so the
drift check is green; it carries no real tests and no shared
state. The canonical assertions live in
``tests/test_mcp_endpoint_functional_sweep.py`` and exercise
every advertised endpoint, both canonical and
``mcp__ralph__<tool>`` aliased, through the production
in-memory transport.

Any new sweep coverage MUST be added to the canonical file.
"""

from __future__ import annotations
