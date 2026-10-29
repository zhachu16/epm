import { AuthGate } from "./components/AuthGate";
import { onLinkClick, useRoute } from "./lib/router";
import { DocsPage } from "./pages/DocsPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ProjectView } from "./pages/ProjectView";

export default function App() {
  const route = useRoute();
  return (
    <AuthGate>
      {route.page === "projects" && <ProjectsPage />}
      {route.page === "project" && <ProjectView key={route.id} id={route.id} version={route.version} />}
      {route.page === "docs" && <DocsPage guide={route.guide} />}
      {route.page === "missing" && (
        <div className="empty">
          <p>There is nothing here.</p>
          <a href="/" onClick={onLinkClick("/")}>
            All projects
          </a>
        </div>
      )}
    </AuthGate>
  );
}
