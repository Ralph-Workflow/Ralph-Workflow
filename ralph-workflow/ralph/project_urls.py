"""Canonical public repository URLs for Ralph Workflow.

These constants are the maintained source of truth for the public repo surfaces
referenced by package metadata, docs config, and regression tests.
"""

from __future__ import annotations

CODEBERG_REPOSITORY_URL = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
GITHUB_MIRROR_URL = "https://github.com/Ralph-Workflow/Ralph-Workflow"
CODEBERG_ISSUES_URL = f"{CODEBERG_REPOSITORY_URL}/issues/new"
CODEBERG_REPOSITORY_GIT_URL = f"{CODEBERG_REPOSITORY_URL}.git"
GITHUB_MIRROR_GIT_URL = f"{GITHUB_MIRROR_URL}.git"

# Ralph-Workflow-Pro is a separate, optional GUI layer that runs the
# engine as a subprocess. It lives in its own Codeberg repository and
# is referenced from the engine docs as the source of truth for the
# Pro↔Ralph integration contract.
RALPH_WORKFLOW_PRO_REPOSITORY_URL = "https://codeberg.org/RalphWorkflow/Ralph-Workflow-Pro"

__all__ = [
    "CODEBERG_ISSUES_URL",
    "CODEBERG_REPOSITORY_GIT_URL",
    "CODEBERG_REPOSITORY_URL",
    "GITHUB_MIRROR_GIT_URL",
    "GITHUB_MIRROR_URL",
    "RALPH_WORKFLOW_PRO_REPOSITORY_URL",
]
