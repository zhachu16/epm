"""Load sources on demand and resolve metric bindings into numbers on an axis."""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from .files import FileProvider
from .reader import Sheet, SourceError, read_csv, read_xlsx
from .table import SourceTable

if TYPE_CHECKING:
    from ..formula.env import AxisInfo
    from ..model.schema import Binding, Source


def _preview(labels: list[str], n: int = 4) -> str:
    shown = ", ".join(labels[:n])
    return shown + (", ..." if len(labels) > n else "")


@dataclass
class LoadedSeries:
    data: np.ndarray
    unmatched: list[str] = field(default_factory=list)  # source labels not on the axis
    uncached: list[str] = field(default_factory=list)  # formula cells with no saved value


class SourceLoader:
    """Reads each file once and builds each source's table once."""

    def __init__(self, sources: dict[str, Source], files: FileProvider | None):
        self.sources = sources
        self.files = files
        self._sheets: dict[tuple, dict[str, Sheet]] = {}
        self._tables: dict[str, SourceTable | SourceError] = {}
        self.file_hashes: dict[str, str] = {}

    def fingerprint(self, name: str) -> str:
        """Hash of a source's mapping and its file contents (empty if it never loaded)."""
        source = self.sources.get(name)
        spec = source.model_dump(mode="json") if source is not None else None
        file_hash = self.file_hashes.get(source.file, "") if source is not None else ""
        return hashlib.sha256(json.dumps([spec, file_hash], sort_keys=True).encode()).hexdigest()

    def _read(self, source: Source) -> dict[str, Sheet]:
        fmt = source.resolved_format
        key = (source.file, fmt, source.delimiter if fmt == "csv" else None)
        if key not in self._sheets:
            if self.files is None:
                raise SourceError(
                    "missing_file",
                    f"'{source.file}' is not available: the model has no input files",
                )
            try:
                data = self.files.read_bytes(source.file)
            except FileNotFoundError:
                raise SourceError("missing_file", f"input file '{source.file}' not found") from None
            except (OSError, ValueError) as e:
                raise SourceError("missing_file", f"cannot read '{source.file}': {e}") from None
            self.file_hashes[source.file] = hashlib.sha256(data).hexdigest()
            if fmt == "csv":
                self._sheets[key] = {source.file: read_csv(data, source.file, source.delimiter)}
            else:
                self._sheets[key] = read_xlsx(data, source.file)
        return self._sheets[key]

    def table(self, name: str) -> SourceTable:
        if name not in self._tables:
            try:
                self._tables[name] = self._build(name)
            except SourceError as e:
                self._tables[name] = e
        result = self._tables[name]
        if isinstance(result, SourceError):
            raise result
        return result

    def _build(self, name: str) -> SourceTable:
        if name not in self.sources:
            suggestions = difflib.get_close_matches(name, list(self.sources), n=3)
            hint = f"; did you mean {', '.join(suggestions)}?" if suggestions else ""
            raise SourceError(
                "unknown_source", f"unknown source '{name}'{hint}", suggestions=suggestions
            )
        source = self.sources[name]
        sheets = self._read(source)
        if source.resolved_format == "csv":
            sheet = sheets[source.file]
        elif source.sheet in sheets:
            sheet = sheets[source.sheet]  # type: ignore[index]
        else:
            raise SourceError(
                "unknown_sheet",
                f"'{source.file}' has no sheet '{source.sheet}' (sheets: {', '.join(sheets)})",
            )
        return SourceTable(name, source, sheet, sheets)

    def load_series(self, binding: Binding, axis: AxisInfo) -> LoadedSeries:
        table = self.table(binding.source)
        entries = table.series(binding.item)  # type: ignore[arg-type]
        positions = {str(v): i for i, v in enumerate(axis.index)}
        out = LoadedSeries(np.full(len(axis), np.nan))
        bad = []
        matched = 0
        for e in entries:
            pos = positions.get(e.label)
            if pos is None:
                out.unmatched.append(e.label)
                continue
            matched += 1
            if e.value is None:
                if e.uncached_formula:
                    out.uncached.append(e.ref)
                continue
            if not isinstance(e.value, float):
                bad.append(f"{e.ref} ({e.value!r})")
                continue
            out.data[pos] = e.value * binding.scale
        if matched == 0:
            raise SourceError(
                "axis_mismatch",
                f"none of the labels in source '{binding.source}' ({_preview(table.labels)}) are "
                f"on axis '{axis.name}' ({_preview([str(v) for v in axis.index])})",
            )
        if bad:
            raise SourceError("invalid_value", f"non-numeric cells: {_preview(bad, 3)}", cells=bad)
        return out

    def load_cell(self, binding: Binding) -> tuple[float, list[str]]:
        entry = self.table(binding.source).cell(binding.cell)  # type: ignore[arg-type]
        if entry.value is None:
            return np.nan, [entry.ref] if entry.uncached_formula else []
        if not isinstance(entry.value, float):
            raise SourceError("invalid_value", f"{entry.ref} is not a number ({entry.value!r})")
        return entry.value * binding.scale, []
