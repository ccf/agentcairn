# Website SEO — Phase 1 (technical foundation)

**Status:** Approved (2026-06-16)
**Affects:** `website/` only — `astro.config.mjs` (sitemap integration), `website/package.json` (dep), `website/public/robots.txt` (new), `website/src/layouts/Base.astro` (head completion + JSON-LD), `website/src/lib/content.ts` (a small SEO/site-metadata addition), tests. No change to `src/cairn` or any other surface.

## Problem

`agentcairn.dev` is a fast, crawlable Astro static site, but its technical SEO is incomplete: **no sitemap, no robots.txt, no canonical, incomplete Open Graph/Twitter cards, and no structured data.** That limits how fully Google indexes the site, blocks rich-result eligibility, and weakens link-preview CTR. This phase fixes the **technical foundation** so the site is fully indexable and rich-result-eligible. Content depth (more pages) and off-page/outreach are out of scope (Phase 2/3).

## Current state (audited 2026-06-16)

- Astro `output: "static"`, `site: "https://agentcairn.dev"`, served by Cloudflare Workers static assets. One page (`src/pages/index.astro`) using `src/layouts/Base.astro`.
- `Base.astro` head has: `charset`, `viewport`, `title`, `meta description`, `og:title/description/image`, `twitter:card=summary_large_image`, favicon, `lang="en"`. `og:image` uses `${site.url}/og.png`.
- `public/`: `favicon.svg` (🪨 emoji), `og.png` (**1200×630**, verified). No `robots.txt`.
- `@astrojs/sitemap` not installed. `src/lib/content.ts` exports `site = { title, description, url, repo }`.

## Goal / decisions (brainstorm)

Phase 1 = technical SEO foundation only. Everything is derived from `Astro.site`/page URL so values are absolute and correct. Claims in structured data must be truthful (free, open-source, Apache-2.0 CLI).

## Architecture / changes

### A. Sitemap (`astro.config.mjs` + dep)

Add `@astrojs/sitemap` to `website/package.json` and register it in `astro.config.mjs` `integrations`. On `astro build` it emits `dist/sitemap-index.xml` + `dist/sitemap-0.xml` from `site`. No options needed for a single-page site (defaults are fine).

### B. `robots.txt` (`website/public/robots.txt`, new)

Static file (Astro copies `public/` verbatim):
```
User-agent: *
Allow: /

Sitemap: https://agentcairn.dev/sitemap-index.xml
```

### C. Site metadata (`src/lib/content.ts`)

Extend `site` with the small fields the head needs (keeps `Base.astro` DRY and values single-sourced):
```ts
export const site = {
  name: "agentcairn",
  title: "agentcairn — local-first memory for AI agents",
  description:
    "Your agent's memory as plain Markdown you own. A rebuildable DuckDB index gives fast hybrid retrieval; the vault is the source of truth.",
  url: "https://agentcairn.dev",
  repo: "https://github.com/ccf/agentcairn",
  pypi: "https://pypi.org/project/agentcairn/",
  ogImageAlt: "agentcairn — local-first memory for AI agents",
  themeColor: "#0b0b0c", // match the site background; confirm against global.css
};
```
(`themeColor` value confirmed against the actual site background during implementation.)

### D. `Base.astro` `<head>` completion

All absolute URLs via `new URL(...)` against `Astro.site`. Add to the existing head:
- **Canonical:** `<link rel="canonical" href={new URL(Astro.url.pathname, Astro.site).href} />`
- **Robots:** `<meta name="robots" content="index,follow" />`
- **Theme color:** `<meta name="theme-color" content={site.themeColor} />`
- **Open Graph (complete):** keep `og:title`/`og:description`; add `og:type="website"`, `og:url` (canonical), `og:site_name={site.name}`, `og:locale="en_US"`; make image absolute `new URL("/og.png", Astro.site).href` with `og:image:width="1200"`, `og:image:height="630"`, `og:image:alt={site.ogImageAlt}`.
- **Twitter (explicit):** keep `twitter:card`; add `twitter:title`, `twitter:description`, `twitter:image` (absolute og.png), `twitter:image:alt`.

The `og:image` absolute URL is shared with twitter:image (compute once in the frontmatter).

### E. Structured data (JSON-LD) in `Base.astro`

Two `<script type="application/ld+json">` blocks, serialized from JS objects (so they're valid JSON):
- **WebSite:**
  ```json
  { "@context": "https://schema.org", "@type": "WebSite",
    "name": "agentcairn", "url": "https://agentcairn.dev",
    "description": "<site.description>" }
  ```
- **SoftwareApplication:**
  ```json
  { "@context": "https://schema.org", "@type": "SoftwareApplication",
    "name": "agentcairn", "applicationCategory": "DeveloperApplication",
    "operatingSystem": "macOS, Linux, Windows", "url": "https://agentcairn.dev",
    "description": "<site.description>",
    "offers": { "@type": "Offer", "price": "0", "priceCurrency": "USD" },
    "license": "https://www.apache.org/licenses/LICENSE-2.0",
    "sameAs": ["<repo>", "<pypi>"] }
  ```
Serialize with `JSON.stringify(obj)` and emit via `set:html` so Astro doesn't HTML-escape the JSON. URLs/strings come from `site`.

## Data flow

```
astro build
  → @astrojs/sitemap reads `site` → dist/sitemap-index.xml + sitemap-0.xml
  → public/robots.txt copied verbatim → dist/robots.txt (references the sitemap)
  → Base.astro renders per-page: canonical + complete OG/Twitter (absolute) + 2 JSON-LD blocks
post-merge: deploy via Cloudflare; re-submit sitemap in Search Console (or auto-discovered via robots.txt)
```

## Error handling / correctness

- All social/canonical URLs are absolute (`new URL(..., Astro.site)`), required by OG/Twitter/canonical; `Astro.site` is set, so this can't produce relative URLs.
- JSON-LD is built from JS objects + `JSON.stringify` → always valid JSON; emitted with `set:html` to avoid entity-escaping that breaks parsers.
- `robots.txt` is a literal static file — no templating, no chance of a `Disallow: /` slip.
- Structured-data claims are truthful (free/$0, Apache-2.0, cross-platform CLI) to avoid spammy-markup penalties.

## Testing / verification

- `npm run build` (in `website/`) succeeds and `dist/` contains `sitemap-index.xml`, `sitemap-0.xml`, and `robots.txt`; `robots.txt` references the sitemap URL.
- Built `dist/index.html` `<head>` contains: one `rel=canonical` (absolute), `og:type/url/site_name/image:width/height/alt`, `twitter:title/description/image`, `meta robots`, `theme-color`, and two `application/ld+json` blocks.
- **JSON-LD validity:** each `ld+json` block `JSON.parse`s without error (assert in a test over the built HTML, or a node check) — no HTML-escaped entities inside.
- Existing `website/tests` (Playwright + the a11y/`build` check that previously caught axe issues) still pass; no new console errors; Lighthouse/CWV not regressed (static + no new blocking JS).
- Manual: paste the built page into a card validator mentally / confirm `og:image` is absolute and 1200×630.

## File-by-file

| File | Change |
|---|---|
| `website/package.json` | add `@astrojs/sitemap` dependency |
| `website/astro.config.mjs` | register `sitemap()` in `integrations` |
| `website/public/robots.txt` | **new** — allow all + sitemap reference |
| `website/src/lib/content.ts` | extend `site` (name, pypi, ogImageAlt, themeColor) |
| `website/src/layouts/Base.astro` | canonical + complete OG/Twitter + robots/theme-color + 2 JSON-LD blocks |
| `website/tests/` | assert sitemap/robots emitted + head has canonical/OG/JSON-LD (JSON parses) |

## Non-goals

- New content/landing/comparison pages, docs site (Phase 2 — the ranking lever).
- Analytics, backlinks, Search Console submission automation, outreach (Phase 3).
- Any change outside `website/`.

## Open questions

None.
