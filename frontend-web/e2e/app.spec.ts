import { expect, type APIRequestContext, type Page, test } from "@playwright/test";

// One in-memory service for the whole file; each test works on its own copy of the example.
test.describe.configure({ mode: "serial" });

const TAX = (value: number) => ({ op: "update", kind: "metric", name: "tax_rate", set: { value } });

async function copyExample(request: APIRequestContext, name: string): Promise<string> {
  const response = await request.post("/api/projects", { data: { name, from: "example-lbo" } });
  expect(response.ok()).toBe(true);
  return (await response.json()).id;
}

async function openProject(page: Page, id: string) {
  await page.goto(`/p/${id}`);
  await expect(page.locator('[data-testid="graph"] .react-flow__node').first()).toBeVisible();
}

async function openFromExplorer(page: Page, id: string, search?: string) {
  if (search) await page.getByTestId("explorer-search").fill(search);
  await page.getByTestId(`explorer-${id}`).click();
}

test("lists the example and opens it read-only", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("project-example-lbo")).toBeVisible();
  await page.getByTestId("project-example-lbo").getByRole("link").click();
  await expect(page.locator('[data-testid="graph"] .react-flow__node').first()).toBeVisible();
  await expect(page.getByTestId("checks-pill")).toHaveText("4 checks passing");
  await expect(page.getByTestId("make-copy")).toBeVisible();
  await expect(page.getByTestId("explorer-new")).toBeHidden();

  await openFromExplorer(page, "metric:tax_rate", "tax_rate");
  await page.getByTestId("metric-value").fill("0.3");
  await expect(page.getByTestId("save")).toBeDisabled();
  await page.getByTestId("preview").click(); // previews still work
  await expect(page.getByTestId("impact")).toContainText("equity_irr");
});

test("copies the example, edits an assumption, and undoes it", async ({ page }) => {
  await page.goto("/");
  page.once("dialog", (dialog) => dialog.accept("Atlas"));
  await page.getByTestId("copy-example-lbo").click();
  await expect(page).toHaveURL(/\/p\/p_/);
  await expect(page.getByTestId("project-name")).toHaveText("Atlas");
  await expect(page.getByTestId("save-status")).toHaveText("Saved · v1");

  await openFromExplorer(page, "metric:tax_rate", "tax_rate");
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%");
  await page.getByTestId("metric-value").fill("0.3");
  await expect(page.getByTestId("save-status")).toHaveText("Unsaved edits · v1");
  await page.getByTestId("preview").click();
  await expect(page.getByTestId("impact")).toContainText("17.3%");
  await page.getByTestId("save").click();
  await expect(page.getByTestId("save-status")).toHaveText("Saved · v2");
  await expect(page.getByTestId("inspector-value")).toHaveText("30.0%");

  await page.getByTestId("bottom-versions").click();
  await expect(page.getByTestId("version-2")).toContainText("upsert metric 'tax_rate'");
  await page.getByTestId("undo").click();
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%");
  await expect(page.getByTestId("save-status")).toHaveText("Saved · v3");
  await expect(page.getByTestId("version-3")).toContainText("(from v1)");
});

test("shows an earlier version read-only and restores it", async ({ page, request }) => {
  const id = await copyExample(request, "Versions");
  await request.post(`/api/projects/${id}/batches`, { data: { ops: [TAX(0.3)] } });
  await openProject(page, id);
  await page.getByTestId("bottom-versions").click();
  await page.getByTestId("view-1").click();
  await expect(page.getByTestId("viewing-version")).toContainText("viewing v1");
  await openFromExplorer(page, "metric:tax_rate", "tax_rate");
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%");
  await expect(page.getByTestId("rename")).toBeHidden();

  await page.getByTestId("restore-1").click();
  await expect(page.getByTestId("viewing-version")).toBeHidden();
  await expect(page.getByTestId("save-status")).toHaveText("Saved · v3");
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%");
});

test("picks up changes saved elsewhere, such as by an agent", async ({ page, request }) => {
  const id = await copyExample(request, "Shared");
  await openProject(page, id);
  await openFromExplorer(page, "metric:tax_rate", "tax_rate");
  await expect(page.getByTestId("inspector-value")).toHaveText("25.0%");
  const response = await request.post(`/api/projects/${id}/batches`, { data: { ops: [TAX(0.2)], label: "from an agent" } });
  expect(response.ok()).toBe(true);
  await expect(page.getByTestId("inspector-value")).toHaveText("20.0%", { timeout: 15_000 });
  await expect(page.getByTestId("save-status")).toHaveText("Saved · v2");
});

test("refuses a save that would overwrite a newer version", async ({ page, request }) => {
  const id = await copyExample(request, "Conflict");
  await openProject(page, id);
  await openFromExplorer(page, "metric:tax_rate", "tax_rate");
  await page.getByTestId("metric-value").fill("0.3"); // unsaved, so the newer version waits
  await request.post(`/api/projects/${id}/batches`, { data: { ops: [TAX(0.2)] } });
  await expect(page.getByTestId("outside-changes")).toBeVisible({ timeout: 15_000 });
  await page.getByTestId("save").click();
  await expect(page.getByTestId("editor-errors")).toContainText("saved since version 1");
  await expect(page.getByTestId("save-status")).toContainText("changed elsewhere");
});

test("saves analysis results and its chart as outputs", async ({ page, request }) => {
  const id = await copyExample(request, "Outputs");
  await openProject(page, id);
  await openFromExplorer(page, "analysis:entry_exit_irr");
  await page.getByTestId("save-results").click();
  await expect(page.getByTestId("chart")).toBeVisible();
  await expect(page.getByTestId("outputs")).toContainText("entry_exit_irr.png");
  await expect(page.getByTestId("outputs")).toContainText("entry_exit_irr.csv");
  const download = page.waitForEvent("download");
  await page.getByTestId("output-entry_exit_irr.csv").click();
  expect((await download).suggestedFilename()).toBe("entry_exit_irr.csv");
});

test("uploads an input file and exports and imports projects", async ({ page, request }) => {
  const id = await copyExample(request, "Files");
  await openProject(page, id);
  await page.getByTestId("bottom-files").click();
  await page.getByTestId("upload-input").setInputFiles("e2e/fixtures/budget.csv");
  await expect(page.getByTestId("files")).toContainText("inputs/budget.csv");
  await expect(page.getByTestId("save-status")).toHaveText("Saved · v2");

  const exported = await request.get(`/api/projects/${id}/export`);
  await page.goto("/");
  await page.getByTestId("import-input").setInputFiles({ name: "files-copy.epm", mimeType: "application/zip", buffer: await exported.body() });
  await expect(page).toHaveURL(/\/p\/p_/);
  await expect(page.getByTestId("project-name")).toHaveText("files-copy");
  await page.getByTestId("bottom-files").click();
  await expect(page.getByTestId("files")).toContainText("inputs/budget.csv");
});

test("searches, renames and deletes projects", async ({ page, request }) => {
  const id = await copyExample(request, "Searchable");
  await page.goto("/");
  await page.getByTestId("project-search").fill("searchable");
  await expect(page.getByTestId(`project-${id}`)).toBeVisible();
  await expect(page.getByTestId("project-example-lbo")).toBeHidden();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByTestId(`delete-${id}`).click();
  await expect(page.getByTestId(`project-${id}`)).toBeHidden();
});

test("shows the docs", async ({ page }) => {
  await page.goto("/docs");
  await expect(page.getByTestId("docs")).toContainText("Getting started");
  await page.getByRole("link", { name: "agent guide" }).first().click();
  await expect(page).toHaveURL(/\/docs\/agent-guide$/);
  await expect(page.getByTestId("docs")).toContainText("Ground rules");
  await page.goto("/docs/agent-guide#diagnostics");
  await expect(page.locator("#diagnostics")).toBeVisible();
});
