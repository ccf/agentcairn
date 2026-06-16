# agentcairn Obsidian Plugin — MVP (path 1)

> **Amendment (2026-06-15, post-QA):** the **Memory Graph was cut from the MVP** and deferred. On a real vault, agentcairn's ingested notes carry **zero `[[wikilinks]]`** between each other (verified: 0 of 559 notes), so a link-based graph is empty — colored dots, no structure. The graph returns only once it can draw **meaningful edges**: semantic relatedness (embeddings — "path 2") and/or `superseded_by` lineage. Shipped MVP (plugin `0.2.0`) is therefore **Memory list + provenance + currency**. The graph sections below are retained as the original design record; treat them as **deferred**, not shipped.

**Status:** Approved (2026-06-15); graph deferred post-QA (see amendment)
**Implementation repo:** a **new, separate** repository `ccf/agentcairn-obsidian` (TypeScript). This spec lives in the agentcairn (Python) repo because it depends on that project's **frontmatter contract**; the plugin code, tests, and releases live in the new repo with their own semver and release process.
**Affects:** nothing in `src/cairn` — the plugin is read-only over the Markdown vault and never touches the Python package or the `.duckdb` index.

## Problem

agentcairn's memory is plain Markdown the user already owns, so any Obsidian user can open `~/agentcairn` and read it today. What's missing is an **interactive legibility layer**: a way to *see, filter, and navigate* what the agent remembers — with provenance (project/harness/session), currency (superseded/expired vs current), and the wikilink memory graph made visible — inside Obsidian. This sharpens agentcairn's wedge against competitors that only offer a one-way Obsidian *export* + static viewer: ours is your **live, editable memory, legible in the tool you already trust**.

This is the **v2 "Obsidian plugin surface"** roadmap item, scoped to **path 1**: a vault-native, pure-TypeScript reader/graph/keyword surface. **No in-browser semantic recall** (DuckDB-WASM has no VSS; query-embedding in-browser is a deliberate non-goal here — that's a later "path 2"). The plugin preserves the daemonless / vault-as-truth identity: it reads only the Markdown vault via Obsidian's own APIs and depends on no running Python process and no native/WASM modules.

## Constraints (from the agentcairn design)

- **Vault is the source of truth; the `.duckdb` index is a disposable cache.** The plugin reads the vault, never the index.
- **Daemonless / pure-JS / mobile-capable.** No child-process shell-outs to the `cairn` CLI, no native deps — keeps the community-store review bar low and works on mobile.
- **DuckDB-WASM has no VSS** → do not promise in-browser semantic search.
- **Frontmatter contract** (what the plugin reads) — agentcairn memory notes carry: `type: memory`, `title`, `permalink`, `tags`, `created`, `source` (`memory://session/<id>`), `importance`, and (when present) `valid_from`, `valid_until`, `superseded_by`, `project`, `harness`. Currency semantics mirror the Python `validity_status`.

## Goal / decisions (brainstorm)

- **Path 1 MVP** (reader/graph/keyword), targeting the **Obsidian community plugin store**.
- **Data source: Obsidian's native `metadataCache`** (frontmatter + resolved links + tags) — pure TS, zero native/WASM deps, mobile-capable, vault-as-truth. Not the `.duckdb`.
- **MVP surfaces:** a right-sidebar **Memory view** with a shared filter bar and two modes — **List** and **Graph** — plus an **active-note provenance** section. The custom **Memory Graph** is in the MVP (the user pulled it in), implemented as our own d3-force view (not a hook into Obsidian's native graph, which has no public coloring API).
- **Separate repo** `ccf/agentcairn-obsidian`, independent semver + release process.
- **Graph lib: d3-force** (lightweight; keeps the bundle small for store review) rendered to SVG/Canvas in our own `ItemView`.

## Architecture

Two layers with a clean boundary, so the logic is testable without a running Obsidian.

### A. Data layer (pure TS, no Obsidian-view deps — fully unit-tested)

- **`MemoryNote`** model:
  ```ts
  // string values match the Python `validity_status` exactly (UI may show "not yet valid")
  type Currency = "current" | "superseded" | "expired" | "not_yet_valid";
  interface MemoryNote {
    path: string;            // vault-relative file path (identity)
    title: string;
    project?: string;
    harness?: string;
    session?: string;        // parsed from `source: memory://session/<id>`
    importance?: number;
    created?: string;        // ISO
    tags: string[];
    validFrom?: string;
    validUntil?: string;
    supersededBy?: string;
    currency: Currency;
    links: string[];         // resolved target paths of [[wikilinks]] to other memory notes
  }
  ```
- **`parseMemoryNote(frontmatter, file, resolvedLinks): MemoryNote | null`** — returns `null` unless `frontmatter.type === "memory"`. Extracts fields defensively (missing/odd types → omitted), parses `session` from the `source` string, and keeps only `links` that resolve to other memory notes.
- **`computeCurrency(note, now): Currency`** — mirrors the Python `validity_status` (`src/cairn/temporal.py`) precedence exactly: `supersededBy` set → `superseded`; else `validUntil` present and `now >= validUntil` (half-open: end is **exclusive**) → `expired`; else `validFrom` present and `validFrom > now` → `not_yet_valid`; else `current`. (Agreement with recall's demotion is the point — same labels, same boundary semantics.)
- **`filterNotes(notes, criteria)`** + **`sortNotes(notes, key)`** — pure. `criteria = { query?, project?, harness?, currency?, tag? }`; `query` is case-insensitive substring/token match over `title + tags + path` (honest keyword *filter*, explicitly not BM25/recall). `sortNotes` by `newest` (created desc) or `importance` (desc, undefined last).
- **`buildGraph(notes): { nodes, edges }`** — nodes = the filtered memory notes; edges = `links` between two memory notes in the set (deduped, undirected for layout). Used by the Graph mode.

### B. Obsidian integration (`main.ts` + views)

- **Plugin entry (`main.ts`)**: on load, register the `MemoryView` (`ItemView`, right leaf), register commands ("agentcairn: Open Memory view", "…: Toggle list/graph"), add a settings tab, and subscribe to `app.metadataCache.on("resolved")` and `on("changed")` to rebuild the model (debounced). Reads via `app.vault.getMarkdownFiles()` + `app.metadataCache.getFileCache(file)` + `resolvedLinks`.
- **`MemoryView`** (one `ItemView`, two render modes sharing one filter state):
  - **Filter bar:** search box; `project` / `harness` / `currency` / `tag` dropdowns (populated from the model); `sort` dropdown; a List⇄Graph toggle.
  - **List mode:** one row per note (title, project, created, harness, importance); click → `workspace.openLinkText(path)`; rows get a CSS class per `currency` (superseded/expired dimmed + a small badge).
  - **Graph mode:** a d3-force simulation rendered into the view; nodes colored by `project` (categorical palette, stable hash→color); superseded/expired nodes dimmed and their edges dashed; click node → open note; pan/zoom; nodes filtered out by the bar are hidden. Performance target: smooth at a few thousand nodes (typical vaults are far smaller); cap/aggregate above a threshold with a visible notice.
  - **Active-note provenance:** a compact section that, when the active file is a memory note, shows its `project` / `harness` / `session` / `created` / `importance` and a **currency badge** (current / superseded / expired / future). Updates on `active-leaf-change`.
- **Settings (minimal):** default sort; default mode (list/graph); optional override for the "project" frontmatter key (default `project`). YAGNI — nothing else.
- **Scoping / empty state:** only `type: memory` notes appear; if a vault has none, the view shows a friendly "No agentcairn memories found in this vault" hint (so it degrades gracefully on non-agentcairn vaults).

## Data flow

```
Obsidian metadataCache (frontmatter + resolvedLinks)
  → parseMemoryNote(...) per markdown file  → MemoryNote[]   (rebuilt on metadataCache change, debounced)
  → filterNotes / sortNotes (from the filter bar)
      → List mode: render rows (currency-styled)
      → Graph mode: buildGraph(...) → d3-force → SVG/Canvas (project-colored, currency-styled)
  active-leaf-change → provenance panel for the active memory note
  click → workspace.openLinkText(path)
```

## Error handling

- A file with no/odd frontmatter, or `type !== "memory"` → skipped (returns `null`), never throws.
- Malformed `created`/`valid_*` strings → field omitted; currency falls back to `current` (never crashes the view).
- `source` not matching `memory://session/<id>` → `session` omitted.
- Links that don't resolve to a memory note → dropped from `links` (no dangling graph edges).
- Large vaults → graph caps node count with a notice rather than freezing; the list virtualizes/paginates if needed.
- No agentcairn notes → empty-state hint, not an error.

## Testing / verification

- **vitest unit tests** (the data layer, no Obsidian runtime): `parseMemoryNote` (memory vs non-memory, field extraction, session parsing, defensive omission); `computeCurrency` (each precedence branch + boundary `now`); `filterNotes`/`sortNotes` (query/project/harness/currency/tag, sort orders, undefined handling); `buildGraph` (edges only between in-set memory notes, dedup, unresolved dropped). Fixtures are plain `CachedMetadata`-shaped objects — no headless Obsidian needed.
- **Manual QA** in a dev vault (copy of a real `~/agentcairn`): list filters/sort, currency styling, graph colors/interaction/perf, provenance panel updates, empty-state on a non-agentcairn vault, light/dark themes, and a mobile smoke if feasible.
- **Build:** `esbuild` produces `main.js`; `manifest.json`/`versions.json` validate against Obsidian's schema; the plugin loads in a real Obsidian without console errors.

## Distribution

- **Separate repo** `ccf/agentcairn-obsidian`: `manifest.json` at root (id `agentcairn`, name "agentcairn", `isDesktopOnly: false`, `minAppVersion`), `styles.css`, `versions.json`, esbuild config, vitest, a README, and a GitHub Actions release workflow (on tag `X.Y.Z` → build → create a Release with `main.js` + `manifest.json` + `styles.css` attached). Independent semver — unrelated to the PyPI package version or the Python release ritual.
- **Community-store submission:** a PR to `obsidianmd/obsidian-releases` adding the plugin to `community-plugins.json` (the final human/maintainer step; the repo + a first release must exist first).
- The agentcairn (Python) repo README links to the plugin; the plugin README documents the frontmatter contract it reads and links back to this spec.

## Non-goals

- **Semantic / vector recall in-browser** (path 2 — query-embedding + brute-force cosine in JS; deferred).
- **Reading or depending on the `.duckdb` index** (vault-as-truth; the index is a disposable cache).
- **Capture / reindex / memory-management actions** (no CLI shell-outs, no writes to notes — keeps the plugin pure-JS, mobile-capable, and read-only).
- **Hooking Obsidian's native Graph view** (undocumented internals; we ship our own).
- Changing anything in the agentcairn Python package.

## Open questions

None.

## Note on scope / staging

This MVP is one cohesive spec (shared data layer + filters across all surfaces), but the implementation plan should **stage** it so each stage is independently testable: (1) data layer + model + currency + filter/sort (unit-tested core); (2) Memory view List mode + provenance panel + currency styling; (3) Memory Graph mode (d3-force); (4) build/manifest/release workflow + store-submission prep. Stage 1 is the testable foundation the three surfaces build on.
