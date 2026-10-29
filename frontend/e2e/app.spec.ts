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
  await page.getByTestId("run-analysis").click();
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
