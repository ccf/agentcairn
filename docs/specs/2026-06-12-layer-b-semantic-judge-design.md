# Layer B — semantic memory-worthiness judge — Design

**Status:** Approved (brainstorm) — 2026-06-12
**Scope:** The semantic quality layer deferred from the structural-ingestion design (`2026-06-11-structural-ingestion-design.md`). Layer A guarantees candidates are genuinely human-authored; Layer B judges whether an authored turn is a *durable memory* or ephemeral chatter.

## Problem

After the 0.7.x rebuild, the vault's 69 memories split roughly **60% durable** (decisions, preferences, lessons, pivots) / **40% ephemeral process chatter** ("I reopened and merged pr12", "did we actually push the website fix?", "production branch is set to main…"). Both classes are authored, long, and marker-laden, so the deterministic `importance.py` heuristic (length + marker words) cannot separate them — the discriminator is semantic. Separately, `ExtractiveDistiller` titles are an 80-char mid-word chop (the "…malformed. Ca" bug) that PyYAML then folds across lines.

## Decisions (locked in brainstorm)

1. **Mechanism:** embedding-prototype judge as the **default** tier (local, free, shipped deps), plus an **opt-in LLM judge** tier for maximum quality. Richer text heuristics rejected (the whack-a-mole shape Layer A eliminated); Claude-Code-agent-as-judge deferred.
2. **Semantics:** the judge produces a durability score that **combines** with the heuristic into the final `importance`; same 0.5 drop threshold as today; survivors get the real score in frontmatter. (Keep-but-demote rejected — re-bloats the vault; judge-replaces-heuristic rejected — loses the deterministic floor.)
3. **LLM tier scope: full distillation** — durability + a descriptive title + a crisp restatement of the body, so memory quality rides model improvements. Non-lossy reconciliation: the **verbatim text is retained in the note** (a `source:` pointer is insufficient — transcripts age out, as the rebuild proved).
4. **Blocking model:** synchronous within the CLI process (daemonless — no queue), bounded by a single batched call + hard timeout + silent degradation, and **detached at the plugin-hook layer** so session teardown never waits. (Two-phase `reflect` rejected: mutates written notes and relocates the daemon problem.)

## Architecture

### New module: `src/cairn/ingest/judge.py`

```python
@dataclass(frozen=True)
class Judgment:
    durability: float          # 0..1
    title: str | None = None   # LLM tier only
    distilled: str | None = None  # LLM tier only

class Judge(Protocol):
    def judge(self, texts: list[str]) -> list[Judgment]: ...
```

Resolution (`resolve_judge()`), in order:

- **Tier 2 — `LLMJudge`** when `CAIRN_JUDGE=anthropic` and `ANTHROPIC_API_KEY` is set. **One batched request per ingest run** covering *all* new (post-dedup) candidates across all transcripts: a JSON-structured prompt returning `{durability, title, distilled}` per candidate. Model via `CAIRN_JUDGE_MODEL` (default: the current cheap/fast Anthropic tier, e.g. `claude-haiku-4-5`). Hard timeout `CAIRN_JUDGE_TIMEOUT` (default 10s). **Any failure — missing key, timeout, HTTP error, malformed/short response — silently degrades to Tier 1** and is counted in the report; ingestion never blocks on the LLM.
- **Tier 1 — `EmbeddingJudge`** (default) when an embedder is available: two curated in-code prototype sets (~10 *durable* exemplars: decisions/preferences/lessons/pivots; ~10 *ephemeral* exemplars: PR coordination, status checks, deploy chatter). `durability = normalized margin of mean-cos(text, durable) − mean-cos(text, ephemeral)`, mapped to [0,1]. Uses the already-shipped FastEmbed nomic model — no new deps, no key, works in every host. Score only.
- **Tier 0 — `None`** (degraded floor): no judge; heuristic-only — exactly today's behavior. Recall is never silently dead and ingestion never hard-requires a model.

### Score combination (`pipeline.py`)

```
tier 1/2: importance = clamp01(0.5 * heuristic_score + 0.5 * durability)
tier 0:   importance = heuristic_score
```

Same `KEEP_THRESHOLD = 0.5` drop semantics; the combined score is what lands in frontmatter. Judging happens **after dedup** (never pay for repeats) and **before the gate**.

### Note format

LLM-tier survivors:

```markdown
- [context] <distilled crisp fact> #ingested
- [verbatim] <original redacted text>
```

Title = LLM's descriptive title. Other tiers keep today's single verbatim `[context]` line. **Dedup/content hash stays on the redacted verbatim text** — note identity is stable under LLM nondeterminism; ledger and idempotency semantics untouched. Verbatim-in-note also enables a future `cairn redistill` (re-derive titles/distillations with a better model) without touching transcripts.

### Title fix (all tiers)

`ExtractiveDistiller` truncates titles at a **word boundary** ≤80 chars with a trailing `…` (no mid-word chops), and note serialization sets a YAML dump width that prevents folding long titles across lines. Fixes the "…malformed. Ca" class.

## Validation gate (before Tier 1 ships as default)

1. Hand-label the real corpus: the 69 current survivors + a ~100-sample of gated-out authored turns, as durable/ephemeral.
2. Score the labeled set with the embedding judge; report AUC and precision/recall at the operating threshold, vs the heuristic alone.
3. **Ship Tier 1 as default only if it beats the heuristic alone** on this corpus; otherwise it ships behind `CAIRN_JUDGE=embedding` (off by default) and the prototypes get revisited.
4. The labeling file and eval script are committed (offline, no keys) so the gate is re-runnable when prototypes or embedders change.

## Operational fit

- Runs inside `ingest_transcript`'s existing flow; the LLM batch is assembled at the `cairn ingest`/`sweep` command level (one call per run, not per transcript).
- **Plugin SessionEnd hook launches the sweep detached** (fire-and-forget, same pattern as the SessionStart warm fix) so session close never waits on the judge.
- `IngestReport` gains `judge_tier` ("llm" / "embedding" / "none") and `judge_degraded` (count of candidates that fell back a tier); surfaced in `cairn ingest` output and `--json`.
- Plugin config documents the opt-in (`CAIRN_JUDGE=anthropic`).

## Error handling

- LLM: timeout/HTTP/parse failures → Tier 1 for the affected candidates, `judge_degraded` incremented, one warning line. Never raises out of the pipeline.
- Embedding judge: embedder load failure → Tier 0 (mirrors recall's graceful degradation).
- A `distilled`/`title` that comes back empty or wildly long (> 4× verbatim length) is discarded for that candidate (verbatim format used); durability still applies if present.

## Testing (offline, no keys)

- **Judge resolution:** env-var matrix → correct tier; missing key/embedder → degradation.
- **EmbeddingJudge:** with the fake embedder, durable exemplars score > ephemeral exemplars; margin normalization clamps to [0,1].
- **LLMJudge:** transport mocked — batched request shape, JSON parsing, timeout → degradation, malformed response → degradation, over-long distillation discarded.
- **Pipeline:** combined-score gating (judged-low authored turn drops; judged-high passes), frontmatter carries combined score, LLM-tier note format (`[context]` distilled + `[verbatim]` original), dedup hash unchanged by title/distillation.
- **Title fix:** word-boundary truncation cases; a >80-char first line yields no mid-word chop and no YAML fold.
- **Validation harness:** eval script runs on a committed labeled fixture and prints AUC/PR (real labeling run is manual).

## Rollout

- **0.8.0** (new feature). No migration: existing notes stay valid; new fields are additive.
- After ship + validation pass: optionally re-run `cairn redistill`-style upgrade later — out of scope here.

## Out of scope (YAGNI / later)

- `cairn redistill` (re-derive titles/distillations over existing notes).
- Claude-Code-agent-as-judge (harness-LLM tier) and non-Anthropic LLM providers (interface is provider-agnostic; Anthropic ships first).
- Recall-side use of importance/durability beyond what exists today.
- Two-phase `reflect` background pass.
- Prototype-set learning/auto-tuning (prototypes are hand-curated; the eval harness measures them).
