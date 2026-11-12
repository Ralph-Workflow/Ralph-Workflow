from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from ralph.git.subprocess_runner import run_git

__all__ = [
    "ConflictStageEntries",
    "conflict_stage_entries",
]


class ConflictStageEntries(dict[str, dict[int, tuple[str, str]]]):
    def __init__(
        self,
        mapping: dict[str, dict[int, tuple[str, str]]] | None = None,
        *,
        failures: dict[str, str] | None = None,
    ) -> None:
        super().__init__(mapping or {})
        self.failures: dict[str, str] = dict(failures or {})


def _parse_stage_records_into(
    stdout: str,
    entries: dict[str, dict[int, tuple[str, str]]],
    failures: dict[str, str],
    *,
    default_path: str | None = None,
    queried_paths: Sequence[str] | None = None,
) -> None:
    for record in stdout.split("\0"):
        if not record:
            continue
        try:
            if "\t" not in record:
                targets: Sequence[str]
                if default_path is not None:
                    targets = (default_path,)
                elif queried_paths is not None:
                    targets = tuple(queried_paths)
                else:
                    targets = ()
                for p in targets:
                    failures[p] = f"malformed index stage record for {p}: {record!r}"
                    entries.pop(p, None)
                continue

            metadata, path = record.split("\t", 1)
            mode, blob, raw_stage = metadata.split()
            stage = int(raw_stage)
            if path in failures:
                continue
            entries.setdefault(path, {})[stage] = (mode, blob)
        except ValueError:
            target_path = path if "\t" in record else default_path
            if target_path is not None:
                failures[target_path] = (
                    f"malformed index stage record for {target_path}: {record!r}"
                )
                entries.pop(target_path, None)
            elif queried_paths is not None:
                for p in queried_paths:
                    failures[p] = f"malformed index stage record for {p}: {record!r}"
                    entries.pop(p, None)


def conflict_stage_entries(repo_root: Path | str, paths: Sequence[str]) -> ConflictStageEntries:
    if not paths:
        return ConflictStageEntries({})

    root = Path(repo_root)
    result = run_git(
        ("ls-files", "-u", "-z", "--", *paths),
        cwd=root,
        label="git-conflict-stage-entries",
    )

    failures: dict[str, str] = {}
    entries: dict[str, dict[int, tuple[str, str]]] = {}

    if result.returncode != 0:
        if "not a git repository" in result.stderr.lower():
            return ConflictStageEntries(
                {},
                failures={p: f"could not read unmerged index stages for {p}" for p in paths},
            )
        if len(paths) > 1:
            for p in paths:
                single_res = run_git(
                    ("ls-files", "-u", "-z", "--", p),
                    cwd=root,
                    label="git-conflict-stage-entries",
                )
                if single_res.returncode != 0:
                    failures[p] = f"could not read unmerged index stages for {p}"
                else:
                    _parse_stage_records_into(
                        single_res.stdout,
                        entries,
                        failures,
                        default_path=p,
                        queried_paths=(p,),
                    )
            return ConflictStageEntries(entries, failures=failures)
        return ConflictStageEntries(
            {},
            failures={paths[0]: f"could not read unmerged index stages for {paths[0]}"},
        )

    _parse_stage_records_into(
        result.stdout,
        entries,
        failures,
        default_path=paths[0] if len(paths) == 1 else None,
        queried_paths=paths,
    )
    return ConflictStageEntries(entries, failures=failures)
