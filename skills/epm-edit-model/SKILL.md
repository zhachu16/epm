---
name: epm-edit-model
description: Inspect, change, extend, fix or run scenarios on an existing epm financial model through the epm MCP connector at https://epm-nm1g.onrender.com/mcp. Use when the user asks to change an assumption, add or rename metrics, fix errors or failing checks, add tables, checks or sensitivities, compare scenarios, or undo or roll back changes in an epm project.
---

# Edit an epm model

An epm project is a model of **named metrics and formulas** over axes such as fiscal years.
You change it only with batches of operations sent to the epm MCP connector. Each saved
batch is a new version that the user sees in the web app and can roll back, so every change
you make is visible and reversible.

[reference.md](reference.md) lists every tool, operation, definition field, formula
function and diagnostic.

## Before you start

- **The connector.** You need the epm tools (`search_projects`, `show`, `apply_batch`, ...).
  If they aren't available, ask the user to add the epm connector, a remote MCP server at
  `https://epm-nm1g.onrender.com/mcp`, and stop there.
- **The project.** Every call needs its `project_id`. If the user gave a name instead, use
  `search_projects`; if several match, ask which one. The example, `example-lbo`, is
  read-only: offer `duplicate_project` and work on the copy.

## Workflow

1. **Read before you write.** `get_project` (note `current_version`), then whatever the
   change touches: `list_objects` to find names, `show` for definitions and values,
   `trace` for what depends on what, `show_table` for statements. Never guess a name: an
   unknown name fails with `unknown_reference` and suggestions.
2. **Note the starting point.** `validate` shows problems that were already there, so you
   neither blame them on your change nor leave yours behind.
3. **Write one batch per logical change**, with a `label` the user will recognise in the
   version list ("Base rate up 100bp", not "update").
4. **Preview it**: `apply_batch(project_id, ops, mode="dry_run", label=...)`. Read `errors`
   and `new_errors`; fix and preview again until `ok` is true. Then tell the user what
   changes (`changes`) and how the key outputs move (`impact.outputs`, before → after).
5. **Commit when the user wants the change**: the same call with `mode="commit"` and
   `expected_version` set to the `current_version` you read. If it fails with
   `version_conflict`, someone saved in between: read again, re-preview, and say so. If the
   user only asked "what if", don't commit: use `run` with `overrides`, or `sweep`.
6. **Verify**: `validate`, then `show` or `run` the outputs the user cares about. Report
   the new version number.

## Making changes

- **Change a value or formula:** `update` with only the fields that change.
- **Switch how a metric is defined:** a metric has exactly one of `value`, `input` or
  `formula`, and `update` merges, so clear the old one in the same `set`
  (`{"value": null, "formula": "..."}`).
- **Add an input:** create a `value` metric with a `unit`, `folder` and, where useful, a
  `description`, then refer to it by name. Never put a new bare number in a formula.
- **Rename:** use `rename`; every formula, table, check, analysis and circularity is
  updated. Don't delete and recreate.
- **Delete:** `trace` the object `downstream` first. A delete fails while anything uses
  it, which is a safeguard, not something to work around.
- **Keep the model's style:** its units, folder names and naming pattern.
- **Checks:** fix the model, not the check. Loosen a tolerance only if that is the actual
  fix, and say so.
- **Same-period loops:** `circular_reference` lists the loop's members. Use `prev()` if the
  loop should cross periods; otherwise add the members to a circularity.

## Scenarios without saving

- `run(project_id, overrides={"exit_multiple": 8}, outputs=["equity_irr", "moic"])`
  evaluates a what-if; the model doesn't change.
- `sweep(project_id, inputs={"interest_rate": [0.04, 0.05, 0.06]}, outputs=["equity_irr"])`
  runs a grid of scenarios. A series output needs a period: `tlb_close@2029`.
- To keep a sensitivity, save it as an analysis (below), then
  `run_analysis(project_id, name, save=True, plot=True)`; the results and chart become
  downloadable outputs (`list_outputs`).

## Undo and roll back

- `undo(project_id)` goes back one change; calling it again keeps going back.
- `rollback(project_id, version)` restores any version from `list_versions`.
- Both save the restored state as a new version, so they can be undone too. Use them when
  the user asks, or to reverse your own committed mistake, and say what you did.

## Examples on a copy of the sample LBO

Each block is one `apply_batch` call (its `label` and `ops`).

A changed assumption. The preview shows the equity IRR moving from 17.3% to 17.0%:

```json
{
  "label": "Base rate up 100bp",
  "ops": [
    {"op": "update", "kind": "metric", "name": "interest_rate", "set": {"value": 0.05}}
  ]
}
```

A new assumption, wired into an existing formula:

```json
{
  "label": "Add a management fee",
  "ops": [
    {"op": "create", "kind": "metric", "name": "management_fee_rate", "def": {"value": 0.01, "unit": "%", "folder": "Assumptions/Operations", "description": "Sponsor management fee, share of revenue"}},
    {"op": "create", "kind": "metric", "name": "management_fee", "def": {"formula": "revenue * management_fee_rate * is_forecast()", "unit": "USD m", "folder": "Income statement"}},
    {"op": "update", "kind": "metric", "name": "sga", "set": {"formula": "revenue * sga_pct_revenue + management_fee"}}
  ]
}
```

A value turned into a formula: clear `value` in the same update:

```json
{
  "label": "Split the tax rate into federal and state",
  "ops": [
    {"op": "create", "kind": "metric", "name": "federal_tax_rate", "def": {"value": 0.21, "unit": "%", "folder": "Assumptions/Operations"}},
    {"op": "create", "kind": "metric", "name": "state_tax_rate", "def": {"value": 0.04, "unit": "%", "folder": "Assumptions/Operations"}},
    {"op": "update", "kind": "metric", "name": "tax_rate", "set": {"value": null, "formula": "federal_tax_rate + state_tax_rate"}}
  ]
}
```

A clearer name, with every reference updated:

```json
{
  "label": "Spell out working capital days",
  "ops": [
    {"op": "rename", "kind": "metric", "name": "dso", "to": "receivable_days"},
    {"op": "update", "kind": "metric", "name": "receivable_days", "set": {"unit": "days", "description": "Receivables as days of revenue"}}
  ]
}
```

A sensitivity to keep, and a check:

```json
{
  "label": "Tax rate sensitivity",
  "ops": [
    {"op": "create", "kind": "analysis", "name": "tax_sensitivity", "def": {"inputs": [{"metric": "tax_rate", "range": {"start": 0.2, "stop": 0.3, "step": 0.025}}], "outputs": ["equity_irr", "moic"], "plot": {"kind": "line", "x": "tax_rate", "y": ["equity_irr"], "format": ".1%"}}},
    {"op": "create", "kind": "check", "name": "leverage_below_seven", "def": {"expr": "leverage <= 7", "severity": "warning", "description": "Total debt stays below 7x EBITDA"}}
  ]
}
```
