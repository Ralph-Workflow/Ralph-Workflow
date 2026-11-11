"""Result value returned by the bootstrap helpers.

Split from :mod:`ralph.config.bootstrap` so the bootstrap module stays under
the repository's 1000-line file cap enforced by
:mod:`ralph.testing.audit_repo_structure`. The public import path
``ralph.config.bootstrap.BootstrapResult`` is unchanged: the bootstrap module
re-exports this class.
"""

from __future__ import annotations

from ralph.config.agents_bootstrap import BootstrapResult

__all__ = ["BootstrapResult"]
