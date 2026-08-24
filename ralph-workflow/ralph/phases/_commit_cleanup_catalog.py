"""Shared commit-cleanup deletability catalog for the engine and prompt."""

from __future__ import annotations

UNSAFE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".js",
        ".ts",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".md",
        ".rst",
        ".txt",
        ".toml",
        ".yaml",
        ".yml",
        ".json",
        ".ini",
        ".cfg",
        ".swift",
        ".kt",
        ".kts",
        ".scala",
        ".php",
        ".sh",
        ".bash",
        ".zsh",
        ".fish",
        ".ps1",
        ".pl",
        ".pm",
        ".lua",
        ".r",
        ".m",
        ".mm",
        ".cs",
        ".fs",
        ".fsx",
        ".vb",
        ".dart",
        ".groovy",
        ".clj",
        ".cljs",
        ".hs",
        ".lhs",
        ".elm",
        ".erl",
        ".ex",
        ".exs",
        ".ml",
        ".mli",
        ".nim",
        ".cr",
        ".pas",
        ".pp",
        ".sql",
        ".graphql",
        ".gql",
        ".prisma",
        ".proto",
        ".asm",
        ".s",
        ".inc",
        ".def",
        ".cmake",
        ".mak",
        ".ninja",
        ".dockerfile",
        ".jenkinsfile",
        ".xml",
        ".csv",
        ".tsv",
    }
)

UNSAFE_PATH_SEGMENTS: tuple[str, ...] = ("tests/", "test_", "_test.", "docs/", "doc/")

HOUSEKEEPING_BASENAMES: frozenset[str] = frozenset({".coverage", "coverage.xml"})

PROTECTED_BASENAMES: frozenset[str] = frozenset(
    {
        "dockerfile",
        "makefile",
        "license",
        "readme",
    }
)

LOCKFILE_BASENAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "Cargo.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "composer.lock",
        "Gemfile.lock",
        "go.sum",
    }
)

GENERATED_TEXT_MARKERS: frozenset[str] = frozenset(
    {
        "agent",
        "ai",
        "analysis",
        "artifact",
        "brainstorm",
        "capture",
        "chat",
        "checkpoint",
        "completion",
        "conversation",
        "debug",
        "dump",
        "generated",
        "generation",
        "inference",
        "interaction",
        "llm",
        "log",
        "message",
        "model",
        "note",
        "output",
        "pipeline",
        "plan",
        "prompt",
        "report",
        "response",
        "review",
        "session",
        "summary",
        "temp",
        "tmp",
        "trace",
        "transcript",
        "verify",
        "worker",
    }
)

SOURCE_FILE_GENERATED_MARKERS: frozenset[str] = frozenset(
    {
        "temp",
        "tmp",
        "scratch",
        "generated",
        "throwaway",
        "dump",
    }
)

GENERATED_TEXT_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".agent",
        ".cache",
        ".gradle",
        ".mypy_cache",
        ".next",
        ".nuxt",
        ".output",
        ".pytest_cache",
        ".ruff_cache",
        "artifacts",
        "build",
        "cache",
        "caches",
        "coverage",
        "dist",
        "htmlcov",
        "logs",
        "node_modules",
        "out",
        "output",
        "outputs",
        "reports",
        "sessions",
        "temp",
        "tmp",
        "traces",
        "transcripts",
        "vendor",
    }
)

GENERATED_TEXT_EXTENSIONS: frozenset[str] = frozenset({".txt", ".json", ".md"})

TEMPORARY_SUFFIXES: frozenset[str] = frozenset(
    {".bak", ".tmp", ".temp", ".old", ".orig", ".rej", ".patch", ".log"}
)

COMMIT_CLEANUP_IDENTITY_COUNTER = "commit_cleanup_identity"
DEFAULT_IDENTITY_MAX = 3


def render_delete_decision_rules_markdown() -> str:
    """Render the prompt decision-rule table from the engine catalog."""
    generated_markers = ", ".join(sorted(GENERATED_TEXT_MARKERS))
    source_markers = ", ".join(f"`{marker}`" for marker in sorted(SOURCE_FILE_GENERATED_MARKERS))
    source_dirs = ", ".join(
        f"`{directory}/`"
        for directory in ("tmp", "temp", "generated", "artifacts")
    )
    lockfiles = ", ".join(f"`{name}`" for name in sorted(LOCKFILE_BASENAMES))
    return (
        "| Path class | Action | Boundary |\n"
        "|---|---|---|\n"
        "| Ralph Workflow runtime artifact listed below | `delete_file` | "
        "exact basename, glob, directory, and extension rules only |\n"
        "| binary, build output, cache, editor/OS file, log, coverage, backup, "
        "or temporary artifact | `delete_file`; optionally `add_to_gitignore` | "
        "delete only an actual generated file in the diff; add a pattern only when "
        "project-wide or recurrent |\n"
        "| machine-local file or pattern | `add_to_git_exclude` | "
        "use for the recognized secret family below and editor state |\n"
        "| generated untracked text/JSON/Markdown named for "
        f"{generated_markers} | `delete_file` | untracked only |\n"
        "| generated untracked source whose name contains "
        f"{source_markers}, or lies under {source_dirs} | `delete_file` | "
        "untracked only |\n"
        "| uncertain, user-authored, intentional config, test, documentation, "
        "lockfile, or tracked source | no action | never change commit meaning |\n"
        "\n"
        "Never remove dependency lockfiles such as "
        f"{lockfiles}."
    )
