# agentcairn Website — Design Spec

**Date:** 2026-06-10
**Status:** Approved direction; pending spec review → implementation plan
**Scope:** A single marketing/landing page at `agentcairn.dev`, built so the design system
extends cleanly to docs/blog later without rework.

---

## 1. Goal & strategy

**Primary goal — comprehension.** agentcairn's wedge (the *inversion*: your Markdown vault is
the source of truth, the index is a disposable cache) is genuinely novel; most visitors have
not seen it. The page's job is to make that idea *click*, then pay off with a dead-simple
install. The narrative is an **argument, not a feature list** — thesis first, methodology last,
and — like the reference (`cognition.ai/blog/frontier-code`) — **no salesy CTA at the end.**

- **Primary action:** copy `uvx agentcairn` (hero) — activation is the payoff, not the lead.
- **Persistent secondary:** GitHub repo (stars/contrib).
- **Voice:** one honest open-source developer who states limitations out loud; candor *is* the
  marketing. Confident, understated, technical-but-clear.

**Success criteria:** a developer who has never heard of agentcairn understands the inversion
within the first screen, reaches the install line, and trusts the project (honest benchmarks,
security posture, real artifacts) — on a page that loads near-instantly and reads like an essay.

---

## 2. Aesthetic north star

**A research journal, not a SaaS landing page.** Calm, light, paper-like, text-first, color
used only as *signal*. The through-line from the reference is the **serif-body / sans-heading
inversion** on near-white ground with a single near-black ink. Running prose reads like an essay
you'd trust; headings stay tight and quiet; the only "loud" moments are honest data
visualizations and a real CLI artifact. Restraint is the brand — whitespace and typographic
contrast do the work, never decoration.

---

## 3. Typography system

Inversion is the move: **serif body, sans headings, mono for everything `cairn`.** Self-hostable
open faces that evoke the reference's (proprietary) STK Bureau Serif / NB International Pro:

| Role | Font (self-hosted, OFL) | Fallback stack |
|------|-------------------------|----------------|
| **Body (serif)** | **Newsreader** | `Newsreader, Georgia, "Times New Roman", serif` |
| **Headings (sans)** | **Geist Sans** | `"Geist Sans", system-ui, sans-serif` |
| **Mono** | **Geist Mono** | `"Geist Mono", ui-monospace, "SF Mono", monospace` |

- **Scale — discrete steps, NO fluid `clamp()`; one bump at the `md` (768px) breakpoint:**
  - Body: `16px` → `18px` md, line-height `1.6`
  - Eyebrow/label (uppercase mono): `12px`, tracking `+0.05em`
  - H3 / section sub: `20px` → `22px`, line-height `1.3`
  - H2 / section: `24px` → `28px`, line-height `1.25`
  - H1 / hero: `32px` → `38px`, line-height `1.15`
- **Weights — narrow, NO 700:** serif `400` (body) + `600` (emphasis/serif headings); sans `400`
  + `500`; optional sans `300` for large quiet display.
- **Tracking:** sans headings `-0.02em` (hero) / `-0.01em` (sections); serif body `0`; uppercase
  eyebrows `+0.05em`.
- **Measure:** prose column `680px` (~72ch); outer content frame `1100px`.
- **Emphasis device:** *italic single words* in prose, never bold or color.
- **Loading:** self-host via Fontsource with metric-matched fallback `@font-face`
  (`ascent-override` / `descent-override` / `size-adjust`) for **zero CLS**.

---

## 4. Color system

**Light only for v1.** Near-black ink on near-white paper; secondary text as alpha steps of the
ink (not separate grays); hairline borders; two accents reserved for links / CTAs / data only.

```css
:root {
  --bg:           #FFFFFF;
  --surface:      #FAFAF8;                 /* faint warm code/card panel */
  --ink:          #191919;                 /* headings + body (NOT pure black) */
  --ink-muted:    rgba(25,25,25,0.56);     /* sub-labels, captions */
  --ink-faint:    rgba(25,25,25,0.40);     /* metadata, byline */
  --border:       rgba(0,0,0,0.10);
  --border-faint: rgba(0,0,0,0.05);
  --accent:       #317CFF;                 /* links, primary CTA, active, [[wikilinks]] */
  --accent-warm:  #E89B3C;                 /* secondary accent, chart series 2 */
  /* data-viz categorical (charts only) */
  --c-teal:#15AABF; --c-purple:#7048E8; --c-gold:#F0B429; --c-magenta:#C2255C;
  --tint-blue:#C9DCFF; --tint-tan:#F3DCB5;
}
```

**Stance:** accents stay out of backgrounds, nav, and large surfaces — color is signal, prose
stays black-on-white. Dark mode is a later token swap, not a redesign (out of scope for v1).

---

## 5. Layout & spacing

- **Widths:** prose `680px`; content frame `1100px`; full-bleed reserved for *one* moment (the
  signature hero animation). Charts/diagrams stay inside the prose measure — no full-bleed media.
- **Spacing scale (4px base):** `4, 8, 12, 16, 24, 32, 48, 64, 96px`.
- **Section rhythm — asymmetric (this is what makes sections breathe):** `96px` top padding
  between major sections; H2 `margin-top:64px; margin-bottom:16px` (more above than below);
  paragraph `margin-bottom:24px`.
- **Whitespace philosophy:** wide empty side gutters, single centered measure, no
  cards-everywhere. Structure comes from whitespace + serif/sans contrast, not boxes and rules.
- **Grid:** single column for prose; a simple 2-/3-col CSS grid only for the differentiator grid
  and the stat-callout row.

---

## 6. Motion language

Functional motion, **not spectacle** — exactly matching the reference (SVG charts that draw in +
one interactive widget; no WebGL/Lottie/parallax/scroll-jacking).

- **What animates:** only (1) the signature hero diagram, (2) the interactive demo widget,
  (3) data-viz on viewport entry. **Prose, nav, and chrome stay static.**
- **Library:** **Motion** (`motion`, formerly framer-motion) for React reveals; plain CSS
  transitions for hover. No GSAP, no three.js.
- **Reveal pattern:** `initial={{opacity:0, y:20}}` → `whileInView={{opacity:1, y:0}}`,
  `viewport={{ once:true, margin:"-10% 0px" }}`.
- **Easing token (define once, reuse):** `cubic-bezier(0.16, 1, 0.3, 1)`.
- **Durations:** content reveals `0.5s`; hover/UI `0.25s`; chart/diagram draw-in `0.8–1.0s`.
- **Chart technique:** bars `transform-origin:bottom; scaleY 0→1`; lines `stroke-dasharray` +
  `stroke-dashoffset` draw-on; graph nodes fade then connectors draw with `0.06s` stagger.
- **Restraint rules:** `once:true` (no replay flicker); **`prefers-reduced-motion`** disables
  transforms and collapses durations to `0.01s` (renders final state instantly); no autoplay
  video; no scroll-jacking.

---

## 7. Page anatomy

Ordered as an argument. **Hero (locked):**

> **H1 —** "Most agent memory makes a database the source of truth. We made it your files."
> **Subhead —** "agentcairn inverts the stack: human-readable Markdown with `[[wikilinks]]` is
> the truth, and a rebuildable DuckDB index gives your agent fast hybrid retrieval. Hand-edit a
> fact in Obsidian and the agent picks it up."
> Byline "By Charles C. Figueiredo · Apache-2.0" + dotted date. Single primary action:
> `uvx agentcairn` (copy-to-clipboard) + ghost "Read the spec →". Visual: the signature
> animation (§8).

| # | Section | Message | Visual |
|---|---------|---------|--------|
| 1 | **Hero** | The inversion: vault is truth, index is disposable. | Signature animation (§8) |
| 2 | **The inversion / the problem** | Mem0 & Zep are cloud DBs; Letta & agentmemory are DB-as-truth with files as export-only — agentcairn is the only one where the vault *is* the truth. | Small before/after diagram: "DB → files (export)" vs "files → index (cache)" |
| 3 | **Six differentiators** | vault-as-truth · disposable index · non-lossy by construction · redaction-before-write · free deterministic `[[wikilink]]` graph · daemonless / zero external DB. | 2×3 icon grid, one bold phrase + one sentence each |
| 4 | **How it works** | vault ⇄ rebuildable DuckDB index ⇄ MCP tools (`remember`/`recall`/`search`/`build_context`/`recent`); capture reads transcripts out-of-band → redacts → dedups → importance-gates → distills. | Inline SVG data-flow diagram |
| 5 | **Survives uninstall (the proof)** | `rm` the `.duckdb` index → `cairn reindex` → everything's back, zero data loss, because the truth was never in the database. | **Interactive staged widget** (§8.2) |
| 6 | **Honestly measured** | Reproducible LongMemEval-S + LoCoMo with ablations and caveats inline — no single headline number. | SVG table: hybrid+reranker recall@5 `0.662` / @10 `0.735` / MRR `0.608` vs hybrid-RRF & BM25 (FastEmbed `nomic`, the default); caveats as visible footnotes |
| 7 | **Quickstart / CLI** | Copy-pasteable: `uvx agentcairn`, `cairn ingest`, `cairn sweep`, `cairn recall`, `cairn reindex`, `cairn doctor`. | Mono code block in `--surface` panel, copy buttons |
| 8 | **Trust & security** | Redaction before every write (regex + entropy + URL-cred), localhost-only MCP, no telemetry, index kept out of the synced vault. | Compact 4-item strip, mono labels |
| 9 | **Roadmap & honest status** | v1 done; v1.1 shipped (reranker default, Ollama tier, bi-temporal validity); v2 (Obsidian plugin, MotherDuck sync). | Dated checklist, three columns |
| 10 | **Prior art & thanks** | Built on understood foundations. | Text list: basic-memory, Simon Späti's Obsidian-RAG-on-DuckDB, DuckDB VSS/FTS, LongMemEval, LoCoMo |
| 11 | **Footer** | Legal/links only — no marketing CTA. | Apache-2.0 · GitHub · author · install one-liner |

Benchmark numbers live **mid-page** (§6), not near the hero (decision D).

---

## 8. Signature visuals

### 8.1 Hero animation — "Markdown becomes a graph becomes a recall"

A single inline SVG (full-bleed within the `1100px` frame) that animates **once** on load and
tells the whole architecture in ~3 seconds:

1. **Left — the vault (truth).** A real Markdown note renders: frontmatter, a sentence, two
   `[[wikilinks]]` highlighted in `--accent`. Mono, `--surface` panel. *Static, always visible.*
2. **Middle — the index (disposable cache).** Arrows draw (`stroke-dashoffset`, `0.8s`) from the
   note into a DuckDB cylinder glyph; as they land, the two `[[wikilinks]]` *detach and become
   graph nodes* (circles fade in, `0.06s` stagger), then connector edges draw between them — the
   "markdown → deterministic graph" moment.
3. **Right — MCP recall.** A `cairn recall "…"` prompt appears, and a ranked, **cited** result
   card fades up (`y:20→0`, `0.5s`), the citation pointing back to the original note — closing
   the loop *files → index → answer → back to files.*

Subtle looping accent: every ~8s one connector edge pulses faintly (`opacity 0.4→1→0.4`) — the
only ambient motion on the page. `prefers-reduced-motion` renders the final state instantly. One
hand-authored SVG + Motion timeline; no chart library.

### 8.2 Interactive "survives uninstall" demo (decision B — in v1)

A button-driven, staged terminal widget (the single most memorable proof):
`rm ~/.../index.duckdb` → **Reindex** → `cairn reindex` streams progress → "restored, 0 facts
lost." Three stages, button-advanced, choreographed with the §6 motion tokens. Contained in one
React island; honors `prefers-reduced-motion` (shows final state, no typing animation).

---

## 9. Tech stack & hosting

- **Framework — Astro (static output).** Content-first, ships zero JS by default → near-instant
  loads (on-brand for local-first/fast). Prose in MDX. A **single React island** (`client:visible`)
  carries the hero animation + demo widget, so Motion ships only there. Docs/blog later = Astro
  content collections, same design system, no rewrite.
- **CSS — Tailwind v4 + `@tailwindcss/typography`.** `prose` with `max-width:680px` gives the
  centered editorial measure with minimal custom CSS; §4 tokens as CSS custom properties.
- **Fonts — self-hosted via Fontsource** (Newsreader, Geist Sans, Geist Mono) + metric-matched
  Arial fallbacks. Zero CLS.
- **Animation — Motion** in the single island only; CSS transitions elsewhere.
- **Hosting — Cloudflare Pages** (free, fast global edge). **Domain — `agentcairn.dev`** (HSTS
  preloaded → HTTPS-only, fitting a security-conscious local-first tool).
- **Repo:** standalone site (decision C), mirroring the README's *substance* — not badges/star
  chrome. Decision: live in a `website/` (or `site/`) directory of the agentcairn repo, deployed
  from there, to keep content close to the code it describes. *(Confirm at plan time: in-repo
  `website/` vs separate `agentcairn-site` repo.)*

---

## 10. Accessibility & performance

- **Motion:** every animation respects `prefers-reduced-motion` (final state, no transforms).
- **Contrast:** `--ink` on `--bg` and `--accent` on `--bg` meet WCAG AA for their text sizes;
  muted/faint inks used only for non-essential metadata at sizes that still pass AA.
- **Semantics:** real heading hierarchy, landmark regions, keyboard-operable copy buttons and the
  demo widget, visible focus states, `alt`/`aria` on the SVG diagrams (with a text equivalent).
- **Performance targets:** Lighthouse ≥ 95 across the board; zero CLS (font metric overrides);
  total JS budget kept to the single island; static HTML for everything else.

---

## 11. Content source

Mirror the README's substance with the *current* shipped facts, including the
**`nomic` default** benchmark numbers (vector-only now edges BM25). Sections 2–10 map directly to
existing README content; copy is re-voiced for the editorial register but makes **no claim not
already supported** in the repo/benchmarks. The honest caveats (graph-boost inert on chat logs;
QA judge not leaderboard-comparable) appear as visible footnotes, not buried.

---

## 12. Out of scope (v1) / future

- Docs section, blog/changelog (design system anticipates them; not built now).
- Dark mode (later token swap).
- Analytics/telemetry (intentionally none — matches product ethos).
- MotherDuck/Obsidian-plugin marketing (roadmap mention only).

---

## 13. Open items to confirm at plan time

1. Site location: in-repo `website/` directory (recommended) vs separate repo.
2. Exact OG/social-card image + favicon (derive from the 🪨 + signature diagram).
3. Whether the demo widget (§8.2) ships interactive in v1 or as an animated-but-non-interactive
   first pass if the island grows too large (fallback, not the plan).

No blocking unknowns — the design is complete enough to plan implementation.
