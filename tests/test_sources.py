import numpy as np
import openpyxl
import pytest

from epm import Engine, load_model
from epm.sources import MemoryFiles
from epm.sources.a1 import A1Error, CellRange, col_to_index, index_to_col, parse_cell, parse_range
from epm.sources.reader import label_of, parse_value

YEARS = [2022, 2023, 2024, 2025, 2026, 2027]
HISTORICAL = [2022, 2023, 2024]


# -- A1 references and cell parsing -----------------------------------------------------


def test_a1():
    assert col_to_index("A") == 1 and col_to_index("z") == 26 and col_to_index("AA") == 27
    assert index_to_col(28) == "AB"
    assert parse_cell("$C$7") == (7, 3)
    assert parse_range("B2:D9") == CellRange(2, 2, 9, 4)
    assert parse_range("A:C") == CellRange(1, 1, None, 3)
    assert parse_range("3:5") == CellRange(3, 1, 5, None)
    assert parse_range("C4") == CellRange(4, 3, 4, 3)
    for bad in ("D9:B2", "A1:B2:C3", "hello", "A0"):
        with pytest.raises(A1Error):
            parse_range(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234.5", 1234.5),
        ("(250)", -250.0),
        ("$ 1,000", 1000.0),
        ("12.5%", 0.125),
        ("-", 0.0),
        ("n/a", None),
        ("", None),
        ("  ", None),
        (3, 3.0),
        (True, 1.0),
        ("1e3", 1000.0),
        ("Revenue", "Revenue"),
    ],
)
def test_parse_value(raw, expected):
    assert parse_value(raw) == expected


def test_label_of():
    assert label_of(2024.0) == "2024"
    assert label_of("2024") == "2024"
    assert label_of(" FY24 ") == "FY24"
    assert label_of(None) is None


# -- fixtures ---------------------------------------------------------------------------


def workbook_bytes() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "IS"
    ws.append(["USD m", 2021, 2022, 2023, 2024])
    ws.append(["Revenue", 900, 1000, 1100, 1200])
    ws.append(["  EBITDA", 180, 210, 240, 270])
    ws.append(["Cash", 40, 50, 60, 70])
    ws.append(["Capex", None, "(30)", "-", "n/a"])
    ws.append(["Notes", "x", "text", "y", "z"])
    ws.append(["Depreciation", 1, 2, 3, "=B7*2"])  # formula without a saved value
    deal = wb.create_sheet("Deal")
    deal["A1"], deal["B1"] = "Entry multiple", 9.5
    deal["A2"], deal["B2"] = "Equity", "1,250"
    out = __import__("io").BytesIO()
    wb.save(out)
    return out.getvalue()


CSV = b"""year,volume,price,share
2022,"1,000",10.5,12%
2023,"1,100",11.0,13%
2024,"1,250",11.5,15%
2025,,12.0,
"""


def make(metrics, sources=None, files=None, **settings):
    model = {
        "settings": {"default_axis": "fy", **settings},
        "axes": {"fy": {"values": YEARS, "historical": HISTORICAL}},
        "sources": sources
        or {
            "is": {"file": "inputs/hist.xlsx", "sheet": "IS"},
            "deal": {"file": "inputs/hist.xlsx", "sheet": "Deal"},
            "ops": {"file": "inputs/ops.csv", "orientation": "columns"},
        },
        "metrics": metrics,
    }
    if files is None:
        files = MemoryFiles({"inputs/hist.xlsx": workbook_bytes(), "inputs/ops.csv": CSV})
    return Engine(load_model(model), files)


def by_code(engine, code):
    return [d for d in engine.diagnostics if d.code == code]


# -- mapping ----------------------------------------------------------------------------


def test_history_then_forecast():
    e = make(
        {
            "growth": {"value": 0.10, "unit": "%"},
            "margin": {"value": 0.25, "unit": "%"},
            "revenue": {
                "unit": "USD m",
                "history": {"source": "is", "item": "Revenue"},
                "formula": "prev(revenue) * (1 + growth)",
            },
            "ebitda": {
                "unit": "USD m",
                "history": {"source": "is", "item": "EBITDA"},
                "formula": "revenue * margin",
            },
        }
    )
    assert [d.code for d in e.diagnostics] == ["unmatched_labels"]  # 2021 is not on the axis
    assert e.diagnostics[0].severity == "info"
    r = e.run()
    assert r.ok
    np.testing.assert_allclose(r["revenue"], [1000, 1100, 1200, 1320, 1452, 1597.2])
    np.testing.assert_allclose(r["ebitda"], [210, 240, 270, 330, 363, 399.3])
    assert e.shape("revenue") == "fy"


def test_history_inside_a_recurrence():
    e = make(
        {
            "fcf": {"axis": "fy", "value": 5},
            "cash": {
                "history": {"source": "is", "item": "Cash"},
                "formula": "prev(cash) + fcf",
            },
        }
    )
    assert [s.kind for s in e.steps if "cash" in s.members] == ["recurrence"]
    np.testing.assert_allclose(e.run()["cash"], [50, 60, 70, 75, 80, 85])


def test_input_series_cells_and_scale():
    e = make(
        {
            "capex": {"input": {"source": "is", "item": "Capex", "scale": 1000}},
            "volume": {"input": {"source": "ops", "item": "volume"}},
            "share": {"input": {"source": "ops", "item": "share"}},
            "entry_multiple": {"input": {"source": "deal", "cell": "B1"}},
            "equity": {"input": {"source": "is", "cell": "Deal!B2"}},
            "multiple_by_year": {"axis": "fy", "input": {"source": "deal", "cell": "B1"}},
        }
    )
    r = e.run()
    assert r["capex"].iloc[0] == -30000 and r["capex"].iloc[1] == 0
    assert np.isnan(r["capex"].iloc[2])  # "n/a"
    assert list(r["volume"])[:3] == [1000, 1100, 1250]
    assert np.isnan(r["volume"].iloc[3])
    np.testing.assert_allclose(r["share"].iloc[:3], [0.12, 0.13, 0.15])
    assert r["entry_multiple"] == 9.5
    assert r["equity"] == 1250
    assert list(r["multiple_by_year"]) == [9.5] * 6
    assert e.shape("entry_multiple") is None


def test_range_and_explicit_index_and_labels():
    sources = {
        "block": {
            "file": "inputs/hist.xlsx",
            "sheet": "IS",
            "range": "A1:D3",  # stops before 2024 and before Cash
        },
        "ops": {
            "file": "inputs/ops.csv",
            "orientation": "columns",
            "index": "A",
            "labels": 1,
        },
    }
    e = make(
        {
            "rev": {"input": {"source": "block", "item": "Revenue"}},
            "price": {"input": {"source": "ops", "item": "price"}},
            "cash": {"input": {"source": "block", "item": "Cash"}},
        },
        sources=sources,
    )
    r = e.run()
    assert list(r["rev"])[:2] == [1000, 1100] and np.isnan(r["rev"].iloc[2])
    assert list(r["price"])[:4] == [10.5, 11.0, 11.5, 12.0]
    [d] = by_code(e, "unknown_item")
    assert d.object == "metric:cash"


def test_uncached_formula_is_reported():
    e = make({"dep": {"input": {"source": "is", "item": "Depreciation"}}})
    [d] = by_code(e, "uncached_formula")
    assert d.detail["cells"] == ["IS!E7"]
    assert np.isnan(e.run()["dep"].iloc[2])


# -- errors -----------------------------------------------------------------------------


def test_unknown_item_suggests():
    e = make({"x": {"input": {"source": "is", "item": "Revenues"}}})
    [d] = e.diagnostics[-1:]
    assert d.code == "unknown_item"
    assert d.detail["suggestions"] == ["Revenue"]


def test_non_numeric_cells():
    e = make({"x": {"input": {"source": "is", "item": "Notes"}}})
    [d] = by_code(e, "invalid_value")
    assert "IS!C6" in d.message


def test_labels_not_on_axis():
    sources = {"ops": {"file": "inputs/ops.csv", "orientation": "rows"}}  # wrong orientation
    e = make({"x": {"input": {"source": "ops", "item": "2022"}}}, sources=sources)
    [d] = by_code(e, "axis_mismatch")
    assert "none of the labels" in d.message


def test_missing_file_blocks_bound_metrics():
    e = make(
        {
            "rev": {"input": {"source": "is", "item": "Revenue"}},
            "double": {"formula": "rev * 2"},
            "ok": {"value": 1},
        },
        files=MemoryFiles({}),
    )
    errors = {d.object: d.code for d in e.diagnostics if d.severity == "error"}
    assert errors == {
        "source:is": "missing_file",
        "source:deal": "missing_file",
        "source:ops": "missing_file",
    }
    r = e.run()
    assert r.status["rev"].state == "error"
    assert "not found" in r.status["rev"].message
    assert r.status["double"].state == "blocked"
    assert r.status["ok"].state == "ok"


def test_unknown_sheet_and_source():
    e = make(
        {"x": {"input": {"source": "nope", "item": "Revenue"}}},
        sources={"is": {"file": "inputs/hist.xlsx", "sheet": "PL"}},
    )
    codes = {d.object: d.code for d in e.diagnostics}
    assert codes == {"source:is": "unknown_sheet", "metric:x": "unknown_source"}
    assert "sheets: IS, Deal" in e.diagnostics[0].message


def test_history_on_axis_without_historical_periods():
    model = {
        "axes": {"fy": {"values": [2022, 2023]}},
        "sources": {"is": {"file": "inputs/hist.xlsx", "sheet": "IS"}},
        "metrics": {
            "r": {
                "axis": "fy",
                "history": {"source": "is", "item": "Revenue"},
                "formula": "1",
            }
        },
    }
    e = Engine(load_model(model), MemoryFiles({"inputs/hist.xlsx": workbook_bytes()}))
    assert [d.code for d in e.diagnostics] == ["unused_history", "unmatched_labels"]


def test_bound_series_needs_an_axis():
    model = {
        "axes": {"fy": {"values": [2022]}},
        "sources": {"is": {"file": "inputs/hist.xlsx", "sheet": "IS"}},
        "metrics": {"r": {"input": {"source": "is", "item": "Revenue"}}},
    }
    e = Engine(load_model(model), MemoryFiles({"inputs/hist.xlsx": workbook_bytes()}))
    assert [d.code for d in e.diagnostics] == ["missing_axis"]


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ({"file": "inputs/a.xlsx"}, "needs 'sheet'"),
        ({"file": "inputs/a.csv", "sheet": "x"}, "no sheets"),
        ({"file": "../secrets.csv"}, "must be relative"),
        ({"file": "/etc/passwd.csv"}, "must be relative"),
        ({"file": "inputs/a.parquet"}, "cannot tell the format"),
        ({"file": "inputs/a.csv", "range": "Z9:A1"}, "reversed"),
        ({"file": "inputs/a.csv", "orientation": "rows", "index": "A"}, "row number"),
        ({"file": "inputs/a.csv", "orientation": "columns", "index": 2}, "column letter"),
    ],
)
def test_source_schema_errors(source, message):
    with pytest.raises(ValueError, match=message):
        load_model({"sources": {"s": source}})


def test_binding_schema_errors():
    with pytest.raises(ValueError, match="exactly one of 'item' or 'cell'"):
        load_model({"metrics": {"x": {"input": {"source": "s"}}}})
    with pytest.raises(ValueError, match="needs a 'formula'"):
        load_model({"metrics": {"x": {"value": 1, "history": {"source": "s", "item": "a"}}}})
    with pytest.raises(ValueError, match="not a single cell"):
        load_model({"metrics": {"x": {"formula": "1", "history": {"source": "s", "cell": "A1"}}}})


def test_historical_periods_must_come_first():
    with pytest.raises(ValueError, match="must come before"):
        load_model({"axes": {"fy": {"values": [1, 2, 3], "historical": [2]}}})


def test_directory_files(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "ops.csv").write_bytes(CSV)
    model = load_model(
        {
            "settings": {"default_axis": "fy"},
            "axes": {"fy": {"values": YEARS}},
            "sources": {"ops": {"file": "inputs/ops.csv", "orientation": "columns"}},
            "metrics": {"price": {"input": {"source": "ops", "item": "price"}}},
        }
    )
    assert list(Engine(model, tmp_path).run()["price"])[:2] == [10.5, 11.0]


def test_override_replaces_bound_metric():
    e = make({"rev": {"input": {"source": "is", "item": "Revenue"}}, "x": {"formula": "rev + 1"}})
    r = e.run(overrides={"rev": 5})
    assert list(r["x"]) == [6] * 6


def test_source_with_unknown_axis():
    e = make(
        {
            "r": {"history": {"source": "is", "item": "Revenue"}, "formula": "1"},
            "x": {"formula": "r + 1"},
        },
        sources={"is": {"file": "inputs/hist.xlsx", "sheet": "IS", "axis": "years"}},
    )
    assert [(d.object, d.code) for d in e.diagnostics] == [("source:is", "unknown_axis")]
    r = e.run()
    assert (r.status["r"].state, r.status["x"].state) == ("error", "blocked")
