"""Path-prefix scrub policy for the Sentry event scrubber.

``ScrubPolicy`` is the frozen set of path prefixes the ``before_send``
scrubber redacts: the home directory plus the cwd/argv prefixes collected at
initialization. The scrubber applies the first matching prefix, so the home
prefix is ordered first.

The dataclass lives in its own module so ``_sentry`` keeps a single top-level
class (the repo-structure policy allows one public top-level class per
module). It is re-exported from :mod:`ralph.telemetry._sentry`, so every
existing importer keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScrubPolicy:
    """The path prefixes the event scrubber redacts."""

    home_prefix: str = ""
    extra_prefixes: tuple[str, ...] = ()


__all__ = ["ScrubPolicy"]
