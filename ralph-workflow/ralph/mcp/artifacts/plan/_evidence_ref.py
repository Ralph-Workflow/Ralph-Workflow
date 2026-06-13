"""Typed ``EvidenceRef`` sub-model for plan-step evidence entries.

The ``PlanStep.expected_evidence`` field is typed as ``list[EvidenceRef]``
so cheap models can declare the kind of evidence (file, command output,
or test name) in a structured way that the executor can parse. The
``kind`` discriminator lets a step mix evidence types without forcing
the executor to sniff the entry shape.

The class also exposes a string-coercion before-validator so legacy
fixtures that pass a bare string (``"src/foo.py"``) still validate
cleanly — the string is converted to ``EvidenceRef(kind='file',
ref=string)``. The dependency direction is strictly one-way: this
module imports from pydantic and ``ralph.pydantic_compat`` only. The
production consumer (``PlanStep``) imports ``EvidenceRef`` directly
from this module, NOT from ``_section_models``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

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

    @model_validator(mode="before")
    @classmethod
    def _coerce_bare_string(cls, value: object) -> object:
        """Accept a bare string and convert it to ``EvidenceRef(kind='file', ref=s)``.

        This is the legacy string-coercion path so that a plan fixture
        that passes ``"src/foo.py"`` as an evidence entry (instead of a
        dict ``{"kind": "file", "ref": "src/foo.py"}``) still validates
        cleanly. The ``kind`` defaults to ``file`` because the original
        ``expected_evidence`` field was a list of plain file-path strings.
        """
        if isinstance(value, str):
            return {"kind": "file", "ref": value}
        return value

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Allow a single positional string to coerce to ``kind='file'``.

        Pydantic v2 normally rejects positional args; we accept one so
        the ``EvidenceRef('src/foo.py')`` shorthand works.
        """
        if len(args) == 1 and isinstance(args[0], str) and not kwargs:
            super().__init__(kind="file", ref=args[0])
            return
        super().__init__(*args, **kwargs)


# Alias for the canonical list-of-evidence shape used by PlanStep.
ExpectedEvidence = list[EvidenceRef]

__all__ = ["EvidenceKind", "EvidenceRef", "ExpectedEvidence"]
