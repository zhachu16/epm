import { useState } from "react";
import { errorText, projects } from "../api/client";
import { useRefresh } from "../api/hooks";
import type { AppState } from "../api/types";
import { plural } from "../lib/format";
import { navigate, onLinkClick, projectPath } from "../lib/router";
import { hasUnsavedEdits, useStore } from "../store";

export function TopBar({ state }: { state: AppState }) {
  const { setBottom, log } = useStore();
  const refresh = useRefresh();
  const project = state.project;
  const checks = Object.values(state.checks);
  const failed = checks.filter((c) => c.state !== "passed");
  const errors = state.diagnostics.filter((d) => d.severity === "error").length;
  const warnings = state.diagnostics.filter((d) => d.severity === "warning").length;
  const [copied, setCopied] = useState(false);

  const rename = async () => {
    const name = window.prompt("Project name:", project.name)?.trim();
    if (!name || name === project.name) return;
    try {
      await projects.update(project.id, { name });
      log("info", `renamed the project to ${name}`);
    } catch (e) {
      log("error", errorText(e));
    }
    await refresh();
  };

  const copy = async () => {
    try {
      const name = window.prompt("Name of the copy:", project.is_example ? "My LBO" : `${project.name} (copy)`)?.trim();
      if (name === undefined) return;
      const created = await projects.duplicate(project.id, name || undefined);
      log("info", `made an editable copy: ${created.name}`);
      navigate(projectPath(created.id));
    } catch (e) {
      log("error", errorText(e));
    }
  };

  const share = async () => {
    const link = `${location.origin}${projectPath(project.id)}`;
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      window.prompt("Link to this project:", link);
    }
  };

  return (
    <header className="topbar">
      <a className="brand" href="/" onClick={onLinkClick("/")} title="All projects" data-testid="home">
        epm
      </a>
      {project.is_example ? (
        <span className="title" data-testid="project-name">
          {project.name}
        </span>
      ) : (
        <button className="title link" onClick={rename} title="Rename the project" data-testid="project-name">
          {project.name}
        </button>
      )}
      {project.is_example && <span className="pill warn">example · read-only</span>}
      {!project.is_example && state.version !== project.current_version && (
        <span className="pill warn" data-testid="viewing-version">
          viewing v{state.version} · read-only ·{" "}
          <a href={projectPath(project.id)} onClick={onLinkClick(projectPath(project.id))}>
            back to current
          </a>
        </span>
      )}
      {!project.is_example && state.version === project.current_version && <SaveStatus version={state.version} />}
      <span className="spacer" />
      <button
        className={`pill ${failed.length ? "error" : "ok"}`}
        onClick={() => setBottom("problems")}
        title="Checks"
        data-testid="checks-pill"
      >
        <span className={`dot ${failed.length ? "failed" : "ok"}`} />
        {failed.length ? `${plural(failed.length, "check")} failing` : `${plural(checks.length, "check")} passing`}
      </button>
      <button
        className={`pill ${errors ? "error" : warnings ? "warn" : ""}`}
        onClick={() => setBottom("problems")}
        title="Problems"
        data-testid="problems-pill"
      >
        {errors} errors · {warnings} warnings
      </button>
      {project.is_example && (
        <button className="btn small primary" onClick={copy} data-testid="make-copy">
          Make an editable copy
        </button>
      )}
      <button className="btn small" onClick={share} title="Copy a link to this project" data-testid="share">
        {copied ? "Link copied" : "Copy link"}
      </button>
      <a className="btn small" href={projects.exportUrl(project.id)} title="Download the project as an .epm archive" data-testid="export">
        Export .epm
      </a>
      <a className="btn small" href="/docs" onClick={onLinkClick("/docs")}>
        Docs
      </a>
    </header>
  );
}

/** Whether everything is saved: each saved change is a new version of the project. */
function SaveStatus({ version }: { version: number }) {
  const save = useStore((s) => s.save);
  const unsaved = useStore(hasUnsavedEdits);
  const refresh = useRefresh();
  const { setSave } = useStore();
  if (save.state === "saving") return <span className="pill" data-testid="save-status">Saving…</span>;
  if (save.state === "conflict") {
    return (
      <span className="pill error" title={save.message} data-testid="save-status">
        Not saved: changed elsewhere ·{" "}
        <button className="link" onClick={() => (setSave({ state: "idle" }), refresh())}>
          reload
        </button>
      </span>
    );
  }
  if (save.state === "error") {
    return (
      <span className="pill error" title={save.message} data-testid="save-status">
        Not saved
      </span>
    );
  }
  if (unsaved) {
    return (
      <span className="pill warn" title="An open editor has edits that are not saved yet" data-testid="save-status">
        Unsaved edits · v{version}
      </span>
    );
  }
  return (
    <span className="pill ok" title="Every change is saved as a new version" data-testid="save-status">
      Saved · v{version}
    </span>
  );
}
