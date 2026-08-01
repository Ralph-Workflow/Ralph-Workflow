"""Canonical public repository URLs for Ralph Workflow.

These constants are the maintained source of truth for the public repo surfaces
referenced by package metadata, docs config, and regression tests.
"""

from __future__ import annotations

GITHUB_REPOSITORY_URL = "https://github.com/Ralph-Workflow/Ralph-Workflow"
GITHUB_ISSUES_URL = f"{GITHUB_REPOSITORY_URL}/issues/new"
GITHUB_REPOSITORY_GIT_URL = f"{GITHUB_REPOSITORY_URL}.git"
CODEBERG_MIRROR_URL = "https://codeberg.org/RalphWorkflow/Ralph-Workflow"
CODEBERG_MIRROR_GIT_URL = f"{CODEBERG_MIRROR_URL}.git"
GETTING_STARTED_URL = (
    f"{GITHUB_REPOSITORY_URL}/blob/main/ralph-workflow/docs/sphinx/getting-started.md"
)

# Ralph-Workflow-Pro is a separate, optional GUI layer that runs the
# engine as a subprocess. It lives in its own Codeberg repository and
# is referenced from the engine docs as the source of truth for the
# Pro↔Ralph integration contract.
RALPH_WORKFLOW_PRO_REPOSITORY_URL = "https://codeberg.org/RalphWorkflow/Ralph-Workflow-Pro"

__all__ = [
    "CODEBERG_MIRROR_GIT_URL",
    "CODEBERG_MIRROR_URL",
    "GETTING_STARTED_URL",
    "GITHUB_ISSUES_URL",
    "GITHUB_REPOSITORY_GIT_URL",
    "GITHUB_REPOSITORY_URL",
    "RALPH_WORKFLOW_PRO_REPOSITORY_URL",
]
