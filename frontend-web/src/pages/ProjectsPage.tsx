import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { errorText, projects } from "../api/client";
import type { ProjectInfo } from "../api/types";
import { formatWhen } from "../lib/format";
import { navigate, onLinkClick, projectPath } from "../lib/router";

/** Every project: search, create, import, and per project open, rename, copy, export, delete. */
export function ProjectsPage() {
  const [query, setQuery] = useState("");
  const [showDeleted, setShowDeleted] = useState(false);
  const [message, setMessage] = useState<{ error: boolean; text: string } | null>(null);
  const client = useQueryClient();
  const picker = useRef<HTMLInputElement>(null);
  const q = query.trim();
  const list = useQuery({ queryKey: ["projects", q], queryFn: () => projects.list(q || undefined) });
  const deleted = useQuery({ queryKey: ["projects", "deleted"], queryFn: () => projects.list(undefined, true), enabled: showDeleted });

  useEffect(() => {
    document.title = "Projects · epm";
  }, []);

  const act = async (action: () => Promise<unknown>, done?: string) => {
    try {
      await action();
      if (done) setMessage({ error: false, text: done });
    } catch (e) {
      setMessage({ error: true, text: errorText(e) });
    }
    await client.invalidateQueries({ queryKey: ["projects"] });
  };

  const create = () => {
    const name = window.prompt("Name of the new project:")?.trim();
    if (name) void act(async () => navigate(projectPath((await projects.create(name)).id)));
  };

  const copy = (p: ProjectInfo) => {
    const name = window.prompt("Name of the copy:", p.is_example ? "My LBO" : `${p.name} (copy)`)?.trim();
    if (name === undefined) return;
    void act(async () => navigate(projectPath((await projects.duplicate(p.id, name || undefined)).id)));
  };

  const rename = (p: ProjectInfo) => {
    const name = window.prompt("Project name:", p.name)?.trim();
    if (name && name !== p.name) void act(() => projects.update(p.id, { name }), `renamed to ${name}`);
  };

  const remove = (p: ProjectInfo) => {
    if (window.confirm(`Delete ${p.name}? It can be restored for 30 days.`)) void act(() => projects.remove(p.id), `deleted ${p.name}`);
  };

  const importFiles = async (files: FileList | null) => {
    const file = files?.[0];
    if (picker.current) picker.current.value = "";
    if (!file) return;
    const name = file.name.replace(/\.epm$/i, "");
    await act(async () => navigate(projectPath((await projects.importEpm(file, name)).id)));
  };

  const rows = list.data ?? [];
  const examples = rows.filter((p) => p.is_example);
  const own = rows.filter((p) => !p.is_example);

  return (
    <div className="page">
      <header className="topbar">
        <span className="brand">epm</span>
        <span className="title">Projects</span>
        <span className="spacer" />
        <a className="btn small" href="/docs" onClick={onLinkClick("/docs")}>
          Docs
        </a>
      </header>
      <main className="page-body">
        <div className="row">
          <input
            className="input grow"
            placeholder="Search projects by name or description"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            data-testid="project-search"
          />
          <button className="btn primary" onClick={create} data-testid="new-project">
            New project
          </button>
          <input ref={picker} type="file" accept=".epm,application/zip" hidden onChange={(e) => importFiles(e.target.files)} data-testid="import-input" />
          <button className="btn" onClick={() => picker.current?.click()} title="Upload an .epm archive as a new project">
            Import .epm…
          </button>
        </div>
        {message && <div className={`message ${message.error ? "error" : "ok"}`}>{message.text}</div>}
        {list.error && <div className="message error">{errorText(list.error)}</div>}

        {examples.length > 0 && (
          <section>
            <h2>Example</h2>
            {examples.map((p) => (
              <ProjectRow key={p.id} project={p}>
                <button className="btn small primary" onClick={() => copy(p)} data-testid={`copy-${p.id}`}>
                  Make an editable copy
                </button>
              </ProjectRow>
            ))}
          </section>
        )}

        <section>
          <h2>Your projects</h2>
          {list.isLoading && <div className="muted">Loading…</div>}
          {!list.isLoading && own.length === 0 && (
            <div className="muted">{q ? "No projects match." : "No projects yet: copy the example, create a new one, or import an .epm file."}</div>
          )}
          {own.map((p) => (
            <ProjectRow key={p.id} project={p}>
              <button className="btn small" onClick={() => rename(p)}>
                Rename
              </button>
              <button className="btn small" onClick={() => copy(p)}>
                Duplicate
              </button>
              <a className="btn small" href={projects.exportUrl(p.id)}>
                Export
              </a>
              <button className="btn small danger" onClick={() => remove(p)} data-testid={`delete-${p.id}`}>
                Delete
              </button>
            </ProjectRow>
          ))}
        </section>

        <section>
          <button className="link muted" onClick={() => setShowDeleted((s) => !s)}>
            {showDeleted ? "Hide recently deleted" : "Show recently deleted"}
          </button>
          {showDeleted &&
            (deleted.data ?? []).map((p) => (
              <div key={p.id} className="project-row muted">
                <span className="grow">{p.name}</span>
                <span>deleted {formatWhen(p.deleted_at)}</span>
                <button className="btn small" onClick={() => act(() => projects.restore(p.id), `restored ${p.name}`)}>
                  Restore
                </button>
              </div>
            ))}
          {showDeleted && deleted.data?.length === 0 && <div className="muted">Nothing deleted in the last 30 days.</div>}
        </section>
      </main>
    </div>
  );
}

function ProjectRow({ project, children }: { project: ProjectInfo; children: React.ReactNode }) {
  const to = projectPath(project.id);
  return (
    <div className="project-row" data-testid={`project-${project.id}`}>
      <div className="grow">
        <a href={to} onClick={onLinkClick(to)} className="project-name">
          {project.name}
        </a>
        {project.description && <div className="muted">{project.description}</div>}
      </div>
      <span className="muted" title="Current version">
        v{project.current_version}
      </span>
      <span className="muted">{formatWhen(project.updated_at)}</span>
      <span className="mono muted" title="Project ID">
        {project.id}
      </span>
      {children}
    </div>
  );
}
