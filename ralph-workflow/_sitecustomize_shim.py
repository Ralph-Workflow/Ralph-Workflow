"""Interpreter-startup shim: give this project's own install priority in ``sys.path``.

This file is force-included into every built wheel and renamed to
``sitecustomize.py`` at the wheel ROOT (see
``[tool.hatch.build.targets.wheel.force-include]`` in ``pyproject.toml``),
which lands it directly in the venv's ``site-packages`` directory rather
than inside the ``ralph`` package. Placed there under that exact name,
CPython's ``site`` module imports it automatically during interpreter
startup (``site.main()`` calls ``execsitecustomize()``, i.e. ``import
sitecustomize``, after processing ``.pth`` files including this project's
own editable-install ``.pth``), and critically, this happens *before*
``python -m ralph`` (or any other ``-m`` invocation) resolves the target
package.

**Why this source file is NOT itself named ``sitecustomize.py``:** several
in-repo callers deliberately prepend this project's own root to a spawned
subprocess's ``PYTHONPATH`` (see ``ralph/mcp/server/lifecycle.py:
_subprocess_env`` and ``tests/test_agy_live_regression.py:_build_live_env``)
so a plain ``sys.executable -m ralph...`` child still resolves the right
``ralph`` package without going through ``uv run``. That means the project
root is ALSO on ``sys.path`` in exactly the scenario this shim needs to
run correctly. If this source file were named ``sitecustomize.py`` and sat
in the project root, ``import sitecustomize`` would find that direct
project-root copy INSTEAD of the properly-installed
``site-packages/sitecustomize.py`` whenever the project root sorts earlier
in ``sys.path`` than the venv's own site-packages directory (which is
precisely the broken ordering this shim exists to correct) — the shim
would then compute its own "this is the site-packages directory" from
``__file__`` and get the project root instead, silently doing nothing
useful. Keeping the source file under a name that can never collide with
the ``import sitecustomize`` interpreter hook avoids that self-shadowing:
whichever copy Python finds under the literal name ``sitecustomize.py`` is
always the one actually installed into site-packages by the packaging
step below, never a stray project-root file of the same name.

**What it fixes:** an ambient ``PYTHONPATH`` inherited from the invoking
shell (e.g. a pipx-installed copy of this same package on a different
interpreter's site-packages) is inserted into ``sys.path`` *ahead of* this
project's own venv site-packages, per CPython's path-initialization order
(script dir/cwd, then ``PYTHONPATH``, then the standard library, then
site-packages and its ``.pth`` additions last). So a bare
``uv run --project <this project> python -m ralph ...`` invocation would
resolve both ``import ralph`` and third-party imports (e.g.
``pydantic``/``pydantic_core``) against that stale directory instead of
this project's own venv, potentially one built for a different
interpreter ABI (observed failure: ``ModuleNotFoundError: No module named
'pydantic_core._pydantic_core'``). This is the same failure shape
``ralph/verify.py`` (subprocess-level ``env={"PYTHONPATH": ""}``) and
``conftest.py`` (in-process ``sys.path`` filtering before pytest collects
any ``ralph.*``-importing test module) already guard against for their
own invocation shapes. Neither of those covers a raw ``python -m ralph``
invocation or the MCP-server subprocess it spawns, because by the time
either of them would run, the wrong package has already been found and
imported by the interpreter's own module-resolution machinery — there is
no repo-code hook later than this one that still runs before that
happens.

This shim REORDERS ``sys.path`` rather than deleting PYTHONPATH-derived
entries outright, so the intentional project-root-prepend pattern named
above keeps working: it moves TWO paths to the FRONT of ``sys.path``,
ahead of anything PYTHONPATH contributed, without removing anything:

1. This venv's own site-packages directory — derived from this file's own
   installed location, which is now reliably that directory (see above) —
   so third-party imports (``pydantic``, etc.) resolve locally.
2. This project's own root, derived from ``sys.prefix`` (the running
   venv's root; this project's own ``.venv`` conventionally sits directly
   under the project root — see ``Makefile``'s own ``VIRTUAL_ENV``
   handling) — so ``import ralph`` resolves locally even when nothing
   already prepended it. This is a heuristic, not a guarantee: if it
   happens to be wrong for some non-standard layout, the reorder is
   simply a no-op for that one candidate (nothing is removed, so nothing
   can break from a wrong guess).

Keep this file dependency-free (stdlib only) since it must execute before
anything in this project, including ``ralph`` itself, is guaranteed to be
importable.
"""

from __future__ import annotations

import sys
from pathlib import Path

_site_packages_dir = Path(__file__).resolve().parent
_project_root_candidate = Path(sys.prefix).parent

_priority = [str(_site_packages_dir), str(_project_root_candidate)]
_present_priority = [p for p in _priority if p in sys.path]
if _present_priority:
    _rest = [p for p in sys.path if p not in _priority]
    sys.path[:] = _present_priority + _rest
