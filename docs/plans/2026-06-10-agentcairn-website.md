# agentcairn Website Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the agentcairn marketing landing page — an editorial, comprehension-led single page — per `docs/specs/2026-06-10-agentcairn-website-design.md`, deployable to `agentcairn.dev`.

**Architecture:** A static Astro site in a self-contained `website/` subtree of the agentcairn repo. Zero JS by default; one React island carries the two Motion-driven visuals (hero diagram + "survives uninstall" demo). Design tokens, fonts, and copy live in dedicated single-responsibility modules; section components render copy from a central `content.ts` so the page is content-driven and DRY. Tests are the right ones for the medium: `astro build` + `astro check` gates, Playwright smoke + reduced-motion + axe accessibility checks.

**Tech Stack:** Astro 5 (static), Tailwind CSS v4 (`@tailwindcss/vite` + CSS `@theme`), `@tailwindcss/typography`, Fontsource (Newsreader / Geist / Geist Mono, self-hosted), Motion (`motion/react`), Playwright + `@axe-core/playwright`, Cloudflare Pages.

---

## File structure (created by this plan)

```
website/
  package.json                    # deps + scripts (dev/build/preview/check/test)
  astro.config.mjs                # static output + tailwind vite plugin + react integration
  tsconfig.json                   # strict TS for islands
  playwright.config.ts            # e2e against `astro preview`
  .gitignore                      # node_modules, dist, .astro, test-results
  README.md                       # how to run/build/deploy the site
  src/
    styles/
      global.css                  # @import tailwindcss; @theme tokens (§4); fonts; prose tweaks; reduced-motion
    lib/
      content.ts                  # ALL copy/data: hero, sections, differentiators, benchmark rows, CLI, roadmap
    layouts/
      Base.astro                  # <html> shell, <head> meta/OG, global.css import, font imports, skip-link
    components/
      Nav.astro                   # quiet top nav
      Section.astro               # reusable section wrapper (eyebrow + spacing rhythm)
      Prose.astro                 # 680px serif measure wrapper
      Hero.astro                  # eyebrow, H1, subhead, byline, CTA row (+ CopyButton, + HeroDiagram island)
      CopyButton.astro            # copy-to-clipboard (tiny inline script, no framework)
      Inversion.astro             # §7.2 before/after
      Differentiators.astro       # §7.3 2x3 grid
      HowItWorks.astro            # §7.4 inline SVG data-flow
      Measured.astro              # §7.6 benchmark SVG table (nomic numbers)
      SurvivesUninstall.astro     # §7.5 wraps the demo island
      Quickstart.astro            # §7.7 CLI block
      TrustSecurity.astro         # §7.8 4-item strip
      Roadmap.astro               # §7.9 dated checklist
      PriorArt.astro              # §7.10 list
      Footer.astro                # §7.11 legal/links, no CTA
    islands/
      HeroDiagram.tsx             # §8.1 "markdown -> graph -> recall" Motion animation
      SurvivesUninstallDemo.tsx   # §8.2 staged interactive widget
    pages/
      index.astro                 # composes the sections in order
  public/
    favicon.svg                   # the rock glyph
    og.png                        # social card (built from the hero diagram)
  tests/
    smoke.spec.ts                 # renders, hero copy, install line, all sections present, copy button
    reduced-motion.spec.ts        # prefers-reduced-motion -> islands show final state
    a11y.spec.ts                  # axe: no critical/serious violations
.github/workflows/site.yml        # build + deploy website/ to Cloudflare Pages
```

**Conventions for every task:** run commands from the `website/` directory unless noted. The Python suite and existing CI are untouched. Commit after each task.

---

## Task 1: Scaffold the Astro project

**Files:**
- Create: `website/package.json`, `website/astro.config.mjs`, `website/tsconfig.json`, `website/.gitignore`, `website/src/pages/index.astro`

- [ ] **Step 1: Create the project skeleton**

`website/package.json`:
```json
{
  "name": "agentcairn-website",
  "type": "module",
  "private": true,
  "scripts": {
    "dev": "astro dev",
    "build": "astro build",
    "preview": "astro preview --port 4321",
    "check": "astro check",
    "test": "playwright test"
  },
  "dependencies": {
    "astro": "^5.0.0",
    "@astrojs/react": "^4.0.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "motion": "^11.11.0",
    "tailwindcss": "^4.0.0",
    "@tailwindcss/vite": "^4.0.0",
    "@tailwindcss/typography": "^0.5.15",
    "@fontsource-variable/newsreader": "^5.0.0",
    "@fontsource-variable/geist": "^5.0.0",
    "@fontsource-variable/geist-mono": "^5.0.0"
  },
  "devDependencies": {
    "@astrojs/check": "^0.9.0",
    "typescript": "^5.6.0",
    "@playwright/test": "^1.48.0",
    "@axe-core/playwright": "^4.10.0"
  }
}
```
> Note: pin exact versions with `npm install` (it writes the lockfile). If a Fontsource package name 404s, confirm with `npm view @fontsource-variable/geist` — Geist Sans is `@fontsource-variable/geist`, mono is `@fontsource-variable/geist-mono`, Newsreader is `@fontsource-variable/newsreader`.

`website/astro.config.mjs`:
```js
// @ts-check
import { defineConfig } from "astro/config";
import react from "@astrojs/react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  output: "static",
  site: "https://agentcairn.dev",
  integrations: [react()],
  vite: { plugins: [tailwindcss()] },
});
```

`website/tsconfig.json`:
```json
{
  "extends": "astro/tsconfigs/strict",
  "include": [".astro/types.d.ts", "**/*"],
  "exclude": ["dist"],
  "compilerOptions": { "jsx": "react-jsx", "jsxImportSource": "react" }
}
```

`website/.gitignore`:
```
node_modules/
dist/
.astro/
test-results/
playwright-report/
```

`website/src/pages/index.astro` (temporary, replaced in Task 3):
```astro
---
---
<html lang="en"><head><title>agentcairn</title></head><body><h1>agentcairn</h1></body></html>
```

- [ ] **Step 2: Install and verify the build**

Run (from `website/`): `npm install && npm run build`
Expected: install succeeds; `astro build` prints "1 page(s) built" and creates `website/dist/index.html`.

- [ ] **Step 3: Commit**

```bash
git add website/package.json website/package-lock.json website/astro.config.mjs website/tsconfig.json website/.gitignore website/src/pages/index.astro
git commit -m "chore(website): scaffold Astro static project"
```

---

## Task 2: Design tokens, fonts, and global CSS

**Files:**
- Create: `website/src/styles/global.css`

- [ ] **Step 1: Write the token + font + prose stylesheet**

`website/src/styles/global.css` (implements spec §3/§4/§5/§6):
```css
@import "tailwindcss";
@plugin "@tailwindcss/typography";

/* Self-hosted variable fonts (Fontsource) */
@import "@fontsource-variable/newsreader";
@import "@fontsource-variable/geist";
@import "@fontsource-variable/geist-mono";

@theme {
  --color-bg: #ffffff;
  --color-surface: #fafaf8;
  --color-ink: #191919;
  --color-ink-muted: rgba(25, 25, 25, 0.56);
  --color-ink-faint: rgba(25, 25, 25, 0.40);
  --color-border: rgba(0, 0, 0, 0.10);
  --color-border-faint: rgba(0, 0, 0, 0.05);
  --color-accent: #317cff;
  --color-accent-warm: #e89b3c;

  --font-serif: "Newsreader Variable", Georgia, "Times New Roman", serif;
  --font-sans: "Geist Variable", system-ui, sans-serif;
  --font-mono: "Geist Mono Variable", ui-monospace, "SF Mono", monospace;

  --spacing-section: 96px;     /* between major sections */
  --container-prose: 680px;
  --container-frame: 1100px;

  --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
}

html { -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility; }
body { background: var(--color-bg); color: var(--color-ink);
       font-family: var(--font-serif); font-size: 16px; line-height: 1.6; }
@media (min-width: 768px) { body { font-size: 18px; } }

/* Headings + eyebrows are sans; body stays serif (the inversion) */
h1, h2, h3, .font-sans { font-family: var(--font-sans); }
.eyebrow { font-family: var(--font-mono); font-size: 12px; letter-spacing: 0.05em;
           text-transform: uppercase; color: var(--color-ink-muted); }

/* Editorial prose measure */
.prose-measure { max-width: var(--container-prose); }

/* Honor reduced motion globally (islands also check via useReducedMotion) */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important; animation-iteration-count: 1 !important; }
}
```

- [ ] **Step 2: Verify it builds and is imported**

Temporarily import it by editing `website/src/pages/index.astro` to `---\nimport "../styles/global.css";\n---` above the existing markup, then run `npm run build`.
Expected: build succeeds, `dist/` CSS contains `--color-accent` and the `@font-face` rules from Fontsource.

- [ ] **Step 3: Commit**

```bash
git add website/src/styles/global.css website/src/pages/index.astro
git commit -m "feat(website): design tokens, self-hosted fonts, prose + reduced-motion CSS"
```

---

## Task 3: Base layout, Nav, Footer, page shell + Playwright smoke test

**Files:**
- Create: `website/src/layouts/Base.astro`, `website/src/components/Nav.astro`, `website/src/components/Footer.astro`, `website/src/lib/content.ts`, `website/playwright.config.ts`, `website/tests/smoke.spec.ts`
- Modify: `website/src/pages/index.astro`

- [ ] **Step 1: Create the content module (single source of copy)**

`website/src/lib/content.ts` (seed with the locked hero + nav/footer; later tasks extend it):
```ts
export const site = {
  title: "agentcairn — local-first memory for AI agents",
  description:
    "Your agent's memory as plain Markdown you own. A rebuildable DuckDB index gives fast hybrid retrieval; the vault is the source of truth.",
  url: "https://agentcairn.dev",
  repo: "https://github.com/ccf/agentcairn",
};

export const nav = [
  { label: "How it works", href: "#how" },
  { label: "Benchmarks", href: "#measured" },
  { label: "Quickstart", href: "#quickstart" },
  { label: "GitHub", href: site.repo },
];

export const hero = {
  eyebrow: "Local-first memory for AI agents",
  h1: "Most agent memory makes a database the source of truth. We made it your files.",
  subhead:
    "agentcairn inverts the stack: human-readable Markdown with [[wikilinks]] is the truth, and a rebuildable DuckDB index gives your agent fast hybrid retrieval. Hand-edit a fact in Obsidian and the agent picks it up.",
  byline: "By Charles C. Figueiredo · Apache-2.0",
  install: "uvx agentcairn",
  specHref: site.repo + "/blob/main/docs/specs/2026-06-08-agentcairn-design.md",
};

export const footer = {
  license: "Apache-2.0",
  copyright: "© 2026 Charles C. Figueiredo",
};
```

- [ ] **Step 2: Create Base layout**

`website/src/layouts/Base.astro`:
```astro
---
import "../styles/global.css";
import { site } from "../lib/content";
const { title = site.title, description = site.description } = Astro.props;
---
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{title}</title>
    <meta name="description" content={description} />
    <meta property="og:title" content={title} />
    <meta property="og:description" content={description} />
    <meta property="og:image" content={`${site.url}/og.png`} />
    <meta name="twitter:card" content="summary_large_image" />
    <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
  </head>
  <body>
    <a href="#main" class="sr-only focus:not-sr-only">Skip to content</a>
    <slot />
  </body>
</html>
```

- [ ] **Step 3: Create Nav and Footer**

`website/src/components/Nav.astro`:
```astro
---
import { nav } from "../lib/content";
---
<nav class="mx-auto flex max-w-[var(--container-frame)] items-center justify-between px-8 py-6 border-b border-[var(--color-border-faint)]">
  <a href="/" class="font-sans font-medium tracking-tight text-[16px]">🪨 agentcairn</a>
  <div class="font-mono text-[13px] text-[var(--color-ink-muted)] flex gap-6">
    {nav.map((n) => <a href={n.href} class="hover:text-[var(--color-ink)]">{n.label}</a>)}
  </div>
</nav>
```

`website/src/components/Footer.astro`:
```astro
---
import { footer, site } from "../lib/content";
---
<footer class="mx-auto max-w-[var(--container-frame)] px-8 py-16 mt-[var(--spacing-section)] border-t border-[var(--color-border-faint)] font-mono text-[12px] text-[var(--color-ink-faint)] flex flex-wrap gap-x-6 gap-y-2">
  <span>{footer.copyright}</span>
  <a href={`${site.repo}/blob/main/LICENSE`} class="hover:text-[var(--color-ink)]">{footer.license}</a>
  <a href={site.repo} class="hover:text-[var(--color-ink)]">GitHub</a>
  <span class="text-[var(--color-ink)]">uvx agentcairn</span>
</footer>
```

- [ ] **Step 4: Compose the page shell**

`website/src/pages/index.astro`:
```astro
---
import Base from "../layouts/Base.astro";
import Nav from "../components/Nav.astro";
import Footer from "../components/Footer.astro";
---
<Base>
  <Nav />
  <main id="main"></main>
  <Footer />
</Base>
```

- [ ] **Step 5: Add Playwright config + smoke test**

`website/playwright.config.ts`:
```ts
import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  webServer: { command: "npm run build && npm run preview", url: "http://localhost:4321", reuseExistingServer: !process.env.CI },
  use: { baseURL: "http://localhost:4321" },
});
```

`website/tests/smoke.spec.ts`:
```ts
import { test, expect } from "@playwright/test";
test("page renders with brand and nav", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveTitle(/agentcairn/);
  await expect(page.getByRole("link", { name: /agentcairn/ }).first()).toBeVisible();
});
```

- [ ] **Step 6: Run it (fail then pass)**

Run: `npx playwright install --with-deps chromium && npm test`
Expected: smoke test PASSES (page + nav render). If `webServer` times out, confirm `npm run preview` serves on 4321.

- [ ] **Step 7: Commit**

```bash
git add website/src/layouts website/src/components/Nav.astro website/src/components/Footer.astro website/src/lib/content.ts website/src/pages/index.astro website/playwright.config.ts website/tests/smoke.spec.ts
git commit -m "feat(website): base layout, nav, footer, content module + smoke test"
```

---

## Task 4: Hero (static parts) + copy-to-clipboard

**Files:**
- Create: `website/src/components/Hero.astro`, `website/src/components/CopyButton.astro`
- Modify: `website/src/pages/index.astro`, `website/tests/smoke.spec.ts`

- [ ] **Step 1: Add the failing smoke assertion**

Append to `website/tests/smoke.spec.ts`:
```ts
test("hero shows the inversion headline and install line", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1 })).toContainText("made it your files");
  await expect(page.getByText("uvx agentcairn").first()).toBeVisible();
});
```
Run `npm test` — Expected: FAIL (no hero yet).

- [ ] **Step 2: Build CopyButton**

`website/src/components/CopyButton.astro`:
```astro
---
const { text } = Astro.props;
---
<button
  class="font-mono text-[11px] text-[var(--color-ink-faint)] border-l border-[var(--color-border)] pl-3 hover:text-[var(--color-ink)]"
  data-copy={text} aria-label={`Copy: ${text}`}>copy</button>
<script>
  document.querySelectorAll("[data-copy]").forEach((b) =>
    b.addEventListener("click", async () => {
      await navigator.clipboard.writeText(b.getAttribute("data-copy") || "");
      const prev = b.textContent; b.textContent = "copied"; setTimeout(() => (b.textContent = prev), 1200);
    }));
</script>
```

- [ ] **Step 3: Build Hero (static composition; diagram island added in Task 5)**

`website/src/components/Hero.astro`:
```astro
---
import { hero } from "../lib/content";
import CopyButton from "./CopyButton.astro";
---
<header class="mx-auto max-w-[var(--container-frame)] px-8 pt-[90px] pb-14">
  <p class="eyebrow mb-7">{hero.eyebrow}</p>
  <h1 class="font-sans font-medium text-[32px] md:text-[38px] leading-[1.15] tracking-[-0.02em] max-w-[18ch] mb-6">{hero.h1}</h1>
  <p class="prose-measure font-serif text-[20px] leading-[1.55] text-[var(--color-ink-muted)] mb-8">{hero.subhead}</p>
  <p class="font-mono text-[12.5px] text-[var(--color-ink-faint)] mb-9">{hero.byline}</p>
  <div class="flex items-center gap-5 flex-wrap">
    <div class="font-mono text-[14px] bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg px-4 py-3 flex items-center gap-3">
      <span><span class="text-[var(--color-accent)]">$</span>&nbsp;{hero.install}</span>
      <CopyButton text={hero.install} />
    </div>
    <a href={hero.specHref} class="font-sans font-medium text-[14px] text-[var(--color-accent)] hover:underline">Read the spec →</a>
  </div>
  <!-- HeroDiagram island slot added in Task 5 -->
</header>
```

- [ ] **Step 4: Place Hero in the page**

In `website/src/pages/index.astro`, import `Hero` and render `<Hero />` as the first child of `<main id="main">`.

- [ ] **Step 5: Run tests + commit**

Run `npm test` — Expected: PASS. Then:
```bash
git add website/src/components/Hero.astro website/src/components/CopyButton.astro website/src/pages/index.astro website/tests/smoke.spec.ts
git commit -m "feat(website): hero section + copy-to-clipboard install line"
```

---

## Task 5: Hero signature animation island (HeroDiagram.tsx)

**Files:**
- Create: `website/src/islands/HeroDiagram.tsx`, `website/tests/reduced-motion.spec.ts`
- Modify: `website/src/components/Hero.astro`

- [ ] **Step 1: Write the island (Motion, reduced-motion aware)**

`website/src/islands/HeroDiagram.tsx` implements spec §8.1 — note (left) → DuckDB index + graph nodes (middle) → cited recall (right), drawing in once. Uses `useReducedMotion()` to render the final state instantly.
```tsx
import { motion, useReducedMotion } from "motion/react";

const ease = [0.16, 1, 0.3, 1] as const;

export default function HeroDiagram() {
  const reduce = useReducedMotion();
  const reveal = (delay: number) =>
    reduce
      ? { initial: { opacity: 1, y: 0 }, animate: { opacity: 1, y: 0 } }
      : { initial: { opacity: 0, y: 20 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.5, delay, ease } };
  const draw = (delay: number) =>
    reduce
      ? { initial: { pathLength: 1 }, animate: { pathLength: 1 } }
      : { initial: { pathLength: 0 }, animate: { pathLength: 1 }, transition: { duration: 0.8, delay, ease } };

  return (
    <div data-testid="hero-diagram" className="mt-14 rounded-2xl border border-[var(--color-border)] p-7 grid grid-cols-1 md:grid-cols-[1fr_auto_1fr_auto_1fr] gap-4 items-center bg-[linear-gradient(180deg,#fff,#fcfcfb)]">
      <motion.div {...reveal(0)}>
        <p className="eyebrow mb-2 text-[10.5px]">Vault · source of truth</p>
        <pre className="font-mono text-[12px] leading-[1.55] bg-[var(--color-surface)] border border-[var(--color-border)] rounded-lg p-3.5 whitespace-pre-wrap">{`---
tags: [auth, fix]
---
Fixed login by rotating
`}<span className="text-[var(--color-accent)]">[[jwt-secret]]</span>{` during `}<span className="text-[var(--color-accent)]">[[deploy]]</span>.</pre>
      </motion.div>

      <svg width="40" height="20" className="mx-auto"><motion.path {...draw(0.4)} d="M2 10 H38" stroke="var(--color-ink-faint)" fill="none" markerEnd="url(#a)" /></svg>

      <motion.div {...reveal(0.5)} className="flex flex-col items-center gap-3">
        <p className="eyebrow text-[10.5px]">Index · disposable cache</p>
        <div className="font-mono text-[11px] text-[var(--color-ink-muted)] border border-[var(--color-border)] rounded-lg px-3.5 py-2.5 bg-white w-full text-center">DuckDB · vector + BM25</div>
        <div className="flex items-center gap-2">
          <motion.span {...reveal(0.8)} className="w-3.5 h-3.5 rounded-full bg-[var(--color-accent)]" />
          <svg width="26" height="2"><motion.line {...draw(0.9)} x1="0" y1="1" x2="26" y2="1" stroke="var(--color-border)" /></svg>
          <motion.span {...reveal(0.9)} className="w-3.5 h-3.5 rounded-full bg-[var(--color-accent-warm)]" />
        </div>
      </motion.div>

      <svg width="40" height="20" className="mx-auto"><motion.path {...draw(1.0)} d="M2 10 H38" stroke="var(--color-ink-faint)" fill="none" /></svg>

      <motion.div {...reveal(1.1)}>
        <p className="eyebrow mb-2 text-[10.5px]">MCP · recall</p>
        <div className="font-sans text-[13px] bg-white border border-[var(--color-border)] rounded-lg p-3.5">
          <p className="font-mono text-[11.5px] text-[var(--color-ink-muted)] mb-2">cairn recall "how did we fix login?"</p>
          <p className="leading-[1.45]">Rotated the jwt-secret during deploy.</p>
          <p className="font-mono text-[10.5px] text-[var(--color-accent)] mt-2">↳ auth-fix.md</p>
        </div>
      </motion.div>
    </div>
  );
}
```

- [ ] **Step 2: Mount it in Hero as a visible island**

In `website/src/components/Hero.astro`, replace the `<!-- HeroDiagram island slot -->` comment with:
```astro
---
// add to the frontmatter imports:
import HeroDiagram from "../islands/HeroDiagram.tsx";
---
<HeroDiagram client:visible />
```

- [ ] **Step 3: Add reduced-motion test**

`website/tests/reduced-motion.spec.ts`:
```ts
import { test, expect } from "@playwright/test";
test("hero diagram renders its final state under reduced motion", async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto("/");
  const diagram = page.getByTestId("hero-diagram");
  await expect(diagram).toBeVisible();
  await expect(diagram.getByText("auth-fix.md")).toBeVisible(); // final state present, not mid-animation
});
```

- [ ] **Step 4: Run + commit**

Run `npm test` — Expected: PASS (diagram visible, final state under reduced motion).
```bash
git add website/src/islands/HeroDiagram.tsx website/src/components/Hero.astro website/tests/reduced-motion.spec.ts
git commit -m "feat(website): signature hero animation island (markdown→graph→recall)"
```

---

## Task 6: "The inversion" + "Six differentiators" sections

**Files:**
- Create: `website/src/components/Section.astro`, `website/src/components/Prose.astro`, `website/src/components/Inversion.astro`, `website/src/components/Differentiators.astro`
- Modify: `website/src/lib/content.ts`, `website/src/pages/index.astro`, `website/tests/smoke.spec.ts`

- [ ] **Step 1: Extend content.ts**

Append to `website/src/lib/content.ts`:
```ts
export const inversion = {
  eyebrow: "The inversion",
  h2: "Most systems make the database the truth. We made it your files.",
  body: [
    "Mem0 and Zep keep your memory in a cloud database. Letta and agentmemory keep it in a database too, and treat files — if any — as a one-way export. agentcairn is the only one where the Markdown vault *is* the source of truth.",
    "So your memory survives a model upgrade, a corrupted index, a schema change — even uninstalling the tool. There is nothing to lose, because the truth was never trapped in the database.",
  ],
};

export const differentiators = [
  { title: "Vault is the source of truth", body: "Human-readable Markdown with frontmatter and [[wikilinks]]. Edit it by hand; the index honors your edits." },
  { title: "The index is disposable", body: "DuckDB is a rebuildable cache. `cairn reindex` restores everything — zero data loss." },
  { title: "Non-lossy by construction", body: "The full note is always retained. Distillation only adds derived notes that link back." },
  { title: "Redaction before every write", body: "Secrets scrubbed (regex + entropy + URL-cred) before body, title, or tags reach the vault." },
  { title: "A free, deterministic graph", body: "Your [[wikilinks]] are the graph — no LLM extraction, no hallucinated entities." },
  { title: "Daemonless, zero external DB", body: "One embedded DuckDB does vector + BM25 + graph. No server, no Neo4j/Postgres/Qdrant." },
];
```

- [ ] **Step 2: Reusable Section + Prose wrappers**

`website/src/components/Section.astro`:
```astro
---
const { id, eyebrow } = Astro.props;
---
<section id={id} class="mx-auto max-w-[var(--container-frame)] px-8 pt-[var(--spacing-section)]">
  {eyebrow && <p class="eyebrow mb-4">{eyebrow}</p>}
  <slot />
</section>
```

`website/src/components/Prose.astro`:
```astro
<div class="prose-measure font-serif text-[18px] leading-[1.6] text-[var(--color-ink)]"><slot /></div>
```

- [ ] **Step 3: Inversion + Differentiators components**

`website/src/components/Inversion.astro`:
```astro
---
import Section from "./Section.astro";
import Prose from "./Prose.astro";
import { inversion } from "../lib/content";
---
<Section id="inversion" eyebrow={inversion.eyebrow}>
  <h2 class="font-sans font-medium text-[24px] md:text-[28px] tracking-[-0.015em] max-w-[24ch] mb-4">{inversion.h2}</h2>
  <Prose>{inversion.body.map((p) => <p class="mb-6" set:html={p.replace(/\*(.+?)\*/g, "<em>$1</em>")} />)}</Prose>
</Section>
```

`website/src/components/Differentiators.astro`:
```astro
---
import Section from "./Section.astro";
import { differentiators } from "../lib/content";
---
<Section id="why" eyebrow="Six differences">
  <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-x-10 gap-y-8 mt-2">
    {differentiators.map((d) => (
      <div>
        <h3 class="font-sans font-medium text-[18px] tracking-[-0.01em] mb-1.5">{d.title}</h3>
        <p class="font-serif text-[16px] leading-[1.5] text-[var(--color-ink-muted)]" set:html={d.body.replace(/`(.+?)`/g, '<code class="font-mono text-[0.85em]">$1</code>')} />
      </div>
    ))}
  </div>
</Section>
```

- [ ] **Step 4: Add to page + assert presence**

Add `<Inversion />` and `<Differentiators />` to `index.astro` after `<Hero />`. Append to `smoke.spec.ts`:
```ts
test("inversion + differentiators render", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: /made it your files/ })).toHaveCount(2); // hero + inversion
  await expect(page.getByText("A free, deterministic graph")).toBeVisible();
});
```

- [ ] **Step 5: Run + commit**

Run `npm test` (PASS), then:
```bash
git add website/src/components/Section.astro website/src/components/Prose.astro website/src/components/Inversion.astro website/src/components/Differentiators.astro website/src/lib/content.ts website/src/pages/index.astro website/tests/smoke.spec.ts
git commit -m "feat(website): inversion + six-differentiators sections"
```

---

## Task 7: "How it works" diagram + "Honestly measured" benchmark table

**Files:**
- Create: `website/src/components/HowItWorks.astro`, `website/src/components/Measured.astro`
- Modify: `website/src/lib/content.ts`, `website/src/pages/index.astro`, `website/tests/smoke.spec.ts`

- [ ] **Step 1: Extend content.ts with the benchmark rows (nomic default)**

```ts
export const howItWorks = {
  body: "Capture reads your agent's session transcripts out-of-band, then redacts → dedups → importance-gates → distills into the vault. Retrieval fuses BM25 + vectors with RRF, with an optional cross-encoder reranker. The vault and the index reconcile on spawn; the MCP server exposes remember · recall · search · build_context · recent.",
};

export const benchmark = {
  caption: "LoCoMo retrieval, turn-level macro-avg, FastEmbed nomic-embed-text-v1.5 (the default).",
  rows: [
    { arm: "BM25 only", r5: "0.527", r10: "0.604", mrr: "0.459", strong: false },
    { arm: "vector only", r5: "0.536", r10: "0.637", mrr: "0.433", strong: false },
    { arm: "hybrid (RRF)", r5: "0.562", r10: "0.648", mrr: "0.477", strong: false },
    { arm: "hybrid + reranker", r5: "0.662", r10: "0.735", mrr: "0.608", strong: true },
  ],
  caveats: [
    "No single headline number — these are relative ablation signals.",
    "graph-boost is inert on chat corpora (no native wikilink graph); it's for real vaults.",
    "QA-accuracy numbers use an Anthropic judge, not GPT-4o — not comparable to published leaderboards.",
  ],
};
```

- [ ] **Step 2: HowItWorks (inline SVG flow)**

`website/src/components/HowItWorks.astro`:
```astro
---
import Section from "./Section.astro";
import Prose from "./Prose.astro";
import { howItWorks } from "../lib/content";
---
<Section id="how" eyebrow="How it works">
  <Prose><p>{howItWorks.body}</p></Prose>
  <figure class="prose-measure mt-8" role="img" aria-label="vault to rebuildable DuckDB index to MCP tools data flow">
    <svg viewBox="0 0 680 120" class="w-full">
      <rect x="0" y="40" width="180" height="40" rx="8" fill="var(--color-surface)" stroke="var(--color-border)"/>
      <text x="90" y="64" text-anchor="middle" class="font-mono" font-size="12" fill="var(--color-ink)">Markdown vault</text>
      <line x1="180" y1="60" x2="250" y2="60" stroke="var(--color-ink-faint)"/>
      <rect x="250" y="40" width="180" height="40" rx="8" fill="#fff" stroke="var(--color-border)"/>
      <text x="340" y="64" text-anchor="middle" class="font-mono" font-size="12" fill="var(--color-ink)">DuckDB index</text>
      <line x1="430" y1="60" x2="500" y2="60" stroke="var(--color-ink-faint)"/>
      <rect x="500" y="40" width="180" height="40" rx="8" fill="#fff" stroke="var(--color-border)"/>
      <text x="590" y="64" text-anchor="middle" class="font-mono" font-size="12" fill="var(--color-accent)">MCP tools</text>
    </svg>
  </figure>
</Section>
```

- [ ] **Step 3: Measured (benchmark table)**

`website/src/components/Measured.astro`:
```astro
---
import Section from "./Section.astro";
import { benchmark } from "../lib/content";
---
<Section id="measured" eyebrow="Honestly measured">
  <div class="prose-measure">
    <table class="w-full border-collapse font-mono text-[13px]">
      <thead><tr class="text-[var(--color-ink-muted)] text-left border-b border-[var(--color-border)]">
        <th class="py-2 font-normal">arm</th><th class="py-2 font-normal">r@5</th><th class="py-2 font-normal">r@10</th><th class="py-2 font-normal">MRR</th>
      </tr></thead>
      <tbody>
        {benchmark.rows.map((r) => (
          <tr class={"border-b border-[var(--color-border-faint)] " + (r.strong ? "text-[var(--color-ink)] font-medium" : "text-[var(--color-ink-muted)]")}>
            <td class="py-2">{r.arm}</td><td>{r.r5}</td><td>{r.r10}</td><td>{r.mrr}</td>
          </tr>
        ))}
      </tbody>
    </table>
    <p class="font-serif text-[14px] text-[var(--color-ink-faint)] mt-3">{benchmark.caption}</p>
    <ul class="font-serif text-[14px] text-[var(--color-ink-muted)] mt-4 list-disc pl-5 space-y-1">
      {benchmark.caveats.map((c) => <li>{c}</li>)}
    </ul>
  </div>
</Section>
```

- [ ] **Step 4: Add to page + assert + commit**

Add `<HowItWorks />` and `<Measured />` to `index.astro`. Append to `smoke.spec.ts`:
```ts
test("benchmark table shows the nomic reranker row", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("0.662")).toBeVisible();
  await expect(page.getByText(/nomic-embed-text/)).toBeVisible();
});
```
Run `npm test` (PASS), then commit:
```bash
git add website/src/components/HowItWorks.astro website/src/components/Measured.astro website/src/lib/content.ts website/src/pages/index.astro website/tests/smoke.spec.ts
git commit -m "feat(website): how-it-works diagram + honest benchmark table (nomic)"
```

---

## Task 8: "Survives uninstall" interactive demo island

**Files:**
- Create: `website/src/islands/SurvivesUninstallDemo.tsx`, `website/src/components/SurvivesUninstall.astro`
- Modify: `website/src/pages/index.astro`, `website/tests/smoke.spec.ts`, `website/tests/reduced-motion.spec.ts`

- [ ] **Step 1: Write the staged widget island**

`website/src/islands/SurvivesUninstallDemo.tsx` implements spec §8.2 — three button-advanced stages (delete index → reindex → restored), Motion transitions, reduced-motion shows final state.
```tsx
import { useState } from "react";
import { motion, useReducedMotion } from "motion/react";

const stages = [
  { cmd: "rm ~/.cache/agentcairn/index.duckdb", out: "index deleted.", label: "Delete the index" },
  { cmd: "cairn reindex ~/vault", out: "rebuilding from Markdown… 128 notes indexed.", label: "Reindex" },
  { cmd: "cairn recall \"auth fix\"", out: "restored — 0 facts lost. The vault was the truth.", label: "Recall" },
];

export default function SurvivesUninstallDemo() {
  const reduce = useReducedMotion();
  const [i, setI] = useState(reduce ? stages.length - 1 : 0);
  const shown = stages.slice(0, i + 1);
  return (
    <div data-testid="uninstall-demo" className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-surface)] p-5 font-mono text-[13px]">
      {shown.map((s, k) => (
        <motion.div key={k}
          initial={reduce ? { opacity: 1 } : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }} className="mb-2">
          <div><span className="text-[var(--color-accent)]">$</span> {s.cmd}</div>
          <div className="text-[var(--color-ink-muted)]">{s.out}</div>
        </motion.div>
      ))}
      {i < stages.length - 1 && (
        <button onClick={() => setI(i + 1)}
          className="mt-2 font-sans text-[13px] font-medium text-[var(--color-accent)] hover:underline">
          {stages[i + 1].label} →
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Wrap it in a section**

`website/src/components/SurvivesUninstall.astro`:
```astro
---
import Section from "./Section.astro";
import Prose from "./Prose.astro";
import SurvivesUninstallDemo from "../islands/SurvivesUninstallDemo.tsx";
---
<Section id="survives" eyebrow="Survives uninstall">
  <h2 class="font-sans font-medium text-[24px] md:text-[28px] tracking-[-0.015em] max-w-[24ch] mb-4">Delete the index. Reindex. Everything's back.</h2>
  <div class="prose-measure mb-6"><Prose><p>The index is a cache. The proof is destructive: remove it, rebuild it, lose nothing — because the truth was never in the database.</p></Prose></div>
  <div class="prose-measure"><SurvivesUninstallDemo client:visible /></div>
</Section>
```

- [ ] **Step 3: Tests (smoke advance + reduced-motion final state)**

Append to `smoke.spec.ts`:
```ts
test("uninstall demo advances through stages", async ({ page }) => {
  await page.goto("/");
  const demo = page.getByTestId("uninstall-demo");
  await demo.getByRole("button", { name: /Reindex/ }).click();
  await demo.getByRole("button", { name: /Recall/ }).click();
  await expect(demo.getByText(/0 facts lost/)).toBeVisible();
});
```
Append to `reduced-motion.spec.ts`:
```ts
test("uninstall demo shows final stage under reduced motion", async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto("/");
  await expect(page.getByTestId("uninstall-demo").getByText(/0 facts lost/)).toBeVisible();
});
```

- [ ] **Step 4: Add to page + run + commit**

Add `<SurvivesUninstall />` to `index.astro` (after `<Measured />` per spec order is §5 before §6; place it before `<Measured />` to match spec section order 5→6). Run `npm test` (PASS), then:
```bash
git add website/src/islands/SurvivesUninstallDemo.tsx website/src/components/SurvivesUninstall.astro website/src/pages/index.astro website/tests/smoke.spec.ts website/tests/reduced-motion.spec.ts
git commit -m "feat(website): interactive survives-uninstall demo island"
```

---

## Task 9: Quickstart, Trust & security, Roadmap, Prior art

**Files:**
- Create: `website/src/components/Quickstart.astro`, `website/src/components/TrustSecurity.astro`, `website/src/components/Roadmap.astro`, `website/src/components/PriorArt.astro`
- Modify: `website/src/lib/content.ts`, `website/src/pages/index.astro`, `website/tests/smoke.spec.ts`

- [ ] **Step 1: Extend content.ts**

```ts
export const cli = [
  "uvx agentcairn                      # on-demand MCP server",
  "cairn ingest --vault ~/vault        # distill recent sessions",
  "cairn sweep  --vault ~/vault        # ingest + reindex",
  "cairn recall \"how did we fix auth?\"  # hybrid recall",
  "cairn reindex ~/vault               # rebuild from Markdown",
  "cairn doctor                        # health-check the index",
];
export const trust = [
  { k: "Redaction before write", v: "regex + entropy + URL-credential" },
  { k: "Localhost-only MCP", v: "READ_ONLY queries, no exposed ports" },
  { k: "No telemetry", v: "nothing phones home" },
  { k: "Index outside the vault", v: "the .duckdb cache is never synced" },
];
export const roadmap = {
  done: ["Transcript ingestion → redaction → Markdown → DuckDB", "MCP server + CLI", "Reproducible benchmark harness"],
  shipped: ["Reranker on by default", "Ollama embedding tier", "Bi-temporal validity", "nomic default embedder"],
  next: ["In-memory HNSW", "Obsidian plugin", "MotherDuck cloud sync"],
};
export const priorArt = [
  { name: "basic-memory", note: "Markdown-as-memory + rebuildable index" },
  { name: "Simon Späti — Obsidian RAG on DuckDB", note: "SQL hybrid search over a vault" },
  { name: "DuckDB VSS + FTS", note: "the embedded engine" },
  { name: "LongMemEval / LoCoMo", note: "the benchmarks" },
];
```

- [ ] **Step 2: The four components**

`website/src/components/Quickstart.astro`:
```astro
---
import Section from "./Section.astro";
import { cli } from "../lib/content";
---
<Section id="quickstart" eyebrow="Quickstart">
  <pre class="prose-measure font-mono text-[13px] leading-[1.7] bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl p-5 overflow-x-auto">{cli.join("\n")}</pre>
</Section>
```

`website/src/components/TrustSecurity.astro`:
```astro
---
import Section from "./Section.astro";
import { trust } from "../lib/content";
---
<Section id="trust" eyebrow="Trust & security">
  <div class="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
    {trust.map((t) => (
      <div>
        <p class="font-mono text-[12px] text-[var(--color-ink)] mb-1">{t.k}</p>
        <p class="font-serif text-[14px] text-[var(--color-ink-muted)]">{t.v}</p>
      </div>
    ))}
  </div>
</Section>
```

`website/src/components/Roadmap.astro`:
```astro
---
import Section from "./Section.astro";
import { roadmap } from "../lib/content";
const cols = [["Done", roadmap.done], ["v1.1 shipped", roadmap.shipped], ["Next", roadmap.next]] as const;
---
<Section id="roadmap" eyebrow="Roadmap & honest status">
  <div class="grid sm:grid-cols-3 gap-8">
    {cols.map(([title, items]) => (
      <div>
        <p class="font-sans font-medium text-[15px] mb-2">{title}</p>
        <ul class="font-serif text-[15px] text-[var(--color-ink-muted)] space-y-1">{items.map((i) => <li>{i}</li>)}</ul>
      </div>
    ))}
  </div>
</Section>
```

`website/src/components/PriorArt.astro`:
```astro
---
import Section from "./Section.astro";
import { priorArt } from "../lib/content";
---
<Section id="prior-art" eyebrow="Prior art & thanks">
  <ul class="prose-measure font-serif text-[16px] text-[var(--color-ink-muted)] space-y-2">
    {priorArt.map((p) => <li><span class="text-[var(--color-ink)]">{p.name}</span> — {p.note}</li>)}
  </ul>
</Section>
```

- [ ] **Step 3: Add to page + assert + commit**

Add the four components to `index.astro` in spec order (Quickstart → TrustSecurity → Roadmap → PriorArt, before `<Footer/>`). Append to `smoke.spec.ts`:
```ts
test("quickstart + roadmap + prior art render", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("cairn doctor")).toBeVisible();
  await expect(page.getByText("nomic default embedder")).toBeVisible();
  await expect(page.getByText("basic-memory")).toBeVisible();
});
```
Run `npm test` (PASS), then commit:
```bash
git add website/src/components/Quickstart.astro website/src/components/TrustSecurity.astro website/src/components/Roadmap.astro website/src/components/PriorArt.astro website/src/lib/content.ts website/src/pages/index.astro website/tests/smoke.spec.ts
git commit -m "feat(website): quickstart, trust, roadmap, prior-art sections"
```

---

## Task 10: Accessibility, performance, OG image & favicon

**Files:**
- Create: `website/tests/a11y.spec.ts`, `website/public/favicon.svg`, `website/public/og.png`

- [ ] **Step 1: Add the axe accessibility test**

`website/tests/a11y.spec.ts`:
```ts
import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
test("no critical or serious accessibility violations", async ({ page }) => {
  await page.goto("/");
  const results = await new AxeBuilder({ page }).analyze();
  const bad = results.violations.filter((v) => ["critical", "serious"].includes(v.impact ?? ""));
  expect(bad, JSON.stringify(bad.map((v) => v.id))).toEqual([]);
});
```

- [ ] **Step 2: Run axe and fix what it flags**

Run `npm test -- a11y.spec.ts`. Resolve any critical/serious violations (likely candidates: color contrast on `--color-ink-faint` text — bump to `--color-ink-muted` where flagged; missing `lang`; nav `<a>` discernible names). Re-run until the test passes.

- [ ] **Step 3: Add favicon + OG image**

`website/public/favicon.svg` (the rock glyph):
```xml
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><text y="26" font-size="26">🪨</text></svg>
```
For `website/public/og.png` (1200×630): export a static render of the hero diagram on the `--color-bg` ground with the H1. Run `npm run build`, open `dist/` in a browser at 1200×630, screenshot to `public/og.png`. (If automating later, a Playwright screenshot script can regenerate it.)

- [ ] **Step 4: Performance sanity + commit**

Run `npm run build` and confirm: only the two islands ship JS (`dist/_astro/*.js` limited to React + Motion for the islands); HTML is otherwise static. Spot-check no layout shift by confirming Fontsource `@font-face` is present in the built CSS.
```bash
git add website/tests/a11y.spec.ts website/public/favicon.svg website/public/og.png
git commit -m "feat(website): a11y test, favicon, OG image + perf sanity"
```

---

## Task 11: Deploy to Cloudflare Pages

**Files:**
- Create: `.github/workflows/site.yml`, `website/README.md`

- [ ] **Step 1: Site README**

`website/README.md`:
```markdown
# agentcairn website

Static Astro site for agentcairn (the landing page at agentcairn.dev).

## Develop
```bash
cd website && npm install && npm run dev   # http://localhost:4321
```

## Build & test
```bash
npm run build      # -> dist/
npm run check      # astro/TS check
npm test           # Playwright e2e (smoke, reduced-motion, a11y)
```

## Deploy
Cloudflare Pages, project root `website/`, build `npm run build`, output `dist/`.
Custom domain `agentcairn.dev`. CI deploys on push to `main` (`.github/workflows/site.yml`).
```

- [ ] **Step 2: GitHub Action (build + deploy)**

`.github/workflows/site.yml`:
```yaml
name: site
on:
  push:
    branches: [main]
    paths: ["website/**", ".github/workflows/site.yml"]
  pull_request:
    paths: ["website/**"]
jobs:
  build:
    runs-on: ubuntu-latest
    defaults: { run: { working-directory: website } }
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20", cache: "npm", cache-dependency-path: website/package-lock.json }
      - run: npm ci
      - run: npm run build
      - run: npx playwright install --with-deps chromium
      - run: npm test
      - name: Deploy to Cloudflare Pages
        if: github.ref == 'refs/heads/main'
        uses: cloudflare/wrangler-action@v3
        with:
          apiToken: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          accountId: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
          command: pages deploy dist --project-name=agentcairn
```
> Requires repo secrets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`, and a Cloudflare Pages project named `agentcairn` with `agentcairn.dev` attached. These are set once in the Cloudflare dashboard / GitHub settings (manual, out-of-band).

- [ ] **Step 3: Final full-suite gate + commit**

Run (from `website/`): `npm run check && npm run build && npm test` — Expected: all PASS.
```bash
git add .github/workflows/site.yml website/README.md
git commit -m "ci(website): build + deploy to Cloudflare Pages"
```

---

## Self-review (against the spec)

- **§1 goal / comprehension-led, no end CTA:** Tasks 4–9 order the page as an argument; Footer (Task 3) has links only. ✓
- **§3 typography (serif body / sans heading / mono):** Task 2 tokens + `h1/h2/h3` sans rule + `.eyebrow` mono. ✓
- **§4 color (light-only tokens):** Task 2 `@theme`. ✓
- **§5 layout (680/1100 widths, 96px rhythm):** `Section`/`Prose` (Task 6), `--spacing-section`. ✓
- **§6 motion (Motion lib, easeOutExpo, reduced-motion):** Tasks 5 & 8 islands + global CSS (Task 2). ✓
- **§7 anatomy (11 sections, benchmark mid-page):** Tasks 4–9 cover hero→prior-art; Footer Task 3. ✓
- **§8.1 hero animation / §8.2 demo:** Tasks 5 & 8. ✓
- **§9 stack (Astro/Tailwind v4/Fontsource/Motion/Cloudflare):** Tasks 1–2, 11. ✓
- **§10 a11y & perf:** Task 10 (axe, reduced-motion, zero-CLS fonts, island-only JS). ✓
- **§11 content from README (nomic numbers):** `content.ts` (Tasks 3,6,7,9) uses shipped facts incl. nomic. ✓
- **§13 open item — in-repo `website/`:** confirmed (this plan builds in `website/`). ✓

No placeholders; component/prop names (`Section`, `Prose`, `content.ts` exports) are consistent across tasks.
```
