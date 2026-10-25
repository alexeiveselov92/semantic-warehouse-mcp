/*
 * sync-docs.mjs — bring the guide (`docs/`) and the changelog into the site's content at build time, so `docs/` stays
 * the single source. Run by `npm run dev` and `npm run build`.
 *
 * For each page it:
 *   - writes Starlight's frontmatter, the title taken from the page's `# H1` (which is then dropped);
 *   - rewrites links between pages (`reference/tools.md#…`) to the routes the site serves, and links to other files of
 *     the repository (`../examples/config.example.yaml`) to the file on GitHub.
 *
 * Every Markdown page under `docs/` must be listed below: a page the site doesn't carry stops the build — the guide on
 * the web is the whole guide. The output folder (src/content/docs) is made again on every run and not committed. The
 * landing page at `/` is not one of these pages: it is src/pages/index.astro, a page of its own outside Starlight.
 */
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const WEBSITE = path.resolve(here, '..');
const ROOT = path.resolve(WEBSITE, '..');
const OUT = path.join(WEBSITE, 'src', 'content', 'docs');
const REPOSITORY = 'https://github.com/alexeiveselov92/semantic-warehouse-mcp';

// The pages, in the sidebar's order (astro.config.mjs): the repository's file, and its place on the site.
const PAGES = [
  { src: 'docs/getting-started.md', dest: 'getting-started.md' },
  { src: 'docs/reference/tools.md', dest: 'reference/tools.md' },
  { src: 'docs/reference/configuration.md', dest: 'reference/configuration.md' },
  { src: 'docs/reference/semantic-models.md', dest: 'reference/semantic-models.md' },
  { src: 'docs/reference/dbt-importer.md', dest: 'reference/dbt-importer.md' },
  { src: 'docs/reference/database-importer.md', dest: 'reference/database-importer.md' },
  { src: 'docs/reference/guard.md', dest: 'reference/guard.md' },
  { src: 'docs/reference/engines.md', dest: 'reference/engines.md' },
  { src: 'docs/reference/running.md', dest: 'reference/running.md' },
  { src: 'docs/README.md', dest: 'guide.md', title: 'About this guide' },
  { src: 'CHANGELOG.md', dest: 'changelog.md', title: 'Changelog' },
];

const routeOf = (dest) => '/' + dest.replace(/\.mdx?$/, '') + '/';
const ROUTES = new Map(PAGES.map((page) => [page.src, routeOf(page.dest)]));
ROUTES.set('docs', routeOf('guide.md'));

let warnings = 0;

/** The first `# H1` as the title, and the page without it. */
function titled(content, override) {
  const lines = content.split('\n');
  let title = override;
  const at = lines.findIndex((line) => /^#\s+\S/.test(line));
  if (at !== -1) {
    title = title || lines[at].replace(/^#\s+/, '').trim();
    lines.splice(at, lines[at + 1]?.trim() === '' ? 2 : 1);
  }
  return { title: title || 'Untitled', body: lines.join('\n') };
}

/** Where a link from a page leads on the site — null to leave it as written. */
function resolve(target, from) {
  if (/^([a-z][a-z0-9+.-]*:|#|\/)/i.test(target)) return null; // a web address, an anchor, or already a route
  const cut = target.indexOf('#');
  const file = cut === -1 ? target : target.slice(0, cut);
  const anchor = cut === -1 ? '' : target.slice(cut);
  if (!file) return null;
  const whole = path.posix.normalize(path.posix.join(path.posix.dirname(from), file)).replace(/\/$/, '');
  if (ROUTES.has(whole)) return ROUTES.get(whole) + anchor;
  if (whole.startsWith('..')) {
    warnings++;
    console.warn(`  ! ${from}: "${target}" leads outside the repository — left as written`);
    return null;
  }
  return `${REPOSITORY}/blob/main/${whole}${anchor}`; // a file of the repository the site doesn't carry
}

function relink(body, from) {
  return body.replace(/\]\(([^)\s]+)(\s+"[^"]*")?\)/g, (all, target, title = '') => {
    const to = resolve(target, from);
    return to === null ? all : `](${to}${title})`;
  });
}

async function markdownUnder(folder) {
  const found = [];
  for (const entry of await fs.readdir(path.join(ROOT, folder), { withFileTypes: true })) {
    const relative = path.posix.join(folder, entry.name);
    if (entry.isDirectory()) found.push(...(await markdownUnder(relative)));
    else if (entry.name.endsWith('.md')) found.push(relative);
  }
  return found;
}

async function main() {
  const missing = (await markdownUnder('docs')).filter((file) => !ROUTES.has(file));
  if (missing.length) {
    throw new Error(`sync-docs: pages of the guide the site doesn't carry — list them in PAGES: ${missing.join(', ')}`);
  }
  await fs.rm(OUT, { recursive: true, force: true });
  await fs.mkdir(OUT, { recursive: true });
  for (const page of PAGES) {
    const raw = await fs.readFile(path.join(ROOT, page.src), 'utf8');
    const { title, body } = titled(raw, page.title);
    const front = `---\ntitle: ${JSON.stringify(title)}\neditUrl: ${JSON.stringify(`${REPOSITORY}/edit/main/${page.src}`)}\n---\n\n`;
    const dest = path.join(OUT, page.dest);
    await fs.mkdir(path.dirname(dest), { recursive: true });
    await fs.writeFile(dest, front + relink(body, page.src).replace(/^\n+/, ''), 'utf8');
  }
  console.log(`sync-docs: ${PAGES.length} pages${warnings ? ` (${warnings} warning(s))` : ''}`);
}

main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
