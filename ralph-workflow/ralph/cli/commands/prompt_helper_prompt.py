"""Prompt helper system prompt builder."""

from __future__ import annotations

import json

_EXISTING_PROMPT_CONTEXT_BLOCK = """\
**CURRENT PROMPT CONTEXT:**

The workspace already has a `PROMPT.md`. Treat the content below as background
context that the user wants to refine. Use it to understand the current product
shape before you ask follow-up questions, but do not assume it is fully correct.

{existing_prompt_context_block}

"""

_DRAFT_CONTEXT_BLOCK = """\
**CURRENT DRAFT SPECIFICATION:**

The following product specification has already been submitted. Continue refining
based on the user's feedback, or update specific sections as requested.

{current_draft_block}

"""

_USER_REQUEST_BLOCK = """\
**USER REQUEST:**

The user wants to build the following. Turn it into a complete product
specification.

{user_idea_block}

"""


def _fenced_block(content: str, *, info: str) -> str:
    """Return a fenced markdown block that remains valid even when content contains backticks."""
    longest_run = 0
    current_run = 0
    for char in content:
        if char == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    fence = "`" * max(3, longest_run + 1)
    return f"{fence}{info}\n{content}\n{fence}"


def build_prompt_helper_prompt(
    *,
    submit_artifact_tool_name: str,
    existing_prompt_context: str | None = None,
    has_draft: bool = False,
    current_draft: dict[str, object] | None = None,
    user_idea: str | None = None,
) -> str:
    """Build the system prompt for the non-interactive prompt-helper agent.

    The returned prompt instructs the agent to turn the supplied idea (and/or an
    existing PROMPT.md or current draft) into a structured product specification
    and submit it immediately, in one shot, without conversing with the user.
    All conversation with the user is owned by the host orchestrator, not the
    agent.

    Parameters
    ----------
    submit_artifact_tool_name : str
        The MCP tool name to use when submitting the product_spec artifact,
        e.g. "mcp__ralph__ralph_submit_artifact".
    existing_prompt_context : str | None
        Existing PROMPT.md content injected by the host when refining an
        existing prompt before the first helper turn.
    has_draft : bool
        When True, include the current draft specification in the prompt so the
        agent can refine from it.
    current_draft : dict[str, object] | None
        The current product_spec artifact content to include when has_draft is True.
    user_idea : str | None
        The free-text idea the host collected from the user, embedded as a
        request block on the first turn when no PROMPT.md exists.
    """
    existing_block = ""
    if existing_prompt_context is not None:
        existing_block = _EXISTING_PROMPT_CONTEXT_BLOCK.format(
            existing_prompt_context_block=_fenced_block(existing_prompt_context, info="md")
        )

    draft_block = ""
    if has_draft and current_draft is not None:
        draft_json = json.dumps(current_draft, indent=2)
        draft_block = _DRAFT_CONTEXT_BLOCK.format(
            current_draft_block=_fenced_block(draft_json, info="json")
        )

    idea_block = ""
    if user_idea is not None:
        idea_block = _USER_REQUEST_BLOCK.format(
            user_idea_block=_fenced_block(user_idea, info="text")
        )

    pm_intro = "You are a product manager writing a structured product specification."

    return f"""{existing_block}{draft_block}{idea_block}{pm_intro}

Based on the information above, produce a single, complete product specification
and submit it **immediately**. You are running non-interactively: do not ask the
user any questions, do not wait for confirmation, and do not present menus or
options. The user cannot reply to you — all conversation with the user is handled
by the host outside of your turn.

Capture, as relevant to the request:

- Who the users are, and what they need
- What goals this should achieve
- Any constraints to be aware of
- How success will be measured
- What behavior or functionality is expected
- If this has a user-facing component, the UX/UI expectations

**Important guidelines:**

1. **Avoid implementation details.** Do not discuss code structure, technical
   architecture, file organization, or low-level execution plans. Focus purely
   on the *what* and *why*, not the *how*.

2. **Structure information clearly.** Reorganize rough input into clean,
   human-readable product language. Use bullets and sections to keep information
   organized.

3. **Capture UX/UI explicitly.** When the request has user-facing components,
   draw out usability, layout, interaction patterns, and visual expectations
   rather than leaving them implied.

4. **Accessibility.** If using color or visual emphasis, ensure information is
   also communicated through labels, icons, headings, or other non-color cues
   so it remains accessible to color-blind users.

5. **Scale to fit.** For a small, focused feature request, keep the artifact
   compact: populate only the required fields (title, scope, goals, users,
   success_criteria) and one or two optional fields where genuinely relevant.
   Do not force a small request into a full PRD. For a large product plan or
   multi-feature initiative, populate all relevant optional fields (constraints,
   product_behavior, ux_ui_requirements, scope_boundaries, open_questions)
   with rich, specific detail. Adapt depth and section density to the actual
   complexity of the request so neither size feels awkward.

6. **Manage long specifications.** When the specification grows large, actively
   chunk related information into clearly bounded sections rather than
   accumulating a flat list. Summarize groups of related points. Regroup
   overlapping ideas into unified sections. Keep the artifact scannable as it
   grows: every section should have a clear scope, every bullet should be
   distinct, and no section should grow so long that it loses readability.

Submit the product specification as an artifact using the following tool:

**Tool:** {submit_artifact_tool_name}

Submit with:
- `artifact_type`: "product_spec"
- `content`: A JSON string containing the product specification

The content should include: title, scope, goals (non-empty list), users
(non-empty list), success_criteria (non-empty list), and optionally:
constraints, product_behavior, ux_ui_requirements, scope_boundaries,
open_questions."""
