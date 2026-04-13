"""Ralph: Multi-agent AI orchestration CLI.

Ralph orchestrates AI coding agents to implement changes based on PROMPT.md.
It runs a developer agent for code implementation, then a reviewer agent for
review and fixes (default), automatically staging and committing the final result.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
