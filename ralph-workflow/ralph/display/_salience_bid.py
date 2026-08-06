"""One role's per-frame bid for the salience allocator (PLAN.md G-1).

Split from :mod:`ralph.display._salience` so that module keeps a single
top-level class (``SalienceAllocator``) per the repo's one-class-per-file
structure policy (``ralph.testing.audit_repo_structure``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleBid:
    """One role's request to paint in the current frame (G-1).

    ``state_changed`` records whether *this role's own underlying state*
    transitioned this frame (a status flipping from running to success, a
    new error, and so on) -- not whether anything else on screen changed.
    """

    role: str
    state_changed: bool = False


__all__ = ["RoleBid"]
