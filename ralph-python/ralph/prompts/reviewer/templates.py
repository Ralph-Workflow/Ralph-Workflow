"""Default templates for reviewer prompts."""

DEFAULT_REVIEW_TEMPLATE = """REVIEW MODE
Your only job is to analyze the implementation, not to edit code or commit changes.

Read the changed files and compare the implementation against the plan and requirements.

PLAN:
{{ PLAN }}

CHANGES:
{{ CHANGES }}

Deliver any findings using the configured review workflow.
"""
