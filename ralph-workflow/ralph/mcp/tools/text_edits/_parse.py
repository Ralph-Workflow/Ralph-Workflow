
from __future__ import annotations

from typing import cast

from ralph.mcp.tools.invalid_params_error import InvalidParamsError
from ralph.mcp.tools.text_edits._text_edit import TextEdit


def parse_text_edits(params: dict[str, object]) -> list[TextEdit]:
    edits_param = params.get("edits")
    if not isinstance(edits_param, list) or len(edits_param) == 0:
        raise InvalidParamsError("Missing 'edits' parameter as non-empty list")
    entries: list[object] = cast(
        "list[object]", edits_param
    )
    parsed: list[TextEdit] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise InvalidParamsError(f"Edit {index}: missing 'oldText' string")
        fields: dict[str, object] = cast(
            "dict[str, object]", entry
        )
        old_text = fields.get("oldText")
        new_text = fields.get("newText", "")
        if not isinstance(old_text, str):
            raise InvalidParamsError(f"Edit {index}: missing 'oldText' string")
        if not old_text:
            raise InvalidParamsError(f"Edit {index}: 'oldText' must be non-empty")
        if not isinstance(new_text, str):
            raise InvalidParamsError(f"Edit {index}: 'newText' must be a string")
        parsed.append(TextEdit(old_text=old_text, new_text=new_text))
    return parsed
