# agentcairn Obsidian Plugin MVP — Implementation Plan

> **Amendment (2026-06-15):** executed through Task 6 and shipped (plugin `0.2.0`), **except the Memory Graph (Task 5), which was cut post-QA** — agentcairn notes don't `[[wikilink]]` each other, so the graph was edge-empty on real vaults. Deferred to a future semantic-edges version. Tasks 1–4 + 6 shipped (list + provenance + currency + release plumbing).

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A vault-native, pure-TypeScript Obsidian plugin that surfaces agentcairn memory — a filterable **Memory list**, an **active-note provenance** panel, **currency** styling, and a custom **Memory Graph** — read entirely from Obsidian's `metadataCache`.

**Architecture:** Two layers. A pure **data layer** (Obsidian-independent: `MemoryNote` model + `parseMemoryNote`/`computeCurrency`/`filterNotes`/`sortNotes`/`buildGraph`) that is fully unit-tested with vitest, and an **Obsidian integration** layer (`Plugin` + one `ItemView` with List/Graph modes + provenance panel) that adapts `metadataCache` into the data layer and renders it. d3-force draws the graph.

**Tech Stack:** TypeScript, Obsidian plugin API, esbuild (bundle → `main.js`), vitest (data-layer tests), d3-force (graph), in a **new repo `ccf/agentcairn-obsidian`** with its own semver + GitHub-release workflow.

**Reference:** Spec `docs/specs/2026-06-15-obsidian-plugin-mvp-design.md` (in the agentcairn Python repo). The frontmatter contract and the `validity_status` precedence the plugin mirrors are defined there.

**Working directory:** Tasks 2+ run in the **new repo** (`~/git/agentcairn-obsidian`), created by Task 1. The data layer is Obsidian-independent and developed test-first; the views are manual-QA (no headless Obsidian).

**Frontmatter contract (read-only):** `type: memory`, `title`, `permalink`, `tags`, `created`, `source` (`memory://session/<id>`), `importance`, `valid_from`, `valid_until`, `superseded_by`, `project`, `harness`. Currency precedence (mirror of `src/cairn/temporal.py::validity_status`): `superseded_by` → `superseded`; else `valid_until` present and `now >= valid_until` (end-exclusive) → `expired`; else `valid_from` present and `valid_from > now` → `not_yet_valid`; else `current`.

---

## File Structure (new repo `agentcairn-obsidian`)

| File | Responsibility |
|---|---|
| `manifest.json`, `versions.json` | Obsidian plugin manifest (id `agentcairn`, `isDesktopOnly: false`) + version map |
| `package.json`, `tsconfig.json`, `esbuild.config.mjs`, `vitest.config.ts` | build + test tooling |
| `src/model.ts` | `MemoryNote`, `Currency`, `parseMemoryNote`, `computeCurrency` (pure) |
| `src/query.ts` | `filterNotes`, `sortNotes`, `buildGraph` (pure) |
| `src/main.ts` | `Plugin` entry: register view + commands + settings; adapt `metadataCache` → `MemoryNote[]` |
| `src/view.ts` | `MemoryView` (`ItemView`): filter bar, List mode, provenance panel |
| `src/graph.ts` | d3-force Memory Graph rendering (called by `view.ts` in Graph mode) |
| `styles.css` | currency styling, layout |
| `tests/model.test.ts`, `tests/query.test.ts` | vitest unit tests for the pure layer |
| `.github/workflows/release.yml` | tag → build → GitHub Release with `main.js`+`manifest.json`+`styles.css` |
| `README.md` | usage + the frontmatter contract + link back to the spec |

---

## Task 1: Scaffold the new repo + tooling

**Files:** create the repo and all tooling files.

- [ ] **Step 1: Create the repo locally and on GitHub**

```bash
mkdir -p ~/git/agentcairn-obsidian && cd ~/git/agentcairn-obsidian
git init
gh repo create ccf/agentcairn-obsidian --public --source=. --description "Obsidian plugin: see, filter, and navigate your agentcairn agent-memory vault" || true
```

- [ ] **Step 2: Write `package.json`**

```json
{
  "name": "agentcairn-obsidian",
  "version": "0.1.0",
  "description": "See, filter, and navigate your agentcairn agent-memory vault in Obsidian.",
  "main": "main.js",
  "scripts": {
    "dev": "node esbuild.config.mjs",
    "build": "tsc -noEmit -skipLibCheck && node esbuild.config.mjs production",
    "test": "vitest run",
    "test:watch": "vitest"
  },
  "keywords": ["obsidian", "agentcairn", "memory"],
  "license": "Apache-2.0",
  "devDependencies": {
    "@types/node": "^20",
    "builtin-modules": "^3.3.0",
    "esbuild": "^0.21.0",
    "obsidian": "latest",
    "typescript": "^5.4.0",
    "vitest": "^1.6.0"
  },
  "dependencies": {
    "d3-force": "^3.0.0",
    "@types/d3-force": "^3.0.10"
  }
}
```

- [ ] **Step 3: Write `tsconfig.json`**

```json
{
  "compilerOptions": {
    "baseUrl": ".",
    "module": "ESNext",
    "target": "ES2018",
    "moduleResolution": "node",
    "lib": ["DOM", "ES2018"],
    "strict": true,
    "noImplicitAny": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts", "tests/**/*.ts"]
}
```

- [ ] **Step 4: Write `esbuild.config.mjs`** (standard Obsidian bundling)

```js
import esbuild from "esbuild";
import process from "process";
import builtins from "builtin-modules";

const production = process.argv[2] === "production";

const ctx = await esbuild.context({
  entryPoints: ["src/main.ts"],
  bundle: true,
  external: ["obsidian", "electron", ...builtins],
  format: "cjs",
  target: "es2018",
  sourcemap: production ? false : "inline",
  treeShaking: true,
  outfile: "main.js",
  logLevel: "info",
});

if (production) {
  await ctx.rebuild();
  process.exit(0);
} else {
  await ctx.watch();
}
```

- [ ] **Step 5: Write `manifest.json` + `versions.json`**

`manifest.json`:
```json
{
  "id": "agentcairn",
  "name": "agentcairn",
  "version": "0.1.0",
  "minAppVersion": "1.5.0",
  "description": "See, filter, and navigate your agentcairn agent-memory vault: provenance, currency, and a memory graph.",
  "author": "Charles C. Figueiredo",
  "authorUrl": "https://github.com/ccf/agentcairn",
  "isDesktopOnly": false
}
```
`versions.json`:
```json
{ "0.1.0": "1.5.0" }
```

- [ ] **Step 6: Write `vitest.config.ts`**

```ts
import { defineConfig } from "vitest/config";
export default defineConfig({ test: { environment: "node", include: ["tests/**/*.test.ts"] } });
```

- [ ] **Step 7: Install + verify the toolchain**

```bash
cd ~/git/agentcairn-obsidian && npm install
npx vitest run   # expected: "No test files found" (exit 1 is fine here) — confirms vitest resolves
```
Add a `.gitignore` (`node_modules/`, `main.js`, `*.js.map`).

- [ ] **Step 8: Commit**

```bash
git add -A && git commit -m "chore: scaffold agentcairn-obsidian plugin (manifest, esbuild, vitest, ts)"
```

---

## Task 2: Data model — `MemoryNote`, `parseMemoryNote`, `computeCurrency`

**Files:** Create `src/model.ts`; Test `tests/model.test.ts`.

The pure layer takes plain frontmatter objects (no `obsidian` import) so it's testable in node.

- [ ] **Step 1: Write the failing tests** (`tests/model.test.ts`)

```ts
import { describe, it, expect } from "vitest";
import { parseMemoryNote, computeCurrency } from "../src/model";

const NOW = new Date("2026-06-15T00:00:00Z");

describe("parseMemoryNote", () => {
  it("returns null for non-memory notes", () => {
    expect(parseMemoryNote({ type: "note" }, "a.md", "A", [], NOW)).toBeNull();
    expect(parseMemoryNote({}, "a.md", "A", [], NOW)).toBeNull();
  });

  it("extracts fields and parses session from source", () => {
    const fm = {
      type: "memory", title: "Rotate key", project: "agentcairn", harness: "codex",
      source: "memory://session/sess-9", importance: 0.81, created: "2026-06-14T00:00:00Z",
      tags: ["ingested"],
    };
    const n = parseMemoryNote(fm, "memories/x.md", "Rotate key", ["memories/y.md"], NOW);
    expect(n).not.toBeNull();
    expect(n!.project).toBe("agentcairn");
    expect(n!.harness).toBe("codex");
    expect(n!.session).toBe("sess-9");
    expect(n!.importance).toBe(0.81);
    expect(n!.links).toEqual(["memories/y.md"]);
    expect(n!.currency).toBe("current");
  });

  it("omits odd-typed fields without throwing", () => {
    const n = parseMemoryNote(
      { type: "memory", importance: "high", created: 123, project: 7 }, "a.md", "A", [], NOW
    );
    expect(n!.importance).toBeUndefined();
    expect(n!.created).toBeUndefined();
    expect(n!.project).toBeUndefined();
  });
});

describe("computeCurrency", () => {
  it("superseded wins over everything", () => {
    expect(computeCurrency({ supersededBy: "z", validUntil: "2099-01-01T00:00:00Z" }, NOW)).toBe("superseded");
  });
  it("expired when now >= valid_until (end-exclusive)", () => {
    expect(computeCurrency({ validUntil: "2026-06-15T00:00:00Z" }, NOW)).toBe("expired"); // now == until → expired
    expect(computeCurrency({ validUntil: "2026-06-16T00:00:00Z" }, NOW)).toBe("current");
  });
  it("not_yet_valid when valid_from > now", () => {
    expect(computeCurrency({ validFrom: "2026-06-16T00:00:00Z" }, NOW)).toBe("not_yet_valid");
  });
  it("current otherwise", () => {
    expect(computeCurrency({}, NOW)).toBe("current");
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `npx vitest run tests/model.test.ts`
Expected: FAIL — `src/model` not found.

- [ ] **Step 3: Implement `src/model.ts`**

```ts
// string values match the Python validity_status exactly
export type Currency = "current" | "superseded" | "expired" | "not_yet_valid";

export interface MemoryNote {
  path: string;
  title: string;
  project?: string;
  harness?: string;
  session?: string;
  importance?: number;
  created?: string;
  tags: string[];
  validFrom?: string;
  validUntil?: string;
  supersededBy?: string;
  currency: Currency;
  links: string[];
}

const str = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);
const num = (v: unknown): number | undefined => (typeof v === "number" && !Number.isNaN(v) ? v : undefined);

function parseDate(s: string | undefined): Date | undefined {
  if (!s) return undefined;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? undefined : d;
}

export function computeCurrency(
  n: { validFrom?: string; validUntil?: string; supersededBy?: string },
  now: Date
): Currency {
  if (n.supersededBy) return "superseded";
  const vu = parseDate(n.validUntil);
  if (vu && now.getTime() >= vu.getTime()) return "expired"; // end-exclusive
  const vf = parseDate(n.validFrom);
  if (vf && vf.getTime() > now.getTime()) return "not_yet_valid";
  return "current";
}

export function parseMemoryNote(
  fm: Record<string, unknown>,
  path: string,
  title: string,
  linkTargets: string[],
  now: Date
): MemoryNote | null {
  if (fm?.type !== "memory") return null;
  const tags = Array.isArray(fm.tags) ? fm.tags.filter((t): t is string => typeof t === "string") : [];
  const source = str(fm.source);
  const m = source?.match(/^memory:\/\/session\/(.+)$/);
  const base = {
    validFrom: str(fm.valid_from),
    validUntil: str(fm.valid_until),
    supersededBy: str(fm.superseded_by),
  };
  return {
    path,
    title: str(fm.title) ?? title,
    project: str(fm.project),
    harness: str(fm.harness),
    session: m ? m[1] : undefined,
    importance: num(fm.importance),
    created: str(fm.created),
    tags,
    ...base,
    currency: computeCurrency(base, now),
    links: linkTargets,
  };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npx vitest run tests/model.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/model.ts tests/model.test.ts
git commit -m "feat: MemoryNote model, parseMemoryNote, computeCurrency (mirrors validity_status)"
```

---

## Task 3: Query layer — `filterNotes`, `sortNotes`, `buildGraph`

**Files:** Create `src/query.ts`; Test `tests/query.test.ts`.

- [ ] **Step 1: Write the failing tests** (`tests/query.test.ts`)

```ts
import { describe, it, expect } from "vitest";
import { filterNotes, sortNotes, buildGraph } from "../src/query";
import type { MemoryNote } from "../src/model";

const mk = (p: Partial<MemoryNote>): MemoryNote => ({
  path: p.path ?? "x.md", title: p.title ?? "T", tags: p.tags ?? [],
  currency: p.currency ?? "current", links: p.links ?? [], ...p,
});

describe("filterNotes", () => {
  const notes = [
    mk({ path: "a.md", title: "Rotate jwt key", project: "agentcairn", harness: "codex", tags: ["auth"] }),
    mk({ path: "b.md", title: "DuckDB gotcha", project: "other", harness: "claude-code", currency: "superseded" }),
  ];
  it("keyword matches title/tags/path (case-insensitive)", () => {
    expect(filterNotes(notes, { query: "jwt" }).map(n => n.path)).toEqual(["a.md"]);
    expect(filterNotes(notes, { query: "AUTH" }).map(n => n.path)).toEqual(["a.md"]);
  });
  it("filters by project/harness/currency/tag", () => {
    expect(filterNotes(notes, { project: "other" }).map(n => n.path)).toEqual(["b.md"]);
    expect(filterNotes(notes, { currency: "superseded" }).map(n => n.path)).toEqual(["b.md"]);
    expect(filterNotes(notes, { harness: "codex" }).map(n => n.path)).toEqual(["a.md"]);
    expect(filterNotes(notes, { tag: "auth" }).map(n => n.path)).toEqual(["a.md"]);
  });
});

describe("sortNotes", () => {
  const notes = [
    mk({ path: "a.md", created: "2026-06-10T00:00:00Z", importance: 0.2 }),
    mk({ path: "b.md", created: "2026-06-14T00:00:00Z", importance: 0.9 }),
    mk({ path: "c.md" }),
  ];
  it("newest puts later created first, undefined last", () => {
    expect(sortNotes(notes, "newest").map(n => n.path)).toEqual(["b.md", "a.md", "c.md"]);
  });
  it("importance desc, undefined last", () => {
    expect(sortNotes(notes, "importance").map(n => n.path)).toEqual(["b.md", "a.md", "c.md"]);
  });
});

describe("buildGraph", () => {
  it("keeps only edges between in-set memory notes; drops unresolved", () => {
    const notes = [mk({ path: "a.md", links: ["b.md", "ghost.md"] }), mk({ path: "b.md", links: [] })];
    const g = buildGraph(notes);
    expect(g.nodes.map(n => n.path).sort()).toEqual(["a.md", "b.md"]);
    expect(g.edges).toEqual([{ source: "a.md", target: "b.md" }]);
  });
});
```

- [ ] **Step 2: Run to verify fail**

Run: `npx vitest run tests/query.test.ts`
Expected: FAIL — `src/query` not found.

- [ ] **Step 3: Implement `src/query.ts`**

```ts
import type { Currency, MemoryNote } from "./model";

export interface FilterCriteria {
  query?: string;
  project?: string;
  harness?: string;
  currency?: Currency;
  tag?: string;
}

export function filterNotes(notes: MemoryNote[], c: FilterCriteria): MemoryNote[] {
  const q = c.query?.trim().toLowerCase();
  return notes.filter((n) => {
    if (c.project && n.project !== c.project) return false;
    if (c.harness && n.harness !== c.harness) return false;
    if (c.currency && n.currency !== c.currency) return false;
    if (c.tag && !n.tags.includes(c.tag)) return false;
    if (q) {
      const hay = `${n.title} ${n.tags.join(" ")} ${n.path}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export type SortKey = "newest" | "importance";

export function sortNotes(notes: MemoryNote[], key: SortKey): MemoryNote[] {
  const copy = [...notes];
  if (key === "newest") {
    copy.sort((a, b) => (b.created ? Date.parse(b.created) : -Infinity) - (a.created ? Date.parse(a.created) : -Infinity));
  } else {
    copy.sort((a, b) => (b.importance ?? -Infinity) - (a.importance ?? -Infinity));
  }
  return copy;
}

export interface GraphEdge { source: string; target: string; }
export interface Graph { nodes: MemoryNote[]; edges: GraphEdge[]; }

export function buildGraph(notes: MemoryNote[]): Graph {
  const inSet = new Set(notes.map((n) => n.path));
  const seen = new Set<string>();
  const edges: GraphEdge[] = [];
  for (const n of notes) {
    for (const t of n.links) {
      if (!inSet.has(t)) continue;
      const key = [n.path, t].sort().join(" ");
      if (seen.has(key)) continue;
      seen.add(key);
      edges.push({ source: n.path, target: t });
    }
  }
  return { nodes: notes, edges };
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npx vitest run`
Expected: PASS (model + query suites).

- [ ] **Step 5: Commit**

```bash
git add src/query.ts tests/query.test.ts
git commit -m "feat: pure filter/sort/graph query layer with tests"
```

---

## Task 4: Plugin entry + Memory view (List mode + provenance panel)

**Files:** Create `src/main.ts`, `src/view.ts`, `styles.css`. (No unit tests — Obsidian runtime; manual QA in Step 6.)

- [ ] **Step 1: Implement the `metadataCache` → `MemoryNote[]` adapter + plugin entry (`src/main.ts`)**

```ts
import { Plugin, TFile, WorkspaceLeaf } from "obsidian";
import { MemoryView, VIEW_TYPE_MEMORY } from "./view";
import { parseMemoryNote, MemoryNote } from "./model";

export default class AgentcairnPlugin extends Plugin {
  async onload() {
    this.registerView(VIEW_TYPE_MEMORY, (leaf) => new MemoryView(leaf, this));
    this.addRibbonIcon("brain", "agentcairn memory", () => this.activateView());
    this.addCommand({ id: "open-memory-view", name: "Open Memory view", callback: () => this.activateView() });
    // Rebuild on vault/metadata changes (debounced inside the view).
    this.registerEvent(this.app.metadataCache.on("resolved", () => this.refreshViews()));
    this.registerEvent(this.app.metadataCache.on("changed", () => this.refreshViews()));
  }

  onunload() {}

  /** Build the MemoryNote[] model from the live metadataCache. */
  buildModel(now: Date = new Date()): MemoryNote[] {
    const resolved = this.app.metadataCache.resolvedLinks; // { srcPath: { destPath: count } }
    const out: MemoryNote[] = [];
    for (const file of this.app.vault.getMarkdownFiles()) {
      const cache = this.app.metadataCache.getFileCache(file);
      const fm = (cache?.frontmatter ?? {}) as Record<string, unknown>;
      const linkTargets = Object.keys(resolved[file.path] ?? {});
      const note = parseMemoryNote(fm, file.path, file.basename, linkTargets, now);
      if (note) out.push(note);
    }
    return out;
  }

  async activateView() {
    const { workspace } = this.app;
    let leaf = workspace.getLeavesOfType(VIEW_TYPE_MEMORY)[0];
    if (!leaf) {
      leaf = workspace.getRightLeaf(false)!;
      await leaf.setViewState({ type: VIEW_TYPE_MEMORY, active: true });
    }
    workspace.revealLeaf(leaf);
  }

  refreshViews() {
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_MEMORY)) {
      (leaf.view as MemoryView).scheduleRender();
    }
  }

  openNote(path: string) {
    const file = this.app.vault.getAbstractFileByPath(path);
    if (file instanceof TFile) this.app.workspace.getLeaf(false).openFile(file);
  }
}
```

- [ ] **Step 2: Implement `MemoryView` with the filter bar, List mode, provenance panel (`src/view.ts`)**

```ts
import { ItemView, WorkspaceLeaf } from "obsidian";
import type AgentcairnPlugin from "./main";
import { filterNotes, sortNotes, SortKey, FilterCriteria } from "./query";
import { MemoryNote, Currency } from "./model";
import { renderGraph } from "./graph";

export const VIEW_TYPE_MEMORY = "agentcairn-memory";

export class MemoryView extends ItemView {
  plugin: AgentcairnPlugin;
  mode: "list" | "graph" = "list";
  criteria: FilterCriteria = {};
  sort: SortKey = "newest";
  private timer: number | null = null;

  constructor(leaf: WorkspaceLeaf, plugin: AgentcairnPlugin) {
    super(leaf);
    this.plugin = plugin;
  }
  getViewType() { return VIEW_TYPE_MEMORY; }
  getDisplayText() { return "agentcairn memory"; }
  getIcon() { return "brain"; }

  async onOpen() {
    this.registerEvent(this.app.workspace.on("active-leaf-change", () => this.renderProvenance()));
    this.render();
  }
  async onClose() {}

  scheduleRender() {
    if (this.timer) window.clearTimeout(this.timer);
    this.timer = window.setTimeout(() => this.render(), 200);
  }

  render() {
    const root = this.containerEl.children[1] as HTMLElement;
    root.empty();
    root.addClass("agentcairn-memory");
    const all = this.plugin.buildModel();
    this.renderFilterBar(root, all);
    const shown = sortNotes(filterNotes(all, this.criteria), this.sort);
    const body = root.createDiv({ cls: "ac-body" });
    if (all.length === 0) {
      body.createDiv({ cls: "ac-empty", text: "No agentcairn memories found in this vault." });
    } else if (this.mode === "list") {
      this.renderList(body, shown);
    } else {
      renderGraph(body, shown, (path) => this.plugin.openNote(path));
    }
    this.renderProvenance(root);
  }

  private renderFilterBar(root: HTMLElement, all: MemoryNote[]) {
    const bar = root.createDiv({ cls: "ac-filter" });
    const search = bar.createEl("input", { attr: { type: "text", placeholder: "search…" } });
    search.value = this.criteria.query ?? "";
    search.oninput = () => { this.criteria.query = search.value; this.scheduleRender(); };
    const uniq = (f: (n: MemoryNote) => string | undefined) =>
      [...new Set(all.map(f).filter((v): v is string => !!v))].sort();
    this.dropdown(bar, "project", uniq((n) => n.project));
    this.dropdown(bar, "harness", uniq((n) => n.harness));
    this.dropdown(bar, "currency", ["current", "superseded", "expired", "not_yet_valid"]);
    const sortSel = bar.createEl("select");
    for (const k of ["newest", "importance"]) sortSel.createEl("option", { value: k, text: k });
    sortSel.value = this.sort;
    sortSel.onchange = () => { this.sort = sortSel.value as SortKey; this.scheduleRender(); };
    const toggle = bar.createEl("button", { text: this.mode === "list" ? "Graph" : "List" });
    toggle.onclick = () => { this.mode = this.mode === "list" ? "graph" : "list"; this.render(); };
  }

  private dropdown(bar: HTMLElement, key: keyof FilterCriteria, opts: string[]) {
    const sel = bar.createEl("select");
    sel.createEl("option", { value: "", text: `${key}: all` });
    for (const o of opts) sel.createEl("option", { value: o, text: o });
    sel.value = (this.criteria[key] as string) ?? "";
    sel.onchange = () => { (this.criteria as any)[key] = sel.value || undefined; this.scheduleRender(); };
  }

  private renderList(body: HTMLElement, notes: MemoryNote[]) {
    const list = body.createDiv({ cls: "ac-list" });
    for (const n of notes) {
      const row = list.createDiv({ cls: `ac-row ac-${n.currency}` });
      row.onclick = () => this.plugin.openNote(n.path);
      row.createDiv({ cls: "ac-title", text: n.title });
      const meta = [n.created?.slice(0, 10), n.harness, n.project, n.importance != null ? `imp ${n.importance}` : null]
        .filter(Boolean).join(" · ");
      row.createDiv({ cls: "ac-meta", text: meta });
      if (n.currency !== "current") row.createSpan({ cls: "ac-badge", text: n.currency });
    }
  }

  renderProvenance(root?: HTMLElement) {
    const host = (root ?? (this.containerEl.children[1] as HTMLElement));
    host.querySelector(".ac-prov")?.remove();
    const file = this.app.workspace.getActiveFile();
    if (!file) return;
    const note = this.plugin.buildModel().find((n) => n.path === file.path);
    if (!note) return;
    const p = host.createDiv({ cls: "ac-prov" });
    p.createDiv({ cls: "ac-prov-h", text: "active note" });
    p.createDiv({ text: [note.project, note.harness, note.session && `session ${note.session}`]
      .filter(Boolean).join(" · ") });
    p.createSpan({ cls: `ac-badge ac-${note.currency}`, text: note.currency });
  }
}
```

- [ ] **Step 3: Write `styles.css`**

```css
.agentcairn-memory .ac-filter { display: flex; flex-wrap: wrap; gap: 4px; padding: 6px; }
.agentcairn-memory .ac-row { padding: 6px 8px; border-bottom: 1px solid var(--background-modifier-border); cursor: pointer; }
.agentcairn-memory .ac-row:hover { background: var(--background-modifier-hover); }
.agentcairn-memory .ac-title { font-weight: 600; }
.agentcairn-memory .ac-meta { font-size: 0.8em; color: var(--text-muted); }
.agentcairn-memory .ac-superseded, .agentcairn-memory .ac-expired { opacity: 0.5; }
.agentcairn-memory .ac-badge { font-size: 0.7em; padding: 0 4px; border-radius: 4px; background: var(--background-modifier-border); margin-left: 4px; }
.agentcairn-memory .ac-prov { margin-top: 8px; padding: 8px; border-top: 1px solid var(--background-modifier-border); font-size: 0.85em; }
.agentcairn-memory .ac-empty { padding: 16px; color: var(--text-muted); }
.agentcairn-memory .ac-graph { width: 100%; height: 100%; }
```

- [ ] **Step 4: Add a temporary no-op `src/graph.ts`** so `view.ts` compiles before Task 5:

```ts
import type { MemoryNote } from "./model";
export function renderGraph(host: HTMLElement, notes: MemoryNote[], onClick: (path: string) => void): void {
  host.createDiv({ cls: "ac-empty", text: `Graph: ${notes.length} notes (coming in the next step).` });
}
```

- [ ] **Step 5: Build**

Run: `cd ~/git/agentcairn-obsidian && npm run build`
Expected: `tsc` typechecks clean and `main.js` is produced. Fix any type errors (Obsidian API types come from the `obsidian` dev dep).

- [ ] **Step 6: Manual QA in a dev vault**

Symlink the plugin into a test vault and load it:
```bash
# Use a COPY of a real agentcairn vault as the test vault to avoid edits to the real one.
mkdir -p ~/acn-testvault/.obsidian/plugins/agentcairn
cp main.js manifest.json styles.css ~/acn-testvault/.obsidian/plugins/agentcairn/
# also copy some real memory notes into ~/acn-testvault/memories/ (read-only usage; the plugin never writes)
```
Open `~/acn-testvault` in Obsidian, enable the plugin (Settings → Community plugins → toggle), open the Memory view via the ribbon/command. Verify: rows list memory notes; search + project/harness/currency filters + sort work; superseded/expired rows are dimmed + badged; clicking a row opens the note; the provenance panel shows the active note's origin + currency; a vault with no `type: memory` notes shows the empty state. Note any issues and fix.

- [ ] **Step 7: Commit**

```bash
git add src/main.ts src/view.ts src/graph.ts styles.css
git commit -m "feat: Memory view — list mode, filter bar, provenance panel, currency styling"
```

---

## Task 5: Memory Graph (d3-force)

**Files:** Replace `src/graph.ts` with the real d3-force renderer.

- [ ] **Step 1: Implement `src/graph.ts`**

```ts
import { forceSimulation, forceLink, forceManyBody, forceCenter, Simulation } from "d3-force";
import { buildGraph, GraphEdge } from "./query";
import type { MemoryNote } from "./model";

// Stable categorical color from a string (project) → HSL.
function colorFor(key: string | undefined): string {
  if (!key) return "var(--text-muted)";
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) % 360;
  return `hsl(${h}, 65%, 55%)`;
}

interface Node { path: string; note: MemoryNote; x?: number; y?: number; }

export function renderGraph(host: HTMLElement, notes: MemoryNote[], onClick: (path: string) => void): void {
  host.empty();
  const MAX = 2000;
  const capped = notes.length > MAX;
  const used = capped ? notes.slice(0, MAX) : notes;
  if (capped) host.createDiv({ cls: "ac-empty", text: `Showing first ${MAX} of ${notes.length} notes.` });

  const { edges } = buildGraph(used);
  const nodes: Node[] = used.map((n) => ({ path: n.path, note: n }));
  const byPath = new Map(nodes.map((n) => [n.path, n]));
  const links = edges.map((e: GraphEdge) => ({ source: byPath.get(e.source)!, target: byPath.get(e.target)! }));

  const svg = host.createSvg("svg", { cls: "ac-graph" });
  const w = host.clientWidth || 600, h = host.clientHeight || 400;
  svg.setAttr("viewBox", `0 0 ${w} ${h}`);
  const gLinks = svg.createSvg("g");
  const gNodes = svg.createSvg("g");

  const lineEls = links.map((l) => {
    const ln = gLinks.createSvg("line");
    ln.setAttr("stroke", "var(--background-modifier-border)");
    if (l.source.note.currency !== "current" || l.target.note.currency !== "current") ln.setAttr("stroke-dasharray", "3,3");
    return ln;
  });
  const circleEls = nodes.map((n) => {
    const c = gNodes.createSvg("circle");
    c.setAttr("r", "6");
    c.setAttr("fill", colorFor(n.note.project));
    if (n.note.currency !== "current") c.setAttr("opacity", "0.45");
    c.addClass("ac-node");
    (c as SVGElement).addEventListener("click", () => onClick(n.path));
    (c as SVGElement).appendChild(document.createElementNS("http://www.w3.org/2000/svg", "title"))
      .textContent = `${n.note.title}${n.note.project ? ` · ${n.note.project}` : ""}`;
    return c;
  });

  const sim: Simulation<Node, undefined> = forceSimulation(nodes)
    .force("link", forceLink(links as any).distance(40))
    .force("charge", forceManyBody().strength(-80))
    .force("center", forceCenter(w / 2, h / 2));

  sim.on("tick", () => {
    links.forEach((l, i) => {
      lineEls[i].setAttr("x1", String(l.source.x)); lineEls[i].setAttr("y1", String(l.source.y));
      lineEls[i].setAttr("x2", String(l.target.x)); lineEls[i].setAttr("y2", String(l.target.y));
    });
    nodes.forEach((n, i) => { circleEls[i].setAttr("cx", String(n.x)); circleEls[i].setAttr("cy", String(n.y)); });
  });

  // Stop the simulation when the view is torn down (host emptied on re-render).
  const stop = () => sim.stop();
  host.addEventListener("ac-detach", stop);
}
```
(If `createSvg`/`setAttr` helper signatures differ in the installed Obsidian typings, use `document.createElementNS`/`setAttribute` directly — keep behavior identical. Pan/zoom can be a follow-up; the MVP renders a centered force layout with click-to-open.)

- [ ] **Step 2: Build + typecheck**

Run: `cd ~/git/agentcairn-obsidian && npm run build`
Expected: clean. Resolve any d3-force typing issues (the `@types/d3-force` dep provides them; the `links as any` cast bridges d3's in-place mutation of link endpoints).

- [ ] **Step 3: Manual QA (graph)**

Reload the plugin in the dev vault (rebuild + copy `main.js`, then re-toggle the plugin or reload Obsidian). Switch to Graph mode: verify nodes are colored by project, edges connect linked memory notes, superseded/expired nodes are dimmed and their edges dashed, clicking a node opens the note, and the filter bar still narrows the graph. Test on a larger copied vault for responsiveness; confirm the >2000 cap notice appears if applicable.

- [ ] **Step 4: Commit**

```bash
git add src/graph.ts
git commit -m "feat: Memory Graph — d3-force, project-colored, currency-aware"
```

---

## Task 6: Release workflow + README + store-submission prep

**Files:** `.github/workflows/release.yml`, `README.md`.

- [ ] **Step 1: Write `.github/workflows/release.yml`**

```yaml
name: release
on:
  push:
    tags: ["*.*.*"]
permissions:
  contents: write
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20" }
      - run: npm ci
      - run: npm test
      - run: npm run build
      - name: Create release with plugin assets
        env: { GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }} }
        run: gh release create "$GITHUB_REF_NAME" main.js manifest.json styles.css --title "$GITHUB_REF_NAME" --generate-notes
```
(The community store requires the release tag to exactly equal the `manifest.json` version, with `main.js`/`manifest.json`/`styles.css` attached at the release root — this workflow does that.)

- [ ] **Step 2: Write `README.md`**

Cover: what the plugin does (see/filter/navigate agentcairn memory; provenance; currency; memory graph), that it is **read-only** and **vault-native** (reads Obsidian's metadata, never the `.duckdb`, never writes), the **frontmatter contract** it reads (link to the agentcairn spec `docs/specs/2026-06-15-obsidian-plugin-mvp-design.md`), install (community store once accepted; manual install via release assets meanwhile), and that semantic recall is intentionally not in-browser (it lives in the `cairn` CLI/MCP). Link back to the main agentcairn repo.

- [ ] **Step 3: Tag a first release to exercise the workflow**

```bash
cd ~/git/agentcairn-obsidian
git add .github/workflows/release.yml README.md && git commit -m "chore: release workflow + README"
git push -u origin main
git tag 0.1.0 && git push origin 0.1.0
```
Verify the Actions run succeeds and the GitHub Release has `main.js` + `manifest.json` + `styles.css` attached.

- [ ] **Step 4: Prepare the community-store submission (do NOT auto-submit)**

Document the exact submission step in the README / an `obsidian-submission.md` note: a PR to `obsidianmd/obsidian-releases` adding an entry to `community-plugins.json`:
```json
{ "id": "agentcairn", "name": "agentcairn", "author": "Charles C. Figueiredo",
  "description": "See, filter, and navigate your agentcairn agent-memory vault.",
  "repo": "ccf/agentcairn-obsidian" }
```
Leave the actual PR for the user (it's the human/maintainer gate and subject to Obsidian's review checklist).

- [ ] **Step 5: Commit any submission notes**

```bash
git add -A && git commit -m "docs: community-store submission notes" || true
```

---

## Final verification

- [ ] `cd ~/git/agentcairn-obsidian && npm test` green (model + query suites).
- [ ] `npm run build` typechecks clean and emits `main.js`.
- [ ] Manual QA passed for List, provenance, currency styling, and Graph in a copied dev vault; empty-state verified on a non-agentcairn vault.
- [ ] First GitHub Release built by CI with the three assets attached.
- [ ] In the **agentcairn (Python) repo**, add a README link to the plugin repo (separate small PR there).

---

## Self-Review (completed during planning)

- **Spec coverage:** data layer (model+currency+filter/sort+graph) → Tasks 2-3; List + provenance + currency styling → Task 4; Memory Graph (d3-force, project color, currency-aware) → Task 5; vault-native via `metadataCache` (no `.duckdb`) → Task 4 adapter; separate repo + own release/semver + store prep → Tasks 1, 6; non-goals (no semantic, no writes, no native-graph hook) honored — no task adds them. Currency precedence matches `validity_status` (end-exclusive) in Task 2 with boundary tests.
- **Type consistency:** `MemoryNote`/`Currency` (Task 2) consumed by `query.ts` (Task 3), `view.ts` (Task 4), `graph.ts` (Task 5). `parseMemoryNote(fm, path, title, linkTargets, now)`, `computeCurrency({validFrom,validUntil,supersededBy}, now)`, `filterNotes(notes, criteria)`, `sortNotes(notes, key)`, `buildGraph(notes)→{nodes,edges}` are used with matching signatures across tasks. `VIEW_TYPE_MEMORY` shared by `main.ts`/`view.ts`. `renderGraph(host, notes, onClick)` stubbed in Task 4, implemented in Task 5 with the same signature.
- **Placeholder scan:** no TBD/TODO; full file contents for tooling + pure layer + views; the only deliberately-deferred niceties (graph pan/zoom) are named as follow-ups, not left as gaps. Manual-QA steps are explicit because Obsidian views aren't unit-testable headlessly.
- **Stack note:** this runs in a NEW repo with a JS/TS toolchain — subagents executing it must `cd ~/git/agentcairn-obsidian` and use `npm`/`npx`, not `uv`.
