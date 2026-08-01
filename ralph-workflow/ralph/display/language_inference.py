"""Bounded, failure-safe lexer inference for file-content previews.

Path names are preferred over content because a short file fragment is often
ambiguous.  Content is consulted only when no meaningful path hint exists.
Every public call degrades to ``"text"`` rather than exposing Pygments or
parser failures to the display path.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import PurePath
from typing import Final

from pygments.lexers import get_lexer_for_filename, guess_lexer

# Compound endings precede their shorter counterparts after sorting below.
_SUFFIXES: Final[dict[str, str]] = {
    ".d.ts": "typescript",
    ".spec.ts": "typescript",
    ".test.ts": "typescript",
    ".test.tsx": "typescript",
    ".module.css": "css",
    ".yaml.j2": "yaml",
    ".yml.j2": "yaml",
    ".tar.gz": "text",
    ".py": "python",
    ".pyi": "python",
    ".rs": "rust",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cp": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hh": "cpp",
    ".hpp": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".m": "objective-c",
    ".mm": "objective-c",
    ".rb": "ruby",
    ".php": "php",
    ".pl": "perl",
    ".pm": "perl",
    ".lua": "lua",
    ".r": "r",
    ".scala": "scala",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".zig": "zig",
    ".dart": "dart",
    ".vue": "vue",
    ".svelte": "svelte",
    ".astro": "astro",
    ".scss": "scss",
    ".sass": "sass",
    ".less": "less",
    ".css": "css",
    ".sql": "sql",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".proto": "protobuf",
    ".thrift": "thrift",
    ".tf": "hcl",
    ".tfvars": "hcl",
    ".hcl": "hcl",
    ".nix": "nix",
    ".gradle": "groovy",
    ".bzl": "python",
    ".star": "python",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".ini": "ini",
    ".cfg": "ini",
    ".conf": "ini",
    ".properties": "properties",
    ".csv": "text",
    ".tsv": "text",
    ".rst": "rst",
    ".tex": "latex",
    ".j2": "jinja",
    ".jinja": "jinja",
    ".hbs": "handlebars",
    ".mustache": "html",
    ".diff": "diff",
    ".patch": "diff",
    ".ps1": "powershell",
    ".bat": "bat",
    ".cmd": "bat",
    ".fish": "fish",
    ".zsh": "bash",
    ".sh": "bash",
    ".s": "asm",
    ".asm": "asm",
    ".sol": "solidity",
    ".jl": "julia",
    ".python": "python",
    ".julia": "julia",
    ".fs": "fsharp",
    ".fsx": "fsharp",
    ".groovy": "groovy",
    ".vim": "vim",
    ".md": "markdown",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".env": "bash",
}
_NAMES: Final[dict[str, str]] = {
    "dockerfile": "docker",
    "containerfile": "docker",
    "makefile": "make",
    "justfile": "make",
    "rakefile": "ruby",
    "gemfile": "ruby",
    "brewfile": "ruby",
    "vagrantfile": "ruby",
    "cmakelists.txt": "cmake",
    ".gitignore": "text",
    ".gitattributes": "text",
    ".editorconfig": "ini",
    ".bashrc": "bash",
    ".zshrc": "bash",
    "go.mod": "go",
    "go.sum": "text",
    "license": "text",
    "codeowners": "text",
}
_BINARY_SUFFIXES: Final[tuple[str, ...]] = (
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".gz",
    ".wasm",
)
_TEMPLATE_SUFFIXES: Final[tuple[str, ...]] = (
    ".template",
    ".mustache",
    ".jinja",
    ".tmpl",
    ".j2",
    ".hbs",
)


def _suffix_length(item: tuple[str, str]) -> int:
    """Return a suffix length for longest-suffix precedence."""
    return len(item[0])


_SUFFIXES_BY_LENGTH: Final[tuple[tuple[str, str], ...]] = tuple(
    sorted(_SUFFIXES.items(), key=_suffix_length, reverse=True)
)


def _sniff(prefix: str) -> str:
    """Return a conservative lexer hint from a bounded content prefix."""
    stripped = prefix.lstrip()
    first_line = prefix.splitlines()[0] if prefix.startswith("#!") and prefix.splitlines() else ""
    lowered_shebang = first_line.lower()
    shebang_lexer = (
        "python"
        if "python" in lowered_shebang
        else "bash"
        if any(shell in lowered_shebang for shell in ("bash", "sh", "zsh", "fish"))
        else ""
    )
    lowered = stripped.lower()
    markers = (
        (("diff ", "@@ ", "--- "), "diff"),
        (("<?xml",), "xml"),
        (("<!doctype html", "<html"), "html"),
        (("---",), "yaml"),
    )
    marked_lexer = next((lexer for starts, lexer in markers if lowered.startswith(starts)), "")
    if shebang_lexer or marked_lexer or not stripped.startswith(("{", "[")):
        return shebang_lexer or marked_lexer
    try:
        json.loads(stripped)
    except (TypeError, ValueError):
        return ""
    return "json"


def _infer_uncached(filename: str, prefix: str) -> str:
    """Infer one alias, allowing Pygments to make the final best-effort guess."""
    lowered = filename.lower()
    basename = PurePath(lowered).name
    if lowered.endswith(_BINARY_SUFFIXES):
        return "text"
    lexer = _NAMES.get(basename, "")
    if not lexer and basename.startswith(".env"):
        lexer = "bash"
    if not lexer and basename.startswith("requirements") and basename.endswith(".txt"):
        lexer = "text"
    if not lexer:
        lexer = next(
            (alias for suffix, alias in _SUFFIXES_BY_LENGTH if lowered.endswith(suffix)), ""
        )
    if not lexer:
        inner_name = next(
            (lowered[: -len(suffix)] for suffix in _TEMPLATE_SUFFIXES if lowered.endswith(suffix)),
            "",
        )
        lexer = next(
            (alias for suffix, alias in _SUFFIXES_BY_LENGTH if inner_name.endswith(suffix)), ""
        )
    if not lexer:
        lexer = _sniff(prefix)
    if not lexer:
        try:
            filename_lexer = get_lexer_for_filename(filename)
            lexer = filename_lexer.aliases[0] if filename_lexer.aliases else ""
        except Exception:
            pass
    if not lexer and prefix:
        try:
            guessed_lexer = guess_lexer(prefix)
            lexer = guessed_lexer.aliases[0] if guessed_lexer.aliases else ""
        except Exception:
            pass
    return lexer or "text"


# The 4 KiB caller cap and finite LRU bound prevent unbounded retention of
# agent-provided content while retaining repeated preview lookups.
_cached_infer = lru_cache(maxsize=512)(_infer_uncached)


def lexer_for_path(path: str | None, content: str = "") -> str:
    """Return a lexer alias for arbitrary path/content without raising.

    Args:
        path: Optional source filename.
        content: Optional text used only when the path is inconclusive.

    Returns:
        A Pygments lexer alias or ``"text"``.
    """
    try:
        return _cached_infer(path or "", content[:4096])
    except Exception:
        return "text"


__all__ = ["lexer_for_path"]
