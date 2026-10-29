import { Marked } from "marked";
import { useEffect, useMemo } from "react";
import { onLinkClick } from "../lib/router";

// Copied from the repository's docs/ by scripts/copy-docs.mjs before every build.
const files = import.meta.glob("../generated/docs/*.md", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

export interface Guide {
  slug: string;
  title: string;
  markdown: string;
}

const ORDER = ["user-guide", "agent-guide"];

export const GUIDES: Guide[] = Object.entries(files)
  .map(([path, markdown]) => {
    const slug = path.split("/").pop()!.replace(/\.md$/, "");
    const title = /^#\s+(.+)$/m.exec(markdown)?.[1] ?? slug;
    return { slug, title, markdown };
  })
  .sort((a, b) => ORDER.indexOf(a.slug) - ORDER.indexOf(b.slug));

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/<[^>]+>/g, "")
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

/**
 * Markdown to HTML, with heading ids so in-page links such as (#diagnostics) work, and links
 * between guides (agent-guide.md#formulas) pointing at their pages here.
 */
export function renderGuide(markdown: string): string {
  const marked = new Marked({
    renderer: {
      heading({ tokens, depth }) {
        const html = this.parser.parseInline(tokens);
        return `<h${depth} id="${slugify(html)}">${html}</h${depth}>\n`;
      },
    },
  });
  return marked
    .parse(markdown, { async: false })
    .replace(/href="([\w-]+)\.md(#[^"]*)?"/g, (_, slug: string, hash = "") => `href="/docs/${slug}${hash}"`);
}

export function DocsPage({ guide }: { guide?: string }) {
  const current = GUIDES.find((g) => g.slug === guide) ?? GUIDES[0];
  const html = useMemo(() => (current ? renderGuide(current.markdown) : ""), [current]);

  useEffect(() => {
    document.title = `${current?.title ?? "Docs"} · epm`;
    const target = location.hash ? document.getElementById(decodeURIComponent(location.hash.slice(1))) : null;
    if (target) target.scrollIntoView();
    else window.scrollTo(0, 0);
  }, [current]);

  return (
    <div className="page">
      <header className="topbar">
        <a className="brand" href="/" onClick={onLinkClick("/")}>
          epm
        </a>
        <span className="title">Docs</span>
        {GUIDES.map((g) => (
          <a key={g.slug} className={`btn small ${g === current ? "primary" : ""}`} href={`/docs/${g.slug}`} onClick={onLinkClick(`/docs/${g.slug}`)}>
            {g.title}
          </a>
        ))}
        <span className="spacer" />
        <a className="btn small" href="/" onClick={onLinkClick("/")}>
          Projects
        </a>
      </header>
      <main className="page-body docs" data-testid="docs">
        {current ? <article dangerouslySetInnerHTML={{ __html: html }} /> : <p className="muted">No guides were bundled with this build.</p>}
      </main>
    </div>
  );
}
