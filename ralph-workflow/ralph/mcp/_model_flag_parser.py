"""Transport-neutral parser for agent model-flag strings.

Splits a free-form model flag string (the kind that gets forwarded to
the underlying CLI invocation) into the ``(provider, model_id)`` pair
that the multimodal runtime needs to seed
:class:`ralph.mcp.multimodal._multimodal_model_identity.MultimodalModelIdentity`.

The parser is transport-neutral: every transport that forwards a
``--model`` style flag benefits from the same tokenization rules.
The single supported grammar is::

    --model <value>
    -m <value>
    --model=<value>
    -m=<value>
    --provider <value> --model <value>
    --provider <value> --model=<value>
    --provider=<value> --model <value>
    --provider=<value> --model=<value>
    <bare-name>           # bare provider/model or bare model name

The qualified ``<provider>/<model>`` form (``anthropic/claude-sonnet-4-6``)
carries the provider explicitly in the value; the parser splits it on
the first ``/``. A bare model name without ``/`` is returned as
``model_id`` with ``provider=None`` — the caller must resolve the
provider against a transport-specific lookup (catalog, alias table,
or default mapping).

Empty input, whitespace-only input, or input that contains neither a
``--model`` / ``-m`` flag nor a non-flag token returns
``(None, None)``. The parser never raises on a malformed flag — every
malformation degrades to a graceful ``(None, None)`` return so the
caller can fall back to its default identity resolution path.
"""

from __future__ import annotations

import re
import shlex

# Flags accepted as introducers for ``--model`` / ``-m`` and
# ``--provider``. The ``=`` separator is also accepted on the same flag
# (e.g. ``--model=qwen2.5-coder``).
_MODEL_FLAGS: frozenset[str] = frozenset({"--model", "-m"})
_PROVIDER_FLAGS: frozenset[str] = frozenset({"--provider"})

# Recognised delimiters for the qualified ``provider/model`` form. We
# split on the first ``/`` only — ``/`` does not appear inside real
# model identifiers — so ``anthropic/claude-sonnet-4-6`` resolves to
# provider ``anthropic`` and model ``claude-sonnet-4-6``.
_QUALIFIED_SEPARATOR: str = "/"

# Characters preserved verbatim by the parser when normalising the
# provider slug. Anything outside this allowlist (uppercase letters,
# spaces, punctuation, symbols) is replaced with ``-`` so providers
# like ``Anthropic`` and ``bedrock us`` normalise to canonical,
# lowercase, alphanum-+-underscore slugs.
_PROVIDER_NORMALISE_PATTERN: re.Pattern[str] = re.compile(r"[^a-z0-9_-]")


def _normalise_provider_slug(raw: str) -> str:
    """Lowercase and strip disallowed characters from a provider slug.

    The replacement character is ``-`` so consecutive disallowed chars
    collapse to a single dash rather than producing empty slots. This
    matches the catalog's slug convention (``anthropic``, ``openai``,
    ``amazon-bedrock``).
    """
    lowered = raw.lower()
    return _PROVIDER_NORMALISE_PATTERN.sub("-", lowered)


def parse_model_flag(flag: str) -> tuple[str | None, str | None]:
    """Parse a forwarded model flag into ``(provider, model_id)``.

    Args:
        flag: Forwarded CLI string, typically a ``--model`` style flag
            or a bare model name. Examples:

            * ``"--model anthropic/claude-sonnet-4-6"``
            * ``"--model qwen2.5-coder --provider ollama"``
            * ``"--provider=ollama --model=qwen2.5-coder"``
            * ``"anthropic/claude-sonnet-4-6"``
            * ``"claude-opus-4-7"`` (bare name; provider=None)

    Returns:
        A ``(provider, model_id)`` tuple. ``provider`` is the
        normalized slug (lowercase, alphanum + ``-`` + ``_`` only) or
        ``None`` when the flag carries no provider hint. ``model_id``
        is the resolved model identifier string, or ``None`` when no
        model token could be extracted.

    The parser never raises on malformed input; every malformed flag
    degrades to a ``(None, None)`` return.
    """
    if not flag or not flag.strip():
        return (None, None)

    try:
        parts = shlex.split(flag)
    except ValueError:
        # Unbalanced quotes or other shlex malformations: degrade
        # gracefully so callers fall back to default identity.
        return (None, None)

    provider_value: str | None = None
    model_value: str | None = None

    index = 0
    while index < len(parts):
        token = parts[index]

        # ``--model=VALUE`` / ``-m=VALUE`` form
        if "=" in token:
            flag_name, _, flag_value = token.partition("=")
            if flag_name in _MODEL_FLAGS and flag_value:
                model_value = flag_value
            elif flag_name in _PROVIDER_FLAGS and flag_value:
                provider_value = flag_value
            index += 1
            continue

        # ``--model VALUE`` / ``-m VALUE`` form
        if token in _MODEL_FLAGS and index + 1 < len(parts):
            model_value = parts[index + 1]
            index += 2
            continue

        # ``--provider VALUE`` form
        if token in _PROVIDER_FLAGS and index + 1 < len(parts):
            provider_value = parts[index + 1]
            index += 2
            continue

        # First non-flag token: treat as a bare model name.
        if model_value is None and not token.startswith("-"):
            model_value = token
        index += 1

    if model_value is None:
        return (None, None)

    if _QUALIFIED_SEPARATOR in model_value:
        provider_raw, _, model_id = model_value.partition(_QUALIFIED_SEPARATOR)
        # The qualified form carries the provider explicitly. Prefer
        # it over any ``--provider`` flag the caller may have set so
        # the values agree — a ``--model anthropic/claude-...`` with
        # ``--provider openai`` is almost certainly a copy-paste error
        # in the agent invocation, but the qualified form is the
        # stronger signal.
        provider_value = provider_raw
        if not model_id:
            # Defensive: a trailing ``/`` with no model name yields an
            # empty model_id; degrade gracefully rather than return
            # a half-resolved identity.
            return (None, None)
        model_value = model_id

    if provider_value is None:
        return (None, model_value)

    return (_normalise_provider_slug(provider_value), model_value)


__all__ = ["parse_model_flag"]
