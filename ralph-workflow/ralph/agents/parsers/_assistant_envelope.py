
from __future__ import annotations

from dataclasses import dataclass

ENVELOPE_NORMAL: str = "normal"
ENVELOPE_SYNTHETIC: str = "synthetic"
ENVELOPE_API_ERROR: str = "api_error"


_TOP_LEVEL_API_ERROR_KEYS: frozenset[str] = frozenset({"isApiErrorMessage"})


@dataclass(frozen=True)
class AssistantEnvelopeDecision:

    envelope: str
    error_text: str = ""
    synthetic_text: str = ""


def _extract_message_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _extract_top_level_error_text(obj: dict[str, object]) -> str:
    error = obj.get("error")
    if isinstance(error, str) and error.strip():
        return error.strip()
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
        err_type = error.get("type")
        if isinstance(err_type, str) and err_type.strip():
            return err_type.strip()
    return ""


def _is_top_level_api_error(obj: dict[str, object]) -> bool:
    return any(bool(obj.get(key)) for key in _TOP_LEVEL_API_ERROR_KEYS)


def _is_message_api_error(message: dict[str, object]) -> bool:
    return bool(message.get("isApiErrorMessage"))


def _is_synthetic_message(message: dict[str, object]) -> bool:
    model = message.get("model")
    if isinstance(model, str) and model == "<synthetic>":
        return True
    usage = message.get("usage")
    stop_reason = message.get("stop_reason")
    if not isinstance(usage, dict) or not isinstance(stop_reason, str):
        return False
    output_tokens: object = usage.get("output_tokens")
    return output_tokens == 0 and stop_reason == "stop_sequence"


def classify_assistant_record(obj: object) -> AssistantEnvelopeDecision:
    if not isinstance(obj, dict):
        return AssistantEnvelopeDecision(envelope=ENVELOPE_NORMAL)

    message = obj.get("message")
    message_dict: dict[str, object] = message if isinstance(message, dict) else {}

    if _is_top_level_api_error(obj) or _is_message_api_error(message_dict):
        error_text = _extract_top_level_error_text(obj)
        if not error_text:
            error_text = _extract_message_text(message_dict.get("content"))
        return AssistantEnvelopeDecision(
            envelope=ENVELOPE_API_ERROR,
            error_text=error_text,
        )

    if _is_synthetic_message(message_dict):
        synthetic_text = _extract_message_text(message_dict.get("content"))
        return AssistantEnvelopeDecision(
            envelope=ENVELOPE_SYNTHETIC,
            synthetic_text=synthetic_text,
        )

    return AssistantEnvelopeDecision(envelope=ENVELOPE_NORMAL)


__all__ = [
    "ENVELOPE_API_ERROR",
    "ENVELOPE_NORMAL",
    "ENVELOPE_SYNTHETIC",
    "AssistantEnvelopeDecision",
    "classify_assistant_record",
]
