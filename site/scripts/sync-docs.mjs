// Copy the repository's canonical markdown into src/pages/ and rewrite its
// links for the site.
//
// The four source files stay the single source of truth: they are what people
// read on github.com, and nothing here edits them. Everything this script
// writes is generated and gitignored, so the site can never drift from the
// repository — re-running it is the only way content changes.
//
// Run via `npm run sync`; `npm run build` and `npm run dev` both do it first.

import { mkdir, readdir, readFile, writeFile, copyFile, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const repo = resolve(here, "../..");
const pages = resolve(here, "../src/pages");
const publicDir = resolve(here, "../public");

const BASE = "/healing-hertz";
const BLOB = "https://github.com/anubhabbehera/healing-hertz/blob/main";

/** source → route. `index` is the site root. */
const DOCS = [
  // "Wiki", not "healing-hertz": the topbar already carries the wordmark, so
  // the repo name as a page heading is the same word twice.
  { from: "README.md", slug: "index", title: "Wiki", nav: "Overview" },
  { from: "docs/rules.md", slug: "rules", title: "Writing diagnostic rules", nav: "Rules" },
  { from: "CONTRIBUTING.md", slug: "contributing", title: "Contributing", nav: "Contributing" },
  { from: "SECURITY.md", slug: "security", title: "Security policy", nav: "Security" },
];

/** Repo-relative link targets that become site routes. */
const ROUTES = new Map([
  ["CONTRIBUTING.md", `${BASE}/contributing/`],
  ["docs/rules.md", `${BASE}/rules/`],
  ["SECURITY.md", `${BASE}/security/`],
  ["README.md", `${BASE}/`],
]);

/** Images are copied into the site rather than linked back to the repository. */
const IMAGE_DIR = "docs/screenshots/";

/**
 * Rewrite markdown link targets.
 *
 * Four cases, in order: an image is served from the site's own copy; a file the
 * site has a page for becomes that route; any other repo-relative path becomes
 * an absolute github.com link, so `LICENSE` and source files resolve instead of
 * 404ing; external links and in-page anchors are left exactly as they are.
 *
 * The image case is not cosmetic. Falling through to the github.com branch
 * would point an <img> at a blob URL, which serves an HTML page — the image
 * silently breaks rather than 404ing, so it would ship looking fine in the
 * markdown and broken on the page.
 */
function rewriteLinks(markdown) {
  return markdown.replace(/\]\(([^)]+)\)/g, (whole, target) => {
    if (/^(https?:|mailto:|#)/.test(target)) return whole;

    // Split a trailing anchor so `docs/rules.md#schema` maps correctly.
    const [path, hash = ""] = target.split("#");
    const suffix = hash ? `#${hash}` : "";

    if (path.startsWith(IMAGE_DIR)) {
      return `](${BASE}/screenshots/${path.slice(IMAGE_DIR.length)}${suffix})`;
    }

    const route = ROUTES.get(path);
    if (route) return `](${route}${suffix})`;

    return `](${BLOB}/${path.replace(/^\.\//, "")}${suffix})`;
  });
}

/**
 * The README opens with a wordmark and three badges. Both are repo-page
 * furniture: the layout already draws the wordmark in the topbar, and build
 * status belongs next to the code, not in the documentation.
 */
function stripRepoFurniture(markdown) {
  return markdown
    .replace(/^<picture>[\s\S]*?<\/picture>\s*/m, "")
    .replace(/^(\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)\s*)+/m, "")
    .trimStart();
}

/**
 * Drop the document's leading `# Heading`. The layout renders the title from
 * frontmatter, so leaving this in prints it twice.
 */
function stripLeadingH1(markdown) {
  return markdown.replace(/^#\s+.*\n+/, "");
}

/**
 * Quote a value for YAML frontmatter.
 *
 * JSON.stringify rather than escaping quotes by hand: escaping `"` alone leaves
 * the backslash unescaped, so a value ending in one closes the string early and
 * the next character escapes the closing quote instead. YAML's double-quoted
 * scalars accept JSON's escapes, so this is both correct and complete.
 */
const quote = (value) => JSON.stringify(value);

async function main() {
  // Wipe generated pages first so a renamed or removed source file cannot leave
  // an orphan route behind.
  await rm(pages, { recursive: true, force: true });
  await mkdir(pages, { recursive: true });
  await mkdir(publicDir, { recursive: true });

  // Synth is dark-only, so only the dark wordmark is ever needed.
  await copyFile(
    resolve(repo, "docs/wordmark-dark.svg"),
    resolve(publicDir, "wordmark-dark.svg"),
  );

  // Screenshots, copied rather than linked so the site serves its own images.
  const shotsFrom = resolve(repo, IMAGE_DIR);
  const shotsTo = resolve(publicDir, "screenshots");
  await mkdir(shotsTo, { recursive: true });
  for (const file of await readdir(shotsFrom)) {
    if (!/\.(png|jpe?g|webp|svg)$/i.test(file)) continue;
    await copyFile(resolve(shotsFrom, file), resolve(shotsTo, file));
  }

  for (const doc of DOCS) {
    const raw = await readFile(resolve(repo, doc.from), "utf8");

    let body = doc.slug === "index" ? stripRepoFurniture(raw) : stripLeadingH1(raw);
    body = rewriteLinks(body);

    const frontmatter = [
      "---",
      "layout: ../layouts/Wiki.astro",
      `title: ${quote(doc.title)}`,
      `nav: ${quote(doc.nav)}`,
      `source: ${quote(doc.from)}`,
      "---",
      "",
    ].join("\n");

    await writeFile(resolve(pages, `${doc.slug}.md`), frontmatter + body);
    console.log(`  ${doc.from} → src/pages/${doc.slug}.md`);
  }
}

await main();
