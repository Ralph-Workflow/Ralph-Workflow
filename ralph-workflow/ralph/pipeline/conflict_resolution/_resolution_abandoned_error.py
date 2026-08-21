"""The error a hard stop raises when it gives up on an attempt."""

from __future__ import annotations

__all__ = ["ResolutionAbandonedError"]


class ResolutionAbandonedError(Exception):
    """An attempt outlived its share and was given up on.

    Distinct from a failed round: a failure says this agent could not
    resolve the conflict, and the next candidate may. This says the
    layer BELOW the driver did not return, which the next candidate
    would only prove again -- at the cost of another share of the
    deadline and another thread the interpreter cannot reclaim.
    """
