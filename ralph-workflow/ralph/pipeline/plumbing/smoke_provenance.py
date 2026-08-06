"""``Provenance``: the ordered trust rung for one Evidence Provenance fact.

Split out of :mod:`ralph.pipeline.plumbing.smoke_evidence` so that module
declares a single top-level class, per this repo's structure policy (see
``ralph/testing/audit_repo_structure.py``). Re-exported from
``smoke_evidence`` so existing call sites keep importing both names from
one module.
"""

from __future__ import annotations

from enum import IntEnum

__all__ = ["Provenance"]


class Provenance(IntEnum):
    """Ordered trust level for one fact. The ordering IS the policy.

    Each rung answers "how did the harness learn this fact?", ranked from
    least to most trustworthy:

    - ``ABSENT``: the fact does not hold; there is no evidence for it at all.
    - ``HOST_SYNTHESIZED``: the harness wrote the evidence itself (e.g. the
      AGY completion-sentinel synthesis branch). Never proof the agent did
      anything.
    - ``WORKSPACE_EFFECT``: a file appeared on disk (e.g. a fallback artifact
      promoted through the canonical submit path). Real, but not attributable
      to a specific tool call.
    - ``TRANSCRIPT``: the agent's own output stream claimed the fact (e.g. a
      parser-classified ``tool_use`` event). Could in principle be spoofed by
      model-authored text; not independently witnessed.
    - ``WIRE``: observed at Ralph's MCP server, HMAC-bound in the wire ledger.
      The only rung that proves the agent actually dialled Ralph's tools.
    """

    ABSENT = 0
    HOST_SYNTHESIZED = 1
    WORKSPACE_EFFECT = 2
    TRANSCRIPT = 3
    WIRE = 4
