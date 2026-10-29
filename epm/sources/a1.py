"""A1-style cell and range references, e.g. ``B3``, ``A1:J40``, ``A:J``, ``3:40``."""

from __future__ import annotations

import re
from dataclasses import dataclass

_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?(\d+)$")
_COL = re.compile(r"^\$?([A-Za-z]{1,3})$")
_ROW = re.compile(r"^\$?(\d+)$")


class A1Error(ValueError):
    pass


def col_to_index(letters: str) -> int:
    """``A`` → 1, ``Z`` → 26, ``AA`` → 27."""
    if not _COL.match(letters):
        raise A1Error(f"'{letters}' is not a column letter")
    n = 0
    for ch in letters.lstrip("$").upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def index_to_col(index: int) -> str:
    out = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def cell_name(row: int, col: int) -> str:
    return f"{index_to_col(col)}{row}"


def parse_cell(ref: str) -> tuple[int, int]:
    """``B3`` → ``(3, 2)`` as (row, column), both 1-based."""
    m = _CELL.match(ref.strip())
    if not m or int(m.group(2)) < 1:
        raise A1Error(f"'{ref}' is not a cell reference like B3")
    return int(m.group(2)), col_to_index(m.group(1))


@dataclass(frozen=True)
class CellRange:
    """A rectangle of cells. ``None`` bounds extend to the edge of the sheet."""

    min_row: int = 1
    min_col: int = 1
    max_row: int | None = None
    max_col: int | None = None

    def clamp(self, n_rows: int, n_cols: int) -> CellRange:
        return CellRange(
            self.min_row,
            self.min_col,
            n_rows if self.max_row is None else min(self.max_row, n_rows),
            n_cols if self.max_col is None else min(self.max_col, n_cols),
        )


def parse_range(text: str) -> CellRange:
    parts = text.strip().split(":")
    if len(parts) == 1:
        row, col = parse_cell(parts[0])
        return CellRange(row, col, row, col)
    if len(parts) != 2:
        raise A1Error(f"'{text}' is not a range like A1:J40")
    a, b = (p.strip() for p in parts)
    if _CELL.match(a) and _CELL.match(b):
        (r1, c1), (r2, c2) = parse_cell(a), parse_cell(b)
    elif _COL.match(a) and _COL.match(b):
        r1, r2, c1, c2 = 1, None, col_to_index(a), col_to_index(b)
    elif _ROW.match(a) and _ROW.match(b):
        r1, r2, c1, c2 = int(a.lstrip("$")), int(b.lstrip("$")), 1, None
    else:
        raise A1Error(f"'{text}' is not a range like A1:J40, A:J or 3:40")
    if (r2 is not None and r2 < r1) or (c2 is not None and c2 < c1) or r1 < 1:
        raise A1Error(f"range '{text}' is empty or reversed")
    return CellRange(r1, c1, r2, c2)
