"""E-7/DA-007: vertical rhythm over decoration -- prefer a hairline rule to
a full box, so colour density falls as routine output scrolls instead of
accumulating a bordered Panel every time something renders.

The "density falls as output scrolls" half of E-7 is proven mechanically
by the salience allocator's frame-indexed decay
(``tests/unit/display/test_salience.py``'s
``test_frame_indexed_decay_demotes_after_the_steady_state_window`` and
``test_demotion_is_one_way_until_a_real_state_change``). This module proves
the other half -- "prefer one hairline rule to a full box" -- which had no
gate at all before DA-007: a future edit could add an undocumented
``Panel(`` call to the routine per-iteration render path and nothing would
fail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_PARALLEL_DISPLAY_PATH = Path(__file__).parents[3] / "ralph" / "display" / "parallel_display.py"

#: A live ``Panel(`` construction call -- not a docstring/comment mention
#: (e.g. the module's own prose referring to "the inline ``Panel(...)``
#: call"), which is why this matches the exact call-site shape, not just
#: the substring "Panel(".
_PANEL_CALL_RE = re.compile(r"^\s*(?:panel = )?Panel\(\s*$", re.MULTILINE)

#: E-7/DA-007: the full, exact set of documented exceptions to "prefer a
#: hairline rule to a full box". All three render a one-shot or on-demand
#: block -- never part of the routine per-iteration/per-turn scrolling
#: output E-7 actually targets -- and all three already degrade to an
#: unboxed heading + body on a height-constrained console (see each
#: method's own P0/wt-028-display docstring), which is the same "prefer
#: indentation to a border" substitution E-7 asks for, just gated on
#: available rows rather than render frequency:
#:
#: - ``emit_first_run_panel``: the first-run welcome/setup panel, shown at
#:   most once per machine (gated on first-run detection upstream), never
#:   inside the iteration loop.
#: - the "Effective Configuration" panel in ``emit_effective_configuration``
#:   (search text, since the method name itself does not appear on the
#:   ``Panel(`` line): a startup-only dump of the resolved config, printed
#:   once per run, not once per iteration.
#: - ``emit_info_panel``: used by the ``diagnose`` subcommand's "Next
#:   steps" / free-form info block -- an explicit, user-requested one-shot
#:   report, not routine scrolling output from the main pipeline loop.
#:
#: A future addition here must either (a) prove it is similarly one-shot/
#: on-demand and extend this exception count with the same reasoning, or
#: (b) render via ``Console.rule``/indentation instead, matching E-7's own
#: preference. Mirrors the E-3 ``burst``-scene carve-out's pattern in
#: ``tests/test_display_generated_scenes.py``: a named, reasoned exception
#: count that fails loudly on an *undocumented* regression rather than
#: silently accepting an ever-growing, unexamined list.
_DOCUMENTED_PANEL_EXCEPTION_COUNT = 3
@pytest.mark.criteria("E-7")


def test_parallel_display_panel_usage_stays_at_the_documented_exception_count() -> None:
    """E-7/DA-007: no more than the three documented, reasoned exceptions to
    "prefer a hairline rule to a full box" may exist in the routine render
    path. A future ``Panel(`` call added here without updating
    ``_DOCUMENTED_PANEL_EXCEPTION_COUNT`` (and its reasoning above) fails
    this test instead of silently shipping undocumented chrome."""
    source = _PARALLEL_DISPLAY_PATH.read_text(encoding="utf-8")
    call_sites = _PANEL_CALL_RE.findall(source)
    assert len(call_sites) == _DOCUMENTED_PANEL_EXCEPTION_COUNT, (
        f"expected exactly {_DOCUMENTED_PANEL_EXCEPTION_COUNT} documented Panel( call sites "
        f"in {_PARALLEL_DISPLAY_PATH.name}, found {len(call_sites)} -- a new Panel( call must "
        "either be justified and added to the documented exception count in "
        "test_vertical_rhythm.py, or converted to Console.rule/indentation per E-7"
    )
