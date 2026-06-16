# Website SEO Phase 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make `agentcairn.dev` fully indexable and rich-result-eligible: sitemap, robots.txt, canonical, complete Open Graph/Twitter, and JSON-LD structured data.

**Architecture:** Astro static site in `website/`. Add `@astrojs/sitemap`; a static `robots.txt`; complete the `<head>` in `Base.astro` (all absolute URLs via `Astro.site`) + two JSON-LD blocks; extend `site` metadata in `content.ts`. A Playwright `seo.spec.ts` asserts the served output.

**Tech Stack:** Astro 6 (static), `@astrojs/sitemap`, Playwright, npm. All work in `website/` (use `npm`, not uv).

**Reference:** Spec `docs/specs/2026-06-16-website-seo-phase1-design.md`. Branch `feat/website-seo-phase1`. `og.png` is 1200×630 (verified). `Astro.site = https://agentcairn.dev`.

---

## Task 1: Sitemap + robots.txt + site metadata

**Files:** `website/package.json`, `website/astro.config.mjs`, `website/public/robots.txt` (new), `website/src/lib/content.ts`.

- [ ] **Step 1: Install the sitemap integration**

```bash
cd website && npm install @astrojs/sitemap
```

- [ ] **Step 2: Register it in `astro.config.mjs`**

Add the import and include it in `integrations` (keep `react()` and the existing config):
```js
import sitemap from "@astrojs/sitemap";
// ...
  integrations: [react(), sitemap()],
```

- [ ] **Step 3: Add `website/public/robots.txt`**

```
User-agent: *
Allow: /

Sitemap: https://agentcairn.dev/sitemap-index.xml
```

- [ ] **Step 4: Extend `site` in `website/src/lib/content.ts`**

Add fields (keep existing `title`/`description`/`url`/`repo`):
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
  themeColor: "#0b0b0c",
};
```
Confirm `themeColor` against the actual page background: `grep -iE "background|--bg|body" website/src/styles/global.css | head`. Use the real background hex; `#0b0b0c` is a placeholder to replace if it differs.

- [ ] **Step 5: Build and verify the static outputs**

```bash
cd website && npm run build
ls dist/sitemap-index.xml dist/sitemap-0.xml dist/robots.txt
grep -i "sitemap-index" dist/robots.txt
grep -o "agentcairn.dev" dist/sitemap-0.xml | head -1
```
Expected: all three files exist; robots references the sitemap; sitemap contains the site URL.

- [ ] **Step 6: Commit**

```bash
git add website/package.json website/package-lock.json website/astro.config.mjs website/public/robots.txt website/src/lib/content.ts
git commit -m "feat(seo): sitemap + robots.txt + extend site metadata"
```

---

## Task 2: Complete `<head>` + JSON-LD in `Base.astro`

**Files:** `website/src/layouts/Base.astro`.

- [ ] **Step 1: Rewrite the head with canonical, complete OG/Twitter, robots/theme-color, and JSON-LD**

Replace the frontmatter + `<head>` of `Base.astro` with:
```astro
---
import "../styles/global.css";
import { site } from "../lib/content";
const { title = site.title, description = site.description } = Astro.props;
const canonical = new URL(Astro.url.pathname, Astro.site).href;
const ogImage = new URL("/og.png", Astro.site).href;

const websiteLd = {
  "@context": "https://schema.org",
  "@type": "WebSite",
  name: site.name,
  url: site.url,
  description: site.description,
};
const appLd = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: site.name,
  applicationCategory: "DeveloperApplication",
  operatingSystem: "macOS, Linux, Windows",
  url: site.url,
  description: site.description,
  offers: { "@type": "Offer", price: "0", priceCurrency: "USD" },
  license: "https://www.apache.org/licenses/LICENSE-2.0",
  sameAs: [site.repo, site.pypi],
};
---
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <meta name="description" content={description} />
    <link rel="canonical" href={canonical} />
    <meta name="robots" content="index,follow" />
    <meta name="theme-color" content={site.themeColor} />

    <meta property="og:type" content="website" />
    <meta property="og:site_name" content={site.name} />
    <meta property="og:locale" content="en_US" />
    <meta property="og:url" content={canonical} />
    <meta property="og:title" content={title} />
    <meta property="og:description" content={description} />
    <meta property="og:image" content={ogImage} />
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="630" />
    <meta property="og:image:alt" content={site.ogImageAlt} />

    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:title" content={title} />
    <meta name="twitter:description" content={description} />
    <meta name="twitter:image" content={ogImage} />
    <meta name="twitter:image:alt" content={site.ogImageAlt} />

    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
    <script type="application/ld+json" set:html={JSON.stringify(websiteLd)} />
    <script type="application/ld+json" set:html={JSON.stringify(appLd)} />
  </head>
  <body>
    <a href="#main" class="sr-only focus:not-sr-only">Skip to content</a>
    <slot />
  </body>
</html>
```
(`set:html` is required so Astro emits the JSON verbatim instead of HTML-escaping `"`/`/`, which would break JSON parsers.)

- [ ] **Step 2: Build and verify the rendered head**

```bash
cd website && npm run build
grep -o 'rel="canonical" href="[^"]*"' dist/index.html
grep -c 'application/ld+json' dist/index.html        # expect 2
grep -o 'og:image" content="[^"]*"' dist/index.html  # expect absolute https URL
node -e "const h=require('fs').readFileSync('dist/index.html','utf8'); const m=[...h.matchAll(/<script type=\"application\/ld\+json\">([\s\S]*?)<\/script>/g)]; m.forEach((x,i)=>{JSON.parse(x[1]); console.log('ld+json',i,'OK')}); if(m.length!==2) throw new Error('expected 2 ld+json, got '+m.length)"
```
Expected: canonical is absolute; exactly 2 `ld+json` blocks, both `JSON.parse` cleanly; `og:image` is an absolute `https://agentcairn.dev/og.png`.

- [ ] **Step 3: Commit**

```bash
git add website/src/layouts/Base.astro
git commit -m "feat(seo): canonical, complete OG/Twitter, theme-color, WebSite+SoftwareApplication JSON-LD"
```

---

## Task 3: SEO test + full verification

**Files:** `website/tests/seo.spec.ts` (new).

- [ ] **Step 1: Write the SEO test**

Mirror the existing Playwright pattern (`tests/smoke.spec.ts`); the configured `webServer` serves the built/preview site. Create `website/tests/seo.spec.ts`:
```ts
import { test, expect } from "@playwright/test";

test("head has canonical, absolute OG image, and complete cards", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", "https://agentcairn.dev/");
  await expect(page.locator('meta[property="og:type"]')).toHaveAttribute("content", "website");
  await expect(page.locator('meta[property="og:url"]')).toHaveAttribute("content", "https://agentcairn.dev/");
  await expect(page.locator('meta[property="og:image"]')).toHaveAttribute("content", "https://agentcairn.dev/og.png");
  await expect(page.locator('meta[property="og:site_name"]')).toHaveAttribute("content", "agentcairn");
  await expect(page.locator('meta[name="twitter:title"]')).toHaveCount(1);
  await expect(page.locator('meta[name="twitter:image"]')).toHaveAttribute("content", "https://agentcairn.dev/og.png");
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "index,follow");
});

test("two valid JSON-LD blocks (WebSite + SoftwareApplication)", async ({ page }) => {
  await page.goto("/");
  const blocks = await page.locator('script[type="application/ld+json"]').allTextContents();
  expect(blocks.length).toBe(2);
  const types = blocks.map((b) => JSON.parse(b)["@type"]); // throws if not valid JSON
  expect(types).toContain("WebSite");
  expect(types).toContain("SoftwareApplication");
});

test("robots.txt and sitemap are served", async ({ request }) => {
  const robots = await request.get("/robots.txt");
  expect(robots.ok()).toBeTruthy();
  expect(await robots.text()).toContain("sitemap-index.xml");
  const sm = await request.get("/sitemap-index.xml");
  expect(sm.ok()).toBeTruthy();
});
```
If `request.get("/robots.txt")` doesn't resolve against the preview baseURL in this harness, adapt to `page.goto("/robots.txt")` + `page.content()`, or assert the files in `dist/` directly — keep the three behaviors covered (robots served + references sitemap; sitemap served).

- [ ] **Step 2: Run the SEO test + the full suite**

```bash
cd website && npm run build && npx playwright test 2>&1 | tail -20
```
Expected: new SEO tests pass AND existing `a11y`, `reduced-motion`, `smoke` specs still pass. (If Playwright needs browsers: `npx playwright install` first.)

- [ ] **Step 3: Confirm no a11y/build regression**

The a11y spec must stay green (the head changes are non-visual; JSON-LD `<script>` is inert). Confirm no new console errors and the page renders identically.

- [ ] **Step 4: Commit**

```bash
git add website/tests/seo.spec.ts
git commit -m "test(seo): assert head meta, JSON-LD validity, robots+sitemap served"
```

---

## Final verification

- [ ] `cd website && npm run build` clean; `dist/` has `sitemap-index.xml`, `sitemap-0.xml`, `robots.txt`.
- [ ] `npx playwright test` all green (seo + a11y + reduced-motion + smoke).
- [ ] Built `dist/index.html` head: one absolute canonical, complete OG (incl. `og:image:width/height/alt`), explicit Twitter tags, `meta robots`, `theme-color`, exactly 2 valid `ld+json` blocks.
- [ ] **Post-merge (user):** redeploy (Cloudflare auto-builds on push), then in Google Search Console submit `https://agentcairn.dev/sitemap-index.xml` (or rely on robots.txt auto-discovery); optionally run the live URL through Google's Rich Results Test.

## Self-Review (during planning)

- **Spec coverage:** sitemap (T1), robots.txt (T1), site metadata (T1), canonical + OG/Twitter + robots/theme-color (T2), JSON-LD WebSite+SoftwareApplication (T2), tests incl. JSON-LD validity + robots/sitemap served (T3). Non-goals (content pages, analytics, outreach) untouched.
- **Consistency:** `site.name/ogImageAlt/themeColor/pypi` added in T1 are consumed in T2; `ogImage`/`canonical` computed once and reused for OG+Twitter; JSON-LD `sameAs` uses `site.repo`/`site.pypi`.
- **Placeholders:** none except `themeColor` hex, explicitly flagged to confirm against `global.css`.
- **Stack note:** all `website/` work uses `npm`/`npx` (not uv); Playwright may need `npx playwright install`.
