import { describe, expect, it } from "vitest";
import { renderGuide, slugify } from "./DocsPage";

describe("renderGuide", () => {
  it("gives headings ids that in-page links can use", () => {
    expect(slugify("6. Edit the same project through MCP")).toBe("6-edit-the-same-project-through-mcp");
    expect(renderGuide("## Saving, versions and recovery")).toContain('id="saving-versions-and-recovery"');
  });

  it("points links between guides at the docs pages", () => {
    const html = renderGuide("See the [agent guide](agent-guide.md#formulas) and [plan](PLAN.md).");
    expect(html).toContain('href="/docs/agent-guide#formulas"');
    expect(html).toContain('href="/docs/PLAN"');
  });
});
