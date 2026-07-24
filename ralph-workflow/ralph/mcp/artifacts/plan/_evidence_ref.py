"""Typed ``EvidenceRef`` sub-model for plan-step evidence entries.

The ``PlanStep.expected_evidence`` field is typed as ``list[EvidenceRef]``
so agents can declare the kind of evidence (file, command output,
or test name) in a structured way that the executor can parse. The
``kind`` discriminator lets a step mix evidence types without forcing
the executor to sniff the entry shape.

The dependency direction is strictly one-way: this module imports from
pydantic and ``ralph.pydantic_compat`` only. The production consumer
(``PlanStep``) imports ``EvidenceRef`` directly from this module, NOT
from ``_section_models``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel

EvidenceKind = Literal["file", "command_output", "test_name"]


class EvidenceRef(RalphBaseModel):
    """A single evidence entry for a plan step.

    The kind discriminator tells the executor how to verify the entry:

    - ``file``: a workspace file the executor can ``cat`` or stat
    - ``command_output``: a shell command the executor should run and
      capture; the entry text is the command itself
    - ``test_name``: a single test id (e.g. ``tests/test_foo.py::test_x``)
      the executor should run
    """

    model_config = ConfigDict(extra="forbid")

    kind: EvidenceKind
    ref: str = Field(..., min_length=1, max_length=1000)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("ref", "note", mode="before")
    @classmethod
    def _strip_string(cls, value: object) -> object:
        """Strip whitespace from string values; leave other types alone."""
        if isinstance(value, str):
            return value.strip()
        return value

# Alias for the canonical list-of-evidence shape used by PlanStep.
ExpectedEvidence = list[EvidenceRef]

__all__ = ["EvidenceKind", "EvidenceRef", "ExpectedEvidence"]
