from __future__ import annotations

_DETERMINISTIC = "deterministic:"
_ADVISORY = "advisory:"
_FILE_HEADER = "+++ b/"


def _cells(row: str) -> list[str]:
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def _enforcement_kinds(cells: list[str]) -> set[str]:
    return {kind for cell in cells for kind in (_DETERMINISTIC, _ADVISORY) if cell.startswith(kind)}


def downgraded_guards(diff: str) -> tuple[str, ...]:
    path = ""
    removed: dict[tuple[str, str], set[str]] = {}
    added: dict[tuple[str, str], set[str]] = {}
    for line in diff.splitlines():
        if line.startswith(_FILE_HEADER):
            path = line[len(_FILE_HEADER) :]
            continue
        if line.startswith(("---", "+++")) or line[1:2] != "|":
            continue
        cells = _cells(line[1:])
        if not cells or not cells[0]:
            continue
        side = removed if line[0] == "-" else added if line[0] == "+" else None
        if side is not None:
            side[(path, cells[0])] = _enforcement_kinds(cells)
    return tuple(
        f"{path}: {row}: deterministic enforcement replaced by advisory"
        for (path, row), before in removed.items()
        if _DETERMINISTIC in before
        and (after := added.get((path, row))) is not None
        and _DETERMINISTIC not in after
        and _ADVISORY in after
    )


__all__ = ["downgraded_guards"]
