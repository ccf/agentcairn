# Website SEO — Phase 2 (keyword-targeted content pages)

**Status:** Approved (2026-06-16)
**Affects:** `website/` only — 5 new `.astro` pages under `src/pages/`, a new `ContentPage.astro` layout, footer/content additions in `src/lib/content.ts`, optional `FAQPage` JSON-LD, tests. Builds on Phase 1 (sitemap/robots/canonical/OG/JSON-LD already shipped). No change to `src/cairn`.

## Problem

Phase 1 made the site fully indexable, but it's still **one page** = one URL. Search ranking comes from **dedicated, separately-indexable pages** that each answer a specific query. Phase 2 adds five keyword-targeted pages (three use-case, one concept, one comparison) so agentcairn can rank for the queries people actually search, with internal linking that strengthens the whole site.

## Current state

Astro static, single page (`index.astro` → `Base.astro`), hand-built `.astro` components reading from `src/lib/content.ts`; anchor-based nav; no content collections. Phase 1 head (canonical, OG/Twitter, `WebSite`+`SoftwareApplication` JSON-LD, `@astrojs/sitemap`, robots) is in place and **per-page-aware** (Base.astro derives canonical/OG from `Astro.url`/`Astro.site` and accepts `title`/`description` props).

## Goal / decisions (brainstorm)

- **5 routed pages** (slugs keyword-targeted), each separately indexable, auto-added to the sitemap.
- **Content model:** plain `.astro` pages + a shared `ContentPage.astro` layout (reuses `Nav`/`Footer`/`Section`/`Prose`). No content collections (overkill for 5 pages).
- **Truthful content**, sourced from our own docs (README, `content.ts` differentiators, CLAUDE.md). The comparison page's competitor claims are **research-grounded, conservative, fair, and user-verified before publish**.
- **Internal linking** via a footer "Guides" group + contextual in-page links — itself a ranking signal.

## Page set

| URL | `<title>` | `<meta description>` (~150 chars) | H1 | Primary intent/keywords |
|---|---|---|---|---|
| `/claude-code-memory` | Persistent Memory for Claude Code — agentcairn | Give Claude Code durable, local memory that survives every session — captured and recalled as Markdown you own. Install in one command. | Persistent memory for Claude Code | claude code memory, claude code remember context, claude code persistent memory |
| `/cursor-memory` | Long-Term Memory for Cursor — agentcairn | Add durable cross-session memory to Cursor. agentcairn recalls prior decisions and stores them as Markdown you control — local, free, open-source. | Long-term memory for Cursor | cursor memory, cursor ai memory, cursor remember context |
| `/obsidian-ai-memory` | AI Agent Memory in Your Obsidian Vault — agentcairn | Your coding agent's memory as plain Markdown in an Obsidian vault you own — readable, editable, and the source of truth (not a one-way export). | Your AI agent's memory, in an Obsidian vault | obsidian ai memory, obsidian agent memory, obsidian llm memory |
| `/agent-memory` | Long-Term Memory for AI Coding Agents — agentcairn | What agent memory is, why coding agents forget, and how to add durable recall — local-first, with your data as the source of truth. | Long-term memory for AI coding agents | agent memory, ai agent memory, llm long-term memory |
| `/alternatives` | agentcairn vs Mem0, Letta, Zep & basic-memory | How agentcairn compares to Mem0, Letta, Zep, and basic-memory: local Markdown vault vs cloud DB, source-of-truth vs export, daemonless. | agentcairn vs other agent-memory tools | mem0 alternative, agent memory tools, letta alternative |

(Titles ≤ ~60 chars where possible; descriptions ≤ ~155. Implementer tunes length to avoid SERP truncation.)

## Architecture

### A. `ContentPage.astro` layout (`src/layouts/`)

A thin layout wrapping `Base.astro` for the marketing/content pages, so structure + CTA are consistent and DRY:
- Props: `title`, `description` (passed through to `Base`), plus an optional `faq` array (for FAQ JSON-LD — see §D).
- Renders: `<Nav />`, a `<main id="main">` with a back-to-home link ("← agentcairn"), a `<slot />` for page body (built from `Section`/`Prose`), a shared **CTA block** (the `cairn` install one-liner + GitHub link, reusing the Quickstart/`CopyButton` styling), and `<Footer />`.
- Each page sets its own `title`/`description`; `Base.astro` (Phase 1) turns those into the unique canonical + OG/Twitter automatically.

### B. The five pages (`src/pages/*.astro`)

Each: one **H1** (the table above), a 1–2 sentence intro answering the query, **2–4 sections**, the shared CTA. ~400–700 words, truthful. Content outlines:

- **`/claude-code-memory`** — Problem: Claude Code starts every session cold; context is lost. How agentcairn fixes it: the Claude Code **plugin** (recall-at-start, capture-at-end) + MCP `recall`/`remember`; sessions are distilled to your vault out-of-band. Your memory is **Markdown you own** (edit by hand; survives uninstall). CTA: `cairn install claude-code`.
- **`/cursor-memory`** — Problem: Cursor doesn't carry decisions across sessions. How: `cairn install cursor` writes the MCP server **and** installs the recall/remember skill; Cursor sessions are ingested into the vault; recall surfaces prior work. Ownership/portability angle. CTA: `cairn install cursor`.
- **`/obsidian-ai-memory`** — Lead with the distinctive wedge: your agent's memory **is** an Obsidian vault — readable/editable Markdown + `[[wikilinks]]`, **source of truth, not an export**; the agentcairn Obsidian plugin surfaces it (list + provenance + currency). Hand-edit a fact → the agent honors it. CTA: install + the Obsidian plugin link.
- **`/agent-memory`** (concept/educational) — What "agent memory" means (capture → recall → consolidation); why agents forget; the two architectures (cloud memory-DB vs local-first vault); what to look for (ownership, portability, recall quality, non-lossiness); how agentcairn embodies the local-first approach. Ends with a short **FAQ** (3–5 Q&As) → FAQ JSON-LD. Links to the use-case + alternatives pages.
- **`/alternatives`** (landscape comparison) — see §C.

### C. Comparison page (`/alternatives`) — content + accuracy process

- **Comparison dimensions** (table columns/rows): storage model (**local Markdown vault** vs **cloud/hosted DB**), **source of truth vs one-way export**, **daemon vs daemonless / external DB**, retrieval (hybrid BM25+vector, graph), data **ownership & portability**, **secret redaction**, license/openness.
- **Tools covered:** Mem0, Letta (MemGPT), Zep, basic-memory. One fair 2–4 sentence blurb each.
- **Honest "when *not* to choose agentcairn"** section (e.g. you want a hosted multi-tenant SaaS, managed infra, or non-coding-agent use). Builds trust + avoids overclaiming.
- **Accuracy process (REQUIRED):** during implementation, do focused **web research per tool** to ground every claim; frame around **durable architectural differences**, not volatile feature lists; keep competitor characterizations **conservative and fair** (no FUD). The page is drafted, then **the user verifies competitor facts before merge** (explicit gate in the plan). Cite/link each tool's official site.
- Ends with a short **FAQ** → FAQ JSON-LD.

### D. FAQ JSON-LD

On `/agent-memory` and `/alternatives`, add a `FAQPage` JSON-LD block built from the page's `faq` array (`ContentPage` serializes it with `set:html`, like Phase 1's blocks). Only include Q&As actually shown on the page (Google requires the markup to match visible content).

### E. Internal linking / IA

- **Footer:** add a "Guides" group in `content.ts` `footer` linking all 5 pages; render in `Footer.astro`.
- **In-page:** each page links to **Quickstart (home `#quickstart`) + GitHub** (CTA) and **1–2 sibling pages** (e.g. use-case pages link to `/agent-memory` and `/alternatives`; `/agent-memory` links to the use-case pages).
- Home page is unchanged structurally (stays the hub); optionally the footer "Guides" group also shows on home (it will, since Footer is shared).

## Data flow

```
astro build
  → renders index + 5 new pages, each via ContentPage → Base (unique title/description → unique canonical/OG)
  → @astrojs/sitemap auto-includes all 6 URLs in sitemap-0.xml
  → FAQ JSON-LD emitted on /agent-memory and /alternatives
post-merge: Cloudflare redeploys; pages are crawlable + internally linked + in the sitemap
```

## Error handling / correctness

- Unique `title`/`description` per page (no duplicate-title SEO penalty); canonical is per-URL via Phase 1's `Base.astro` (verified absolute).
- FAQ JSON-LD built from JS objects + `JSON.stringify` + `set:html` (valid, unescaped); FAQ markup mirrors visible Q&As.
- No thin/duplicate content: each page is distinct prose for a distinct intent (not boilerplate clones).
- Comparison claims: conservative, sourced, **user-verified** — the one place factual error is plausible, gated on human review.

## Testing / verification

- `npm run build` clean; `dist/` contains `claude-code-memory/index.html`, `cursor-memory/index.html`, `obsidian-ai-memory/index.html`, `agent-memory/index.html`, `alternatives/index.html`.
- All 6 URLs present in `dist/sitemap-0.xml`.
- Each new page: exactly one `<h1>`, a unique `<title>` and `<meta description>`, an absolute `rel=canonical` matching its path.
- `/agent-memory` and `/alternatives`: a `FAQPage` `ld+json` block that `JSON.parse`s and whose questions match on-page text.
- Playwright `content-pages.spec.ts`: each route 200s, has the expected H1, and is listed in the sitemap; a11y spec still green across new pages (run axe on at least one content page); no broken internal links (CTA/sibling links resolve).
- Manual: titles/descriptions within SERP length; comparison facts **verified by the user**.

## File-by-file

| File | Change |
|---|---|
| `website/src/layouts/ContentPage.astro` | **new** — shared content-page layout (Nav + back-link + slot + CTA + Footer; optional FAQ JSON-LD) |
| `website/src/pages/claude-code-memory.astro` | **new** — use-case page |
| `website/src/pages/cursor-memory.astro` | **new** — use-case page |
| `website/src/pages/obsidian-ai-memory.astro` | **new** — use-case page |
| `website/src/pages/agent-memory.astro` | **new** — concept page + FAQ |
| `website/src/pages/alternatives.astro` | **new** — comparison page + FAQ (user-verified facts) |
| `website/src/lib/content.ts` | add footer "Guides" links + any shared copy |
| `website/src/components/Footer.astro` | render the Guides group |
| `website/tests/content-pages.spec.ts` | **new** — routes/H1/sitemap/FAQ-JSON-LD checks |

## Non-goals

- A blog / ongoing content cadence; per-competitor "vs X" pages; Codex/Antigravity use-case pages (all later).
- Analytics, backlinks, outreach (Phase 3).
- Restructuring the home page or existing components beyond the footer addition.
- Any change outside `website/`.

## Open questions

None. (Comparison competitor facts are resolved during implementation via research + the user-verification gate.)
