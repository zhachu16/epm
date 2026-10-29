"""Map a sheet region onto labelled series according to a source's orientation."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .a1 import CellRange, col_to_index, parse_cell, parse_range
from .reader import Sheet, SourceError, label_of

if TYPE_CHECKING:
    from ..model.schema import Source


@dataclass(frozen=True)
class Entry:
    """One cell of a series: its axis label, parsed value and reference for messages."""

    label: str
    value: Any
    ref: str
    uncached_formula: bool = False


class SourceTable:
    def __init__(self, name: str, source: Source, sheet: Sheet, sheets: dict[str, Sheet]):
        self.name = name
        self.source = source
        self.sheet = sheet
        self.sheets = sheets
        region = parse_range(source.range) if source.range else CellRange()
        self.region = region.clamp(sheet.n_rows, sheet.n_cols)
        self.by_rows = source.orientation == "rows"
        r = self.region
        if self.by_rows:
            self.index_line = source.index if source.index is not None else r.min_row
            self.label_line = col_to_index(source.labels) if source.labels else r.min_col
            positions = range(r.min_col, (r.max_col or 0) + 1)
            items = range(r.min_row, (r.max_row or 0) + 1)
            index_cell = lambda p: (self.index_line, p)  # noqa: E731
            label_cell = lambda i: (i, self.label_line)  # noqa: E731
        else:
            self.index_line = col_to_index(source.index) if source.index else r.min_col
            self.label_line = source.labels if source.labels is not None else r.min_row
            positions = range(r.min_row, (r.max_row or 0) + 1)
            items = range(r.min_col, (r.max_col or 0) + 1)
            index_cell = lambda p: (p, self.index_line)  # noqa: E731
            label_cell = lambda i: (self.label_line, i)  # noqa: E731

        # axis labels along the index line, skipping the item-label line itself
        self.periods: list[tuple[int, str]] = []
        seen: dict[str, str] = {}
        for p in positions:
            if p == self.label_line:
                continue
            label = label_of(sheet.get_raw(*index_cell(p)))
            if label is None:
                continue
            ref = sheet.ref(*index_cell(p))
            if label in seen:
                raise SourceError(
                    "duplicate_label",
                    f"axis label '{label}' appears twice ({seen[label]} and {ref})",
                )
            seen[label] = ref
            self.periods.append((p, label))

        # item labels along the label line, skipping the index line itself
        self.items: dict[str, int] = {}
        self.duplicates: set[str] = set()
        for i in items:
            if i == self.index_line:
                continue
            label = label_of(sheet.get_raw(*label_cell(i)))
            if label is None:
                continue
            if label in self.items:
                self.duplicates.add(label)
            self.items[label] = i
        self._cell = (lambda i, p: (i, p)) if self.by_rows else (lambda i, p: (p, i))

    def _line_name(self) -> str:
        if self.by_rows:
            return f"row {self.index_line}"
        from .a1 import index_to_col

        return f"column {index_to_col(self.index_line)}"

    @property
    def labels(self) -> list[str]:
        return [label for _, label in self.periods]

    def series(self, item: str) -> list[Entry]:
        if not self.periods:  # checked here, so cell-only sources need no axis labels
            line = "row" if self.by_rows else "column"
            raise SourceError(
                "empty_source",
                f"no axis labels found in the index {line} of '{self.name}' ({self._line_name()})",
            )
        key = item.strip()
        if key in self.duplicates:
            raise SourceError(
                "ambiguous_item", f"item '{key}' appears more than once in source '{self.name}'"
            )
        if key not in self.items:
            suggestions = difflib.get_close_matches(key, list(self.items), n=3)
            hint = (
                f"; did you mean {', '.join(repr(s) for s in suggestions)}?" if suggestions else ""
            )
            raise SourceError(
                "unknown_item",
                f"source '{self.name}' has no item '{key}'{hint}",
                suggestions=suggestions,
            )
        i = self.items[key]
        out = []
        for p, label in self.periods:
            row, col = self._cell(i, p)
            out.append(
                Entry(
                    label,
                    self.sheet.get(row, col),
                    self.sheet.ref(row, col),
                    (row, col) in self.sheet.uncached_formulas,
                )
            )
        return out

    def cell(self, ref: str) -> Entry:
        sheet_name, _, address = ref.rpartition("!")
        sheet = self.sheet
        if sheet_name:
            sheet_name = sheet_name.strip("'")
            if sheet_name not in self.sheets:
                raise SourceError(
                    "unknown_sheet",
                    f"'{self.source.file}' has no sheet '{sheet_name}' "
                    f"(sheets: {', '.join(self.sheets)})",
                )
            sheet = self.sheets[sheet_name]
        row, col = parse_cell(address)
        return Entry(
            address, sheet.get(row, col), sheet.ref(row, col), (row, col) in sheet.uncached_formulas
        )
