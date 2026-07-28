"""Bounded, failure-safe lexer inference for file-content previews."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import PurePath
from typing import Final

from pygments.lexers import get_lexer_for_filename, guess_lexer

_SUFFIXES: Final[dict[str, str]] = {
    ".d.ts": "typescript", ".test.tsx": "typescript", ".spec.ts": "typescript", ".tsx": "typescript", ".ts": "typescript", ".yaml.j2": "yaml", ".conf.tmpl": "text", ".module.css": "css",
    ".py": "python", ".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin", ".swift": "swift", ".m": "objective-c", ".mm": "objective-c", ".c": "c", ".h": "c", ".hh": "cpp", ".hpp": "cpp", ".cc": "cpp", ".cpp": "cpp", ".cs": "csharp",
    ".rb": "ruby", ".php": "php", ".pl": "perl", ".lua": "lua", ".r": "r", ".scala": "scala", ".clj": "clojure", ".ex": "elixir", ".erl": "erlang", ".hs": "haskell", ".ml": "ocaml", ".zig": "zig", ".dart": "dart", ".vue": "vue", ".svelte": "svelte", ".astro": "astro",
    ".scss": "scss", ".sass": "sass", ".less": "less", ".sql": "sql", ".graphql": "graphql", ".proto": "protobuf", ".thrift": "thrift", ".tf": "hcl", ".hcl": "hcl", ".nix": "nix", ".gradle": "groovy", ".bzl": "python", ".star": "python",
    ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".ini": "ini", ".cfg": "ini", ".env": "bash", ".properties": "properties", ".csv": "text", ".tsv": "text", ".rst": "rst", ".tex": "latex", ".j2": "jinja", ".hbs": "handlebars", ".mustache": "html", ".diff": "diff", ".patch": "diff",
    ".ps1": "powershell", ".bat": "bat", ".fish": "fish", ".zsh": "bash", ".s": "asm", ".asm": "asm", ".sol": "solidity", ".jl": "julia", ".fs": "fsharp", ".groovy": "groovy", ".vim": "vim", ".md": "markdown", ".html": "html", ".xml": "xml", ".css": "css", ".js": "javascript", ".sh": "bash",
}
_NAMES: Final[dict[str, str]] = {
    "dockerfile": "docker", "containerfile": "docker", "makefile": "make", "justfile": "make", "rakefile": "ruby", "gemfile": "ruby", "brewfile": "ruby", "vagrantfile": "ruby", "cmakelists.txt": "cmake", ".gitignore": "text", ".gitattributes": "text", ".editorconfig": "ini", ".bashrc": "bash", ".zshrc": "bash", "go.mod": "go", "go.sum": "text", "license": "text", "codeowners": "text",
}
_BINARY_SUFFIXES: Final[tuple[str, ...]] = (".tar.gz", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".pdf")


def _sniff(prefix: str) -> str:
    """Return a conservative lexer hint from a bounded content prefix."""
    stripped = prefix.lstrip()
    first = prefix.splitlines()[0] if prefix.startswith("#!") and prefix.splitlines() else ""
    if "python" in first:
        return "python"
    if first:
        return "bash"
    markers = (
        (("diff ", "@@ ", "--- "), "diff"),
        (("---",), "yaml"),
        (("<?xml",), "xml"),
        (("<!doctype html", "<html"), "html"),
    )
    lowered = stripped.lower()
    inferred = next((lexer for starts, lexer in markers if lowered.startswith(starts)), "")
    if inferred or not stripped.startswith(("{", "[")):
        return inferred
    try:
        json.loads(stripped)
    except ValueError:
        return ""
    return "json"


def _infer_uncached(filename: str, prefix: str) -> str:
    lowered = filename.lower()
    if lowered.endswith(_BINARY_SUFFIXES):
        return "text"
    lexer = _NAMES.get(PurePath(lowered).name, "")
    if not lexer and (PurePath(lowered).name.startswith(".env") or PurePath(lowered).name.startswith("requirements")):
        lexer = "bash" if PurePath(lowered).name.startswith(".env") else "text"
    if not lexer:
        lexer = next((value for suffix, value in _SUFFIXES.items() if lowered.endswith(suffix)), "")
    if not lexer:
        lexer = _sniff(prefix)
    if not lexer:
        try:
            filename_lexer = get_lexer_for_filename(filename)
            lexer = filename_lexer.aliases[0] if filename_lexer.aliases else ""
        except Exception:
            pass
    if not lexer:
        try:
            guessed_lexer = guess_lexer(prefix)
            lexer = guessed_lexer.aliases[0] if guessed_lexer.aliases else ""
        except Exception:
            pass
    return lexer or "text"


_cached_infer = lru_cache(maxsize=512)(_infer_uncached)


def lexer_for_path(path: str | None, content: str = "") -> str:
    """Return a lexer alias for arbitrary path/content without raising."""
    return _cached_infer(path or "", content[:4096])


__all__ = ["lexer_for_path"]
