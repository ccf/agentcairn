# Website SEO Phase 2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add 5 keyword-targeted, separately-indexable content pages (3 use-case, 1 concept, 1 comparison) with a shared layout, internal linking, and FAQ structured data — so agentcairn ranks for the queries people search.

**Architecture:** Plain `.astro` pages under `website/src/pages/`, each via a new `ContentPage.astro` layout that wraps the Phase-1 `Base.astro` (unique `title`/`description` → unique canonical/OG per page). Footer gains a "Guides" group for internal linking. `@astrojs/sitemap` auto-includes the new routes.

**Tech Stack:** Astro 6 static, Playwright, npm. All work in `website/` (npm/npx, not uv).

**Reference:** Spec `docs/specs/2026-06-16-website-seo-phase2-design.md`. Branch `feat/website-seo-phase2`.

**Voice (match the existing site):** terse, technical, confident, concrete. Short declarative sentences. Backtick `code`/commands. No marketing fluff, no hype, no fabricated benchmarks. Truthful — every claim traceable to README / `content.ts` / CLAUDE.md.

**Exact install commands (use verbatim; do not invent):**
- Claude Code: `cairn install claude-code`
- Cursor: `cairn install cursor`
- On-demand server / general: `uvx agentcairn`, `cairn recall "…"`, `cairn doctor`
- (Pull anything else from `website/src/lib/content.ts` `install`/`quickstart` arrays — don't make up flags.)

---

## Task 1: `ContentPage` layout + footer "Guides" internal links

**Files:** Create `website/src/layouts/ContentPage.astro`; modify `website/src/lib/content.ts` (footer guides) + `website/src/components/Footer.astro`.

- [ ] **Step 1: Add the Guides links to `content.ts`**

Extend the `footer` object:
```ts
export const footer = {
  license: "Apache-2.0",
  copyright: "© 2026 Charles C. Figueiredo",
  definition:
    "a stack of stones raised to mark a trail or a place worth remembering, left for whoever comes next.",
  guides: [
    { label: "Memory for Claude Code", href: "/claude-code-memory" },
    { label: "Memory for Cursor", href: "/cursor-memory" },
    { label: "AI memory in Obsidian", href: "/obsidian-ai-memory" },
    { label: "What is agent memory?", href: "/agent-memory" },
    { label: "vs Mem0, Letta, Zep, basic-memory", href: "/alternatives" },
  ],
};
```

- [ ] **Step 2: Render the Guides group in `Footer.astro`**

Add a "Guides" list to the footer markup (match existing footer styling/classes), iterating `footer.guides` as `<a href={g.href}>{g.label}</a>`. Internal anchor links (relative `/...`), so Astro/Cloudflare serve them.

- [ ] **Step 3: Create `ContentPage.astro`**

```astro
---
import Base from "./Base.astro";
import Nav from "../components/Nav.astro";
import Footer from "../components/Footer.astro";
import Section from "../components/Section.astro";
const { title, description, faq } = Astro.props;
const faqLd = faq && faq.length
  ? {
      "@context": "https://schema.org",
      "@type": "FAQPage",
      mainEntity: faq.map((f) => ({
        "@type": "Question",
        name: f.q,
        acceptedAnswer: { "@type": "Answer", text: f.a },
      })),
    }
  : null;
---
<Base title={title} description={description}>
  <Nav />
  <main id="main">
    <p class="px-6 pt-6"><a href="/" class="text-[var(--color-ink-muted)] hover:underline">← agentcairn</a></p>
    <slot />
    <Section id="cta" eyebrow="Get started">
      <p class="font-serif text-[16px]">Install agentcairn into your agent in one command:</p>
      <pre class="font-mono text-[14px] mt-2"><code>cairn install claude-code</code></pre>
      <p class="mt-3"><a href="https://github.com/ccf/agentcairn" class="underline">GitHub</a> · <a href="/#quickstart" class="underline">Full quickstart</a></p>
    </Section>
  </main>
  <Footer />
  {faqLd && <script type="application/ld+json" set:html={JSON.stringify(faqLd)} slot="head" />}
</Base>
```
NOTE: verify how `Base.astro` accepts head-injected content. If `Base` doesn't support a `head` slot, instead pass `faq` INTO `Base.astro` (add an optional `faq` prop there and render the FAQ `ld+json` in its `<head>` next to the Phase-1 JSON-LD). Pick whichever the real `Base.astro` structure supports — the requirement is: **FAQ JSON-LD ends up inside `<head>`, valid, only when `faq` is provided.** Match the CTA block styling to the existing `Quickstart.astro`/`CopyButton.astro` look (reuse them if clean).

- [ ] **Step 4: Build to verify it compiles** (a real page consumes it in Task 2; for now just typecheck)

Run: `cd website && npm run build`
Expected: builds clean (no page uses ContentPage yet, so output unchanged).

- [ ] **Step 5: Commit**

```bash
git add website/src/layouts/ContentPage.astro website/src/lib/content.ts website/src/components/Footer.astro
git commit -m "feat(seo): ContentPage layout + footer Guides internal links"
```

---

## Task 2: Three use-case pages (Claude Code, Cursor, Obsidian)

**Files:** Create `website/src/pages/claude-code-memory.astro`, `cursor-memory.astro`, `obsidian-ai-memory.astro`.

Each uses `ContentPage` with the spec's `title`/`description`, one `<h1>`, the brief below rendered with `Section`/`Prose`, sibling links to `/agent-memory` + `/alternatives`, and a harness-specific install command in the CTA (override the default in the slot if needed). ~400–700 words, in the site voice, truthful.

- [ ] **Step 1: `claude-code-memory.astro`**

- `title`: "Persistent Memory for Claude Code — agentcairn"; `description` per spec.
- H1: **Persistent memory for Claude Code**
- Intro (1–2 sentences): Claude Code starts every session cold — agentcairn gives it durable memory that persists across sessions, stored as Markdown you own.
- Sections (truthful, from docs):
  - *The problem* — each Claude Code session loses prior context/decisions.
  - *How agentcairn works with Claude Code* — the Claude Code **plugin** recalls relevant memory at session start and captures at session end; the bundled MCP server exposes `recall`/`search`/`remember`; capture is out-of-band (`cairn sweep`) so nothing is lost. Install: `cairn install claude-code`.
  - *Your memory is yours* — distilled to a local Markdown vault (Obsidian-compatible); survives model upgrades, index rebuilds, even uninstalling the tool.
- CTA: `cairn install claude-code` + GitHub. Sibling links: `/agent-memory`, `/alternatives`.

- [ ] **Step 2: `cursor-memory.astro`**

- `title`: "Long-Term Memory for Cursor — agentcairn"; `description` per spec.
- H1: **Long-term memory for Cursor**
- Intro: Cursor doesn't carry decisions across sessions — agentcairn adds durable, local recall.
- Sections: *the problem* (cold sessions); *how* — `cairn install cursor` writes the MCP server **and** installs the recall/remember skill into Cursor; Cursor sessions are ingested into the vault out-of-band; recall surfaces prior work; *ownership* — Markdown vault you control, local + free + open-source.
- CTA: `cairn install cursor` + GitHub. Siblings: `/agent-memory`, `/alternatives`.

- [ ] **Step 3: `obsidian-ai-memory.astro`**

- `title`: "AI Agent Memory in Your Obsidian Vault — agentcairn"; `description` per spec.
- H1: **Your AI agent's memory, in an Obsidian vault**
- Intro: agentcairn stores your coding agent's memory as plain Markdown in an Obsidian vault you own — the **source of truth**, not a one-way export.
- Sections: *the wedge* — readable/editable Markdown + `[[wikilinks]]`; hand-edit a fact and the agent honors it; *the Obsidian plugin* — surfaces memory (list + provenance + currency) via the `agentcairn-obsidian` plugin; *how it stays in sync* — rebuildable DuckDB index reconciles to the vault. Link the plugin repo (`https://github.com/ccf/agentcairn-obsidian`).
- CTA: install + GitHub. Siblings: `/agent-memory`, `/alternatives`.

- [ ] **Step 4: Build + eyeball**

Run: `cd website && npm run build && ls dist/claude-code-memory/index.html dist/cursor-memory/index.html dist/obsidian-ai-memory/index.html`
Expected: all three built. Spot-check each has one `<h1>` and a unique `<title>`: `for p in claude-code-memory cursor-memory obsidian-ai-memory; do echo "$p:"; grep -o '<title>[^<]*' dist/$p/index.html; grep -c '<h1' dist/$p/index.html; done`

- [ ] **Step 5: Commit**

```bash
git add website/src/pages/claude-code-memory.astro website/src/pages/cursor-memory.astro website/src/pages/obsidian-ai-memory.astro
git commit -m "feat(seo): use-case pages — Claude Code, Cursor, Obsidian"
```

---

## Task 3: Concept page `/agent-memory` + FAQ JSON-LD

**Files:** Create `website/src/pages/agent-memory.astro`.

- [ ] **Step 1: Write the page**

- `title`: "Long-Term Memory for AI Coding Agents — agentcairn"; `description` per spec.
- H1: **Long-term memory for AI coding agents**
- Educational, intent = informational. Sections:
  - *Why coding agents forget* — context windows are ephemeral; each session starts cold.
  - *What "agent memory" is* — capture → recall → consolidation; durable facts surfaced when relevant.
  - *Two architectures* — cloud memory-DB (hosted, your data in their database) vs **local-first vault** (Markdown you own). Fair, neutral.
  - *What to look for* — ownership/portability, recall quality (hybrid retrieval), non-lossiness, redaction.
  - *How agentcairn does it* — local Markdown vault as source of truth + rebuildable DuckDB hybrid index; links to the use-case pages + `/alternatives`.
- **FAQ** (3–5 Q&As shown on the page) passed as `faq` to `ContentPage` → FAQ JSON-LD. Suggested Qs (answer truthfully, concise): "What is agent memory?", "How is local-first memory different from a cloud memory database?", "Does my agent's memory survive uninstalling the tool?", "Which agents does agentcairn support?". Each answer must match the visible on-page text.

- [ ] **Step 2: Build + verify FAQ JSON-LD**

```bash
cd website && npm run build
node -e "const h=require('fs').readFileSync('dist/agent-memory/index.html','utf8'); const m=[...h.matchAll(/<script type=\"application\/ld\+json\">([\s\S]*?)<\/script>/g)].map(x=>JSON.parse(x[1])); const faq=m.find(o=>o['@type']==='FAQPage'); if(!faq) throw new Error('no FAQPage ld+json'); console.log('FAQ Qs:', faq.mainEntity.length)"
```
Expected: prints the FAQ question count (≥3); all `ld+json` parse.

- [ ] **Step 3: Commit**

```bash
git add website/src/pages/agent-memory.astro
git commit -m "feat(seo): concept page /agent-memory with FAQ structured data"
```

---

## Task 4: Comparison page `/alternatives` (research + draft + USER-VERIFY gate)

**Files:** Create `website/src/pages/alternatives.astro`.

- [ ] **Step 1: Research each tool (ground every claim)**

Do focused web research on **Mem0**, **Letta (MemGPT)**, **Zep**, and **basic-memory** — official sites/docs/repos. For each, capture only **durable, architectural** facts: where memory is stored (cloud/hosted DB vs local files/DB), whether files are the source of truth or an export, hosted-service vs self-hosted/daemonless, retrieval approach, license. Record the source URL for each claim. Do **not** assert volatile feature specifics or anything unverified.

- [ ] **Step 2: Write the page**

- `title`: "agentcairn vs Mem0, Letta, Zep & basic-memory"; `description` per spec.
- H1: **agentcairn vs other agent-memory tools**
- Lead with the existing, user-authored framing (from `content.ts`): cloud-DB (Mem0/Zep) vs DB-with-files-as-export (Letta) vs **vault-as-source-of-truth** (agentcairn); basic-memory is the closest local-Markdown neighbor.
- **Comparison table** — rows = the tools + agentcairn; columns = storage model, source-of-truth vs export, daemonless / no external DB, retrieval (hybrid/graph), data ownership & portability, secret redaction, license. Fill conservatively from Step 1.
- One fair 2–4 sentence **blurb per tool**, linking its official site.
- **"When *not* to choose agentcairn"** — honest (hosted SaaS / managed infra / non-coding-agent multi-tenant use → other tools fit better).
- **FAQ** (e.g. "Is agentcairn a Mem0 alternative?", "Can I move my memory off agentcairn?") → FAQ JSON-LD.
- Siblings: link `/agent-memory` + the use-case pages.

- [ ] **Step 3: Build + verify**

```bash
cd website && npm run build && ls dist/alternatives/index.html
node -e "const h=require('fs').readFileSync('dist/alternatives/index.html','utf8'); [...h.matchAll(/<script type=\"application\/ld\+json\">([\s\S]*?)<\/script>/g)].forEach(x=>JSON.parse(x[1])); console.log('ld+json OK')"
```

- [ ] **Step 4: 🚦 USER-VERIFICATION GATE — do NOT merge before this**

Present the drafted competitor claims (the table + blurbs + source URLs) to the user for fact-check. Apply any corrections. Only after the user confirms the competitor facts are accurate and fair does this page proceed. (The controller surfaces the draft to the user; the implementer must not skip or self-approve this.)

- [ ] **Step 5: Commit (after user verification)**

```bash
git add website/src/pages/alternatives.astro
git commit -m "feat(seo): comparison page /alternatives (competitor facts user-verified)"
```

---

## Task 5: Tests + full verification

**Files:** Create `website/tests/content-pages.spec.ts`.

- [ ] **Step 1: Write the test** (mirror `tests/seo.spec.ts` / `smoke.spec.ts`)

```ts
import { test, expect } from "@playwright/test";

const PAGES = [
  { path: "/claude-code-memory", h1: /Persistent memory for Claude Code/i },
  { path: "/cursor-memory", h1: /memory for Cursor/i },
  { path: "/obsidian-ai-memory", h1: /Obsidian vault/i },
  { path: "/agent-memory", h1: /memory for AI coding agents/i },
  { path: "/alternatives", h1: /agentcairn vs/i },
];

for (const p of PAGES) {
  test(`${p.path} renders with one H1, unique title, canonical`, async ({ page }) => {
    const resp = await page.goto(p.path);
    expect(resp?.ok()).toBeTruthy();
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator("h1")).toContainText(p.h1);
    await expect(page).toHaveTitle(/agentcairn/i);
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
      "href", new RegExp(`https://agentcairn\\.dev${p.path}/?$`)
    );
  });
}

test("all content pages are in the sitemap", async ({ request }) => {
  const xml = await (await request.get("/sitemap-0.xml")).text();
  for (const p of PAGES) expect(xml).toContain(`https://agentcairn.dev${p.path}`);
});

test("FAQ structured data on concept + comparison pages", async ({ page }) => {
  for (const path of ["/agent-memory", "/alternatives"]) {
    await page.goto(path);
    const blocks = await page.locator('script[type="application/ld+json"]').allTextContents();
    const types = blocks.map((b) => JSON.parse(b)["@type"]);
    expect(types).toContain("FAQPage");
  }
});
```
(Adjust the `h1` regexes to the final copy. If trailing-slash canonical differs, match the built form.)

- [ ] **Step 2: Run the full suite**

Run: `cd website && npm run build && npx playwright test 2>&1 | tail -25`
Expected: new `content-pages` tests pass + existing `seo`/`a11y`/`reduced-motion`/`smoke` still green. Run axe on at least one content page (extend a11y spec or add one assertion) to confirm no new violations.

- [ ] **Step 3: Commit**

```bash
git add website/tests/content-pages.spec.ts
git commit -m "test(seo): content-page routes, canonical, sitemap, FAQ JSON-LD"
```

---

## Final verification

- [ ] `cd website && npm run build` clean; `dist/` has all 5 new `*/index.html`.
- [ ] All 6 URLs in `dist/sitemap-0.xml`.
- [ ] Each new page: one `<h1>`, unique `<title>`/`<meta description>`, absolute per-path canonical.
- [ ] `/agent-memory` + `/alternatives` carry valid `FAQPage` JSON-LD matching visible Q&As.
- [ ] `npx playwright test` all green (content-pages + seo + a11y + reduced-motion + smoke).
- [ ] **Comparison page facts user-verified** (Task 4 gate) before merge.
- [ ] Internal links resolve (footer Guides + in-page CTAs/siblings).
- [ ] **Post-merge (user):** Cloudflare redeploys; in Search Console, confirm the new URLs get discovered (sitemap already submitted in Phase 1) and request indexing for the key pages; optionally run `/agent-memory` + `/alternatives` through the Rich Results Test (FAQ).

## Self-Review (during planning)

- **Spec coverage:** ContentPage layout + footer Guides (T1), 3 use-case pages (T2), concept + FAQ (T3), comparison + research + user-verify gate (T4), tests incl. sitemap/canonical/FAQ (T5). Per-page titles/descriptions/H1s match the spec table. Non-goals (blog, per-competitor pages, Codex/Antigravity, analytics) untouched.
- **Consistency:** every page routes through `ContentPage` → `Base` (Phase-1 canonical/OG); `faq` prop flows page → ContentPage → FAQ JSON-LD; footer `guides` added in T1 consumed by Footer + matches the 5 routes.
- **Accuracy:** install commands quoted from `content.ts`; competitor claims gated behind research + explicit user verification (T4 Step 4). No fabricated metrics; voice/source guidance up top.
- **Placeholders:** page briefs are content specs (title/description/H1/sections/points/CTA/links) — prose is written to the brief at execution in the site voice; this is the right granularity for content work, not a placeholder.
- **Stack note:** all `website/` work uses npm/npx; FAQ-JSON-LD-in-`<head>` mechanism must be verified against the real `Base.astro` slot support (T1 Step 3 note).
