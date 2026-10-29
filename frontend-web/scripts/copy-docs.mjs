// Copy the guides from the repository's docs/ into the bundle, so the docs page shows the
// same text as the repository and the package still builds on its own.
import { copyFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const docs = join(root, "..", "docs");
const target = join(root, "src", "generated", "docs");
const guides = ["user-guide.md", "agent-guide.md"];

rmSync(target, { recursive: true, force: true });
mkdirSync(target, { recursive: true });
for (const name of guides) {
  if (existsSync(join(docs, name))) copyFileSync(join(docs, name), join(target, name));
}
