---
name: epm-create-model
description: Build a new financial model (DCF, LBO, budget, three-statement or operating model) as a project in epm, through the epm MCP connector at https://epm-nm1g.onrender.com/mcp. Use when the user asks to create, build, set up or start a model in epm, including starting from the example LBO.
---

# Create an epm model

epm models are **named metrics and formulas** over axes such as fiscal years: no cells, no
cell references. You build one by sending batches of operations to the epm MCP connector;
each saved batch becomes a new version of the project, which the user can open in the web
app and roll back.

[reference.md](reference.md) lists every tool, operation, definition field, formula
function and diagnostic. Read it before writing formulas you are unsure of.

## Before you start

- **The connector.** You need the epm tools (`create_project`, `apply_batch`, `validate`,
  ...). If they aren't available, ask the user to add the epm connector, a remote MCP server
  at `https://epm-nm1g.onrender.com/mcp`, and stop there. Don't imitate the tools.
- **The brief.** Know the model type, the periods (first and last year, which are actuals),
  the currency and units, and the outputs the user cares about. Ask only for what you can't
  sensibly assume, and state the assumptions you made.
- **Starting from the example.** For an LBO, copying the sample is usually faster:
  `create_project(name="...", from_project="example-lbo")`, then change it with the
  epm-edit-model skill. Otherwise build from scratch as below.

## Workflow

1. **Create the project.** `create_project(name, description)` returns its `project_id`;
   use it in every later call. Tell the user the ID. The name you give is the model's name
   everywhere (the project list, the web app and the model's own `settings.name`), so choose
   it now; there is no need to set `settings.name` in a batch.
2. **Build in layers, one batch per layer**, in this order, because later layers refer to
   earlier ones:
   1. settings and the axis (`default_axis`, currency, the years and which are historical);
   2. assumptions: one `value` metric per number, with `unit`, `folder` and, when not
      obvious, `description`;
   3. source files and `input` metrics, if the user gave data (upload with a `file`
      operation, then a `source`);
   4. calculations: `formula` metrics, from operations through to the outputs;
   5. presentation and controls: tables, checks, analyses, and `settings.key_outputs`.
3. **Preview every batch first**: `apply_batch(project_id, ops, mode="dry_run", label=...)`.
   If `ok` is false, read `errors`, fix the batch and preview again. Then send the same batch
   with `mode="commit"`.
4. **Validate** once the calculations are in: `validate(project_id)` must report no errors.
   Fix warnings such as `unit_mismatch` or `missing_value` too; they usually mean a real
   mistake.
5. **Check the numbers.** `show_table` and `run(project_id, outputs=[...])`. Sanity-check
   signs, magnitudes and growth; a model that runs isn't necessarily right.
6. **Report**: the `project_id`, what the model contains, the key outputs with their
   values, the assumptions you made, and that every batch is a version the user can roll back.
   `get_project` returns `web_url`, the project's page in the web app: give it to the user.

## Modelling conventions

- **One name.** The project's name is the model's name. To change it, use
  `rename_project`, or set `settings.name` in a batch; either renames both and saves a
  version.
- **Name every number.** `revenue * 0.22` hides an assumption; `revenue * ebitda_margin`
  exposes it to the user and to sensitivities. The only bare numbers in formulas are
  structural ones such as `1 +` or `/ 2`.
- **Names are snake_case** and say what they are: `capex_pct_revenue`, `tlb_interest`.
- **Units on everything:** `USD m` (or the model's currency and scale) for amounts, `%` for
  rates, `x` for multiples, `days`, `count`. Percentages are fractions: `0.08` is 8%.
- **Folders** group the explorer: `Assumptions/...`, then one per statement or block.
- **Roll balances forward with `prev`:** `prev(cash, init=opening_cash) + net_cash_flow`.
  Give `init` whenever the first period has no previous one.
- **Same-period loops** (interest on average debt) need a circularity listing every metric
  in the loop; prefer opening balances when the user doesn't need the precision.
- **Checks** guard what must hold: a balance sheet that balances, sources equal to uses,
  rates above growth.
- **Key outputs** (`settings.key_outputs`) are the few metrics every later preview reports,
  such as `enterprise_value` or `equity_irr`.

## Worked example: a five-year DCF

The user asked for "a simple DCF for Acme, 2026 to 2030, revenue 120m growing 8%".

`create_project(name="Acme DCF", description="Five-year DCF of Acme")` returns a
`project_id`. Each block below is one `apply_batch` call (its `label` and `ops`), first with
`mode="dry_run"`, then with `mode="commit"`.

Settings and the axis (no historical years here):

```json
{
  "label": "Set up the model",
  "ops": [
    {"op": "update", "kind": "settings", "set": {"currency": "USD", "default_axis": "fy"}},
    {"op": "create", "kind": "axis", "name": "fy", "def": {"values": [2026, 2027, 2028, 2029, 2030], "label": "Fiscal year"}}
  ]
}
```

Assumptions, each a named, unit-tagged value:

```json
{
  "label": "Add the assumptions",
  "ops": [
    {"op": "create", "kind": "metric", "name": "base_revenue", "def": {"value": 120, "unit": "USD m", "folder": "Assumptions", "description": "Revenue in the year before the forecast"}},
    {"op": "create", "kind": "metric", "name": "revenue_growth", "def": {"value": 0.08, "unit": "%", "folder": "Assumptions"}},
    {"op": "create", "kind": "metric", "name": "ebitda_margin", "def": {"value": 0.22, "unit": "%", "folder": "Assumptions"}},
    {"op": "create", "kind": "metric", "name": "capex_pct_revenue", "def": {"value": 0.05, "unit": "%", "folder": "Assumptions"}},
    {"op": "create", "kind": "metric", "name": "tax_rate", "def": {"value": 0.25, "unit": "%", "folder": "Assumptions"}},
    {"op": "create", "kind": "metric", "name": "discount_rate", "def": {"value": 0.09, "unit": "%", "folder": "Assumptions", "description": "WACC"}},
    {"op": "create", "kind": "metric", "name": "terminal_growth", "def": {"value": 0.02, "unit": "%", "folder": "Assumptions"}}
  ]
}
```

Calculations, from revenue to enterprise value:

```json
{
  "label": "Add the calculations",
  "ops": [
    {"op": "create", "kind": "metric", "name": "revenue", "def": {"formula": "prev(revenue, init=base_revenue) * (1 + revenue_growth)", "unit": "USD m", "folder": "Operations"}},
    {"op": "create", "kind": "metric", "name": "ebitda", "def": {"formula": "revenue * ebitda_margin", "unit": "USD m", "folder": "Operations"}},
    {"op": "create", "kind": "metric", "name": "capex", "def": {"formula": "revenue * capex_pct_revenue", "unit": "USD m", "folder": "Operations"}},
    {"op": "create", "kind": "metric", "name": "taxes", "def": {"formula": "ebitda * tax_rate", "unit": "USD m", "folder": "Operations"}},
    {"op": "create", "kind": "metric", "name": "fcf", "def": {"formula": "ebitda - taxes - capex", "unit": "USD m", "folder": "Valuation", "description": "Unlevered free cash flow"}},
    {"op": "create", "kind": "metric", "name": "discount_factor", "def": {"formula": "1 / (1 + discount_rate) ** (period_index() + 1)", "unit": "x", "folder": "Valuation"}},
    {"op": "create", "kind": "metric", "name": "pv_fcf", "def": {"formula": "fcf * discount_factor", "unit": "USD m", "folder": "Valuation"}},
    {"op": "create", "kind": "metric", "name": "terminal_value", "def": {"formula": "last(fcf) * (1 + terminal_growth) / (discount_rate - terminal_growth)", "unit": "USD m", "folder": "Valuation"}},
    {"op": "create", "kind": "metric", "name": "enterprise_value", "def": {"formula": "total(pv_fcf) + terminal_value * last(discount_factor)", "unit": "USD m", "folder": "Valuation"}}
  ]
}
```

A table to read it by, a check, a sensitivity heatmap, and the key output:

```json
{
  "label": "Add the table, a check, a sensitivity and the key output",
  "ops": [
    {"op": "create", "kind": "table", "name": "dcf", "def": {"rows": [{"header": "Operations"}, {"metric": "revenue"}, {"metric": "ebitda", "label": "EBITDA"}, {"metric": "taxes"}, {"metric": "capex"}, {"metric": "fcf", "label": "Free cash flow", "style": "subtotal"}, {"header": "Valuation"}, {"metric": "discount_factor"}, {"metric": "pv_fcf", "label": "PV of FCF"}]}},
    {"op": "create", "kind": "check", "name": "discount_above_growth", "def": {"expr": "discount_rate > terminal_growth", "description": "The terminal value needs the discount rate above long-run growth"}},
    {"op": "create", "kind": "analysis", "name": "wacc_growth", "def": {"inputs": [{"metric": "discount_rate", "values": [0.08, 0.09, 0.1]}, {"metric": "terminal_growth", "values": [0.01, 0.02, 0.03]}], "outputs": ["enterprise_value"], "plot": {"kind": "heatmap", "x": "discount_rate", "y": "terminal_growth", "z": "enterprise_value", "format": ",.0f"}}},
    {"op": "update", "kind": "settings", "set": {"key_outputs": ["enterprise_value"]}}
  ]
}
```

`validate` then reports nothing, and `run(project_id, outputs=["enterprise_value"])` gives
about 259 (USD m). `run_analysis(project_id, "wacc_growth", save=True, plot=True)` keeps the
sensitivity and its chart as outputs the user can download.
