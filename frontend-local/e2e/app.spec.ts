import { expect, type Page, test } from "@playwright/test";

// One server and one model for the whole file, so the tests run in order and each leaves
// the model as it found it.
test.describe.configure({ mode: "serial" });

async function openApp(page: Page) {
  await page.goto("/");
  await expect(page.locator('[data-testid="graph"] .react-flow__node').first()).toBeVisible();
}

async function openFromExplorer(page: Page, id: string, search?: string) {
  if (search) await page.getByTestId("explorer-search").fill(search);
  await page.getByTestId(`explorer-${id}`).click();
}

/** Type into the Monaco editor inside ``testId``, replacing what it holds. */
async function typeInEditor(page: Page, testId: string, text: string) {
  await page.locator(`[data-testid="${testId}"] .monaco-editor`).click();
  await page.keyboard.press("ControlOrMeta+A");
  await page.keyboard.type(text);
}

test("opens on the dependency graph with checks passing", async ({ page }) => {
  await openApp(page);
  await expect(page.getByTestId("checks-pill")).toHaveText("4 checks passing");
  await expect(page.getByTestId("problems-pill")).toHaveText("0 errors · 0 warnings");
});

test("edits an input with a preview of its effect, then undoes it", async ({ page }) => {
  await openApp(page);
  await openFromExplorer(page, "metric:tax_rate", "tax_rate");
  await expect(page.getByTestId("metric-editor")).toBeVisible();
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%");

  await page.getByTestId("metric-value").fill("0.3");
  await page.getByTestId("preview").click();
  const impact = page.getByTestId("impact");
  await expect(impact).toContainText("equity_irr");
  await expect(impact).toContainText("17.3%");
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%"); // nothing saved yet

  await page.getByTestId("save").click();
  await expect(page.getByTestId("inspector-value")).toHaveText("30.0%");
  await expect(page.getByTestId("metric-value")).toHaveValue("0.3");

  await page.getByTestId("undo").click();
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%");
  await expect(page.getByTestId("metric-value")).toHaveValue("0.25");
});

test("creates a metric, rejects an invalid formula, then deletes it", async ({ page }) => {
  await openApp(page);
  page.once("dialog", (dialog) => dialog.accept("revenue_doubled"));
  await page.getByTestId("explorer-new").selectOption("metric");
  await expect(page.getByTestId("tab-metric:revenue_doubled")).toBeVisible();

  await typeInEditor(page, "metric-formula", "revenue */ 2");
  await page.getByTestId("save").click();
  await expect(page.getByTestId("editor-errors")).toBeVisible();

  await typeInEditor(page, "metric-formula", "revenue * 2");
  await page.getByTestId("save").click();
  await expect(page.getByTestId("editor-errors")).toBeHidden();
  await expect(page.getByTestId("inspector-value")).toHaveText("2,809.0");

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByTestId("delete").click();
  await expect(page.getByTestId("tab-metric:revenue_doubled")).toBeHidden();
  await page.getByTestId("explorer-search").fill("revenue_doubled");
  await expect(page.getByTestId("explorer-metric:revenue_doubled")).toBeHidden();
});

test("runs fm commands in the terminal", async ({ page }) => {
  await openApp(page);
  await page.getByTestId("bottom-terminal").click();
  const terminal = page.getByTestId("terminal");
  await terminal.click();
  await page.keyboard.type("show equity_irr");
  await page.keyboard.press("Enter");
  await expect(terminal).toContainText("irr(equity_cash_flows)");
});

test("reviews and commits an agent's proposal", async ({ page, request }) => {
  await openApp(page);
  const token = await page.locator('meta[name="epm-token"]').getAttribute("content");
  const response = await request.post("/api/batches", {
    headers: { "X-EPM-Token": token ?? "" },
    data: {
      label: "lower the tax rate",
      mode: "propose",
      ops: [{ op: "update", kind: "metric", name: "tax_rate", set: { value: 0.2 } }],
    },
  });
  expect(response.ok()).toBe(true);

  await page.getByTestId("bottom-changes").click();
  const proposal = page.locator('[data-testid^="pending-"]');
  await expect(proposal).toContainText("lower the tax rate");
  await proposal.click();
  await expect(page.getByTestId("impact")).toContainText("equity_irr");
  await page.getByTestId("commit-pending").click();
  await expect(proposal).toBeHidden();

  await openFromExplorer(page, "metric:tax_rate", "tax_rate");
  await expect(page.getByTestId("inspector-value")).toHaveText("20.0%");
  await page.getByTestId("undo").click();
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%");
});

test("runs an analysis and charts it", async ({ page }) => {
  await openApp(page);
  await openFromExplorer(page, "analysis:entry_exit_irr");
  await page.keyboard.press("ControlOrMeta+Enter"); // the Run shortcut
  await expect(page.getByTestId("chart")).toBeVisible();
  await expect(page.locator('[data-testid="analysis-results"] tbody tr')).toHaveCount(25);
});

test("shows tables and the raw model files", async ({ page }) => {
  await openApp(page);
  await openFromExplorer(page, "table:income_statement");
  await expect(page.getByTestId("table-editor")).toContainText("net_income");

  await openFromExplorer(page, "file:model.json");
  await expect(page.getByTestId("file-model.json")).toContainText("Project Atlas LBO");
});

test("saves a new analysis built in the form", async ({ page }) => {
  await openApp(page);
  page.once("dialog", (dialog) => dialog.accept("tax_check"));
  await page.getByTestId("explorer-new").selectOption("analysis");
  const editor = page.getByTestId("analysis-editor");
  await editor.getByRole("button", { name: "Add input" }).click();
  await editor.locator("select").filter({ hasText: "metric…" }).selectOption("tax_rate");
  await editor.getByPlaceholder("0.04, 0.06, 0.08").fill("0.2, 0.3");
  await editor.locator("select").filter({ hasText: "add output…" }).selectOption("equity_irr");
  await expect(page.getByTestId("action-status")).toHaveText("Unsaved changes");
  await page.getByTestId("save").click();
  await expect(page.getByTestId("action-status")).toHaveText("No unsaved changes");
  await expect(page.getByTestId("editor-errors")).toBeHidden();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByTestId("delete").click();
  await expect(page.getByTestId("tab-analysis:tax_check")).toBeHidden();
});

test("the inspector follows the active tab", async ({ page }) => {
  await openApp(page);
  const heading = page.locator('[data-testid="inspector"] h2');
  await openFromExplorer(page, "metric:tax_rate", "tax_rate");
  await openFromExplorer(page, "table:income_statement", "income_statement");
  await expect(heading).toHaveText("income_statement");
  await page.getByTestId("tab-metric:tax_rate").click();
  await expect(heading).toHaveText("tax_rate");
  await page.getByTestId("tab-table:income_statement").click();
  await expect(heading).toHaveText("income_statement");
});

test("edits a definition as JSON and saves it with the keyboard", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await openApp(page);
  await openFromExplorer(page, "metric:tax_rate", "tax_rate");
  await page.getByTestId("view-json").click();
  await expect(page.getByTestId("definition-json")).toContainText('"value": 0.25');
  await page.locator('[data-testid="definition-json"] .monaco-editor').click();
  await page.keyboard.press("ControlOrMeta+A");
  // pasted, not typed: Monaco would auto-close the brackets and quotes as they are typed
  const json = '{"unit": "%", "folder": "Assumptions/Operations", "value": 0.3}';
  await page.evaluate((text) => navigator.clipboard.writeText(text), json);
  await page.keyboard.press("ControlOrMeta+V");
  // the clipboard paste lands asynchronously; save once it has
  await expect(page.getByTestId("action-status")).toHaveText("Unsaved changes");
  await page.keyboard.press("ControlOrMeta+S");
  await expect(page.getByTestId("inspector-value")).toHaveText("30.0%");
  await expect(page.getByTestId("action-status")).toHaveText("No unsaved changes");

  await page.getByTestId("view-form").click();
  await expect(page.getByTestId("metric-value")).toHaveValue("0.3");
  await page.getByTestId("undo").click();
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%");
});

test("drags graph nodes, and resets the layout", async ({ page }) => {
  await openApp(page);
  await expect(page.locator(".react-flow__minimap")).toHaveCount(0);
  const node = page.locator('[data-testid="graph"] .react-flow__node').first();
  // where the layout puts it, in graph coordinates, whatever the zoom and pan
  const laidOut = await node.evaluate((el) => (el as HTMLElement).style.transform);
  const before = (await node.boundingBox())!;
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2);
  await page.mouse.down();
  await page.mouse.move(before.x + before.width / 2 + 120, before.y + before.height / 2 + 90, { steps: 8 });
  await page.mouse.up();
  const after = (await node.boundingBox())!;
  expect(after.x - before.x).toBeGreaterThan(60);
  await expect(page.getByTestId("reset-layout")).toBeEnabled();

  await page.getByTestId("reset-layout").click();
  await expect.poll(() => node.evaluate((el) => (el as HTMLElement).style.transform)).toBe(laidOut);
  await expect(page.getByTestId("reset-layout")).toBeDisabled();
});
