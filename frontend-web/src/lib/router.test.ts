import { describe, expect, it } from "vitest";
import { parseRoute, projectPath } from "./router";

describe("parseRoute", () => {
  it("reads every page", () => {
    expect(parseRoute("/")).toEqual({ page: "projects" });
    expect(parseRoute("/p/p_abc")).toEqual({ page: "project", id: "p_abc" });
    expect(parseRoute("/p/p_abc/", "?version=3")).toEqual({ page: "project", id: "p_abc", version: 3 });
    expect(parseRoute("/p/p_abc", "?version=zero")).toEqual({ page: "project", id: "p_abc" });
    expect(parseRoute("/docs")).toEqual({ page: "docs" });
    expect(parseRoute("/docs/agent-guide")).toEqual({ page: "docs", guide: "agent-guide" });
    expect(parseRoute("/elsewhere/x/y")).toEqual({ page: "missing" });
  });

  it("round-trips project links", () => {
    expect(parseRoute(projectPath("example-lbo"))).toEqual({ page: "project", id: "example-lbo" });
    const [path, search] = projectPath("p x", 2).split("?");
    expect(parseRoute(path, `?${search}`)).toEqual({ page: "project", id: "p x", version: 2 });
  });
});
