"""Sentry initialization with privacy-compliant PII scrubbing."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import sentry_sdk

_DSN: str = (
    "https://418c4f0099a0db0987b420c3cd1d5bb0@o4511480216158208.ingest.de.sentry.io/4511480219959376"
)
_HOME_PREFIX: str = str(Path.home())


def _scrub_obj(obj: object) -> None:
    """Recursively replace home-directory paths with '~' in all string values."""
    if isinstance(obj, dict):
        d = cast("dict[str, object]", obj)
        for key in list(d.keys()):
            val = d[key]
            if isinstance(val, str):
                if _HOME_PREFIX in val:
                    d[key] = val.replace(_HOME_PREFIX, "~")
            else:
                _scrub_obj(val)
    elif isinstance(obj, list):
        lst = cast("list[object]", obj)
        for i, item in enumerate(lst):
            if isinstance(item, str):
                if _HOME_PREFIX in item:
                    lst[i] = item.replace(_HOME_PREFIX, "~")
            else:
                _scrub_obj(item)


def _scrub_event(event: object, _hint: object) -> object:
    if isinstance(event, dict):
        d = cast("dict[str, object]", event)
        d.pop("server_name", None)
    _scrub_obj(event)
    return event


def init_sentry(user_id: str, session_id: str) -> None:
    """Initialize Sentry with anonymous user identity and PII scrubbing."""
    sentry_sdk.init(
        dsn=_DSN,
        send_default_pii=False,
        traces_sample_rate=1.0,
        profiles_sample_rate=1.0,
        before_send=_scrub_event,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
        before_send_transaction=_scrub_event,  # type: ignore[arg-type]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    )
    sentry_sdk.set_user({"id": user_id})  # type: ignore[misc]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    sentry_sdk.set_tag("session_id", session_id)
