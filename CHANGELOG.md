# Changelog

All notable changes to **agentcairn** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [0.22.0] - 2026-06-23

### Added
- **First-class OpenCode support** ([opencode.ai](https://opencode.ai)). `cairn sweep` auto-detects and
  distills OpenCode sessions (a new `OpenCodeAdapter` over `~/.local/share/opencode/storage`,
  positive-ID + fail-closed); `cairn install opencode` writes the MCP server into `opencode.json`'s
  `mcp` block, installs `recall`/`remember` slash commands, and drops a lean ambient TS plugin
  (`integrations/opencode/`) that does recall-at-start + capture-at-end as a thin shell over the
  `cairn` CLI. *(The OpenCode-side schema/plugin-API was inspected from source; verify in a live
  OpenCode session — capture/recall degrade safely if an assumption is off.)*
- **Cloud embedding tier (opt-in)** — `CAIRN_EMBEDDER=voyage` (default `voyage-3`) or `=openai`
  (`text-embedding-3-small`) for higher recall quality, keys from `VOYAGE_API_KEY` / `OPENAI_API_KEY`
  (+ `OPENAI_BASE_URL`). stdlib-only (no SDK dep), fail-closed (never returns zero vectors). Local
  `fastembed` stays the default; with the cloud tier on, your already-redacted note text + queries
  reach the provider (opt-in, like `CAIRN_JUDGE=anthropic`). Switching tiers re-embeds the vault.
- **Hermes Agent support** — agentcairn as a native Hermes `MemoryProvider` (`integrations/hermes/`):
  auto-recall + session-end distillation + curated `memory_save`/`recall`/`search` tools, sharing the
  same vault as your other agents.
- **`cairn recall --json`** — machine-readable recall output for tooling/plugins.
- A `Dockerfile` for the MCP server (used by directory listings, e.g. Glama).

### Changed
- Supported-agents tables reordered; **Hermes** added, **Gemini CLI** dropped (deprecated). Website +
  README refreshed (Obsidian integration, `cairn link`, `cairn schedule`, top nav, `/hermes` page);
  the long-form Roadmap moved out of the README.
- README/repo hardening: Trivy filesystem security scan + status badges; bumped vulnerable transitive
  deps (`starlette`, `cryptography`, `pydantic-settings`).

## [0.21.0] - 2026-06-20

### Added
- **`cairn schedule install | uninstall | status`** — a managed per-OS scheduler (launchd on macOS,
  user crontab on Linux) that runs `cairn sweep` periodically, so long-running and resumed sessions
  (and non-Claude-Code hosts) get captured without hand-editing crontab. Opt-in and idempotent, with
  `--interval` (e.g. `30m`/`1h`), `--vault`, and `--print`. `cairn install` now hints at it.

### Changed
- **Recall returns each note at most once.** Hybrid recall was chunk-level, so a single note could
  appear several times and crowd out others; results are now de-duplicated by note (keeping the
  best-scoring chunk), with the candidate pool widened so `k` unique notes are still returned.

### Fixed
- **Capture on compaction.** The Claude Code plugin now also captures on `PreCompact` (not only
  `SessionEnd`), so long/resumed sessions are swept at each compaction boundary instead of waiting
  for the session to formally end. Plugin bumped to 0.3.0 — `claude plugin update agentcairn`.

### Internal
- End-to-end test harness under `tests/e2e/`: an offline capture→index→recall smoke, a gated
  recall-quality eval (the ruler for ranking tuning), an MCP-over-stdio tool-contract test, a per-host
  `cairn install` matrix, and a Claude Code plugin-hook contract test. Added a Trivy filesystem
  security scan (Security tab) and README badges.

## [0.20.1] - 2026-06-18

### Fixed
- **Plugin recall outage from the 0.18 index migration.** The bundled plugin manifests still pinned
  `CAIRN_INDEX` to the old global index path that 0.18 rehomed away, so the plugin MCP server failed
  with `no index`. Removed `CAIRN_INDEX`/`index_path` from all plugin MCP manifests, hooks, and
  userConfig, and bumped the plugin to 0.2.0; the index now derives from `CAIRN_VAULT`. Update the
  plugin (`claude plugin update agentcairn`, and the Codex/Antigravity equivalents) to restore recall.

## [0.20.0] - 2026-06-18

### Added
- `cairn link` — opt-in command that writes each note's top semantic neighbors into a `related:`
  frontmatter list of `[[wikilinks]]`, populating the Obsidian graph (edges + backlinks). Idempotent
  (writes only when a note's links change), one-directional (Obsidian backlinks show the reverse),
  `--top`/`--min-score` tunable, `--dry-run` to preview. Reuses 0.19.0's `semantic_neighbors`.

## [0.19.0] - 2026-06-18

### Changed
- Memory **permalinks/slugs derive from the distilled title** instead of the (often trivial)
  trigger turn — readable filenames in the vault (existing notes unchanged).
- `cairn ingest` now reports promoted compaction `summaries` in the headline and no longer
  double-counts them under "skipped".

### Added
- `build_context` returns a `related` list of semantic-neighbor notes (cosine over indexed
  vectors), so it's useful even for notes without `[[wikilinks]]`. User-authored wikilinks
  still populate `outgoing`/`incoming`.

## [0.18.0] - 2026-06-17

### Changed
- The DuckDB index default is now **vault-scoped**: `~/.cache/agentcairn/indexes/<vault_key>.duckdb`,
  derived from the vault. A scratch/test vault can no longer write into your production index.
  `--index` / `CAIRN_INDEX` still override. `cairn install` no longer pins `CAIRN_INDEX`
  (and strips a stale one); the legacy global `index.duckdb` is auto-rehomed on first run.
  `cairn install`'s `--index` flag was removed (the index is derived from the vault).

### Added
- `--vault` on `recall` / `recent` / `index-status` / `doctor` (the index derives from it).
- `cairn doctor` now reports `DRIFT` (with counts + remedy) when the index and vault disagree.

## [0.17.0] - 2026-06-16

### Added
- **Compaction summaries are now captured as `session-summary` memories.** When a coding-agent session overflows its context, the harness writes a dense, model-generated summary of the session so far (Claude Code's `isCompactSummary` record; Codex similarly). agentcairn already recognized these (`EventKind.COMPACT_SUMMARY`) but dropped them — now the **latest** compaction per session is captured as one verbatim, project-stamped `session-summary` note. These bypass the durability judge (compaction is itself the substance signal) but are still **redacted** and deduped, are clearly marked **model-generated** (`kind: session-summary`), carry full `project`/`harness`/session provenance (so they flow into provenance-aware recall and the Obsidian plugin's currency/provenance view), and are **excluded from cosine consolidation** so a session synthesis can never supersede a user-asserted memory. One *current* summary per session: a newer compaction supersedes the prior note (`superseded_by`, non-lossy, timestamp-guarded). Scope: Claude Code + Codex (the harnesses that emit compaction summaries); user-prompt capture is unchanged. Out of scope (future): atomic extraction from summaries, Cursor/Antigravity.

## [0.16.0] - 2026-06-15

### Added
- **Provenance-aware recall (#28).** Memory notes now carry their origin — `project` (the repo/working-dir name) and `harness` — in frontmatter and the index, threaded end-to-end from the ingest event through distillation. At recall time the current project's memories are **boosted ×1.4** (alongside the existing graph ×1.2 / validity ×0.5 multipliers, and re-applied on the rerank path) so they lead, while cross-project memories still surface — a single global vault stays the default and nothing is hidden. The current project is resolved from an explicit `project` argument, else the caller's working directory (`os.getcwd()`), else none (no boost). Cross-project hits are marked `[from: <project>]` (CLI) / `cross_project: true` (MCP). An optional hard scope — `recall/search --scope project` (CLI) or `scope="project"` (MCP `search`/`recall` tools) — limits results to the current project only; with no resolved project it logs a warning and falls back to boosting-only. The DuckDB `notes` table gains nullable `project`/`harness` columns via an additive migration (the index is a rebuildable cache); existing notes without provenance get `NULL` (no boost, still surface). Provenance applies going-forward — old notes are not backfilled. Out of scope (issue #28 half 2): separate/scoped vaults, shared-vault multi-user attribution, and `git_branch`.

## [0.15.0] - 2026-06-15

### Added
- **Cursor is now first-class on both sides — ingest *and* output.**
  - **Ingest (#36, the final harness).** agentcairn captures Cursor sessions from its global `<CursorUser>/globalStorage/state.vscdb` SQLite store (`cursorDiskKV` table, `bubbleId:*` JSON "bubbles"); `cairn sweep` auto-detects it alongside Claude Code, Codex, and Antigravity. The DB is read lock-free and immutable (`file:…?immutable=1`) so a running Cursor is never disturbed, and the user-only filter is pushed into SQL (`json_valid(value)` precedes `json_extract`, so a single malformed bubble can't abort the scan). Classification is positive-identification and fail-closed: only a `type==1` bubble with non-empty `text` is a candidate, and attached files / rules / @-mentions / codebase context live in separate fields, so injected framing can't leak into the vault. `--project` isn't honored for Cursor (one global DB spans all projects; per-bubble `workspaceProjectDir` still gives provenance).
  - **Output — the recall/remember skill.** `cairn install cursor` now also installs the `using-agentcairn-memory` skill to `~/.cursor/skills/using-agentcairn-memory/SKILL.md`, alongside the unchanged `~/.cursor/mcp.json` write. Cursor has no plugin system, so it stays an **MCP host** (`kind="mcp"`); the skill install is additive via a new `Host.skill_dir`, and the skill ships as package data (`cairn/assets/`) so a pip-installed `cairn` can write it without the repo `plugin/` dir (a test keeps the two copies byte-identical). `--print` previews the MCP snippet and a `would install skill → …` note and writes nothing.

## [0.14.0] - 2026-06-14

### Added
- **Antigravity is now a first-class plugin host** (alongside Claude Code and Codex). The plugin bundles the MCP server (recall/search/`remember`) and reuses the `using-agentcairn-memory` skill, packaged at the plugin root as `plugin.json` + `mcp_config.json` (Antigravity's manifest lives at the root and auto-discovers a wrapper-form `mcp_config.json` with `CAIRN_VAULT` set). `cairn install antigravity --source <dir>` installs it via `agy plugin install`. Antigravity has no recognized plugin hooks, so ambient capture stays out-of-band via `cairn sweep` (its transcripts are ingested since 0.13.0).

### Changed
- **`cairn install` reclassifies `antigravity` from an MCP host to a plugin host.** It shells `agy plugin install <source>` and, after a successful install, removes any stale `mcpServers.agentcairn` entry from `~/.gemini/config/mcp_config.json` (backup-first) so the bundled plugin MCP isn't double-registered — the same "only bundle" rule as Codex. Because `agy plugin install` accepts a local directory or a registered marketplace (not a git repo), Antigravity **requires** `--source <dir>` and errors clearly otherwise; Codex/Claude Code keep defaulting to the `ccf/agentcairn` marketplace.

## [0.13.0] - 2026-06-14

### Added
- **Antigravity CLI is now an ingested harness.** agentcairn captures Antigravity sessions out-of-band from `~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl` (plaintext JSONL, written per conversation in both headless and interactive modes); `cairn sweep` auto-detects it alongside Claude Code and Codex. Classification is positive-identification and fail-closed: only a `USER_INPUT` step with `source == "USER_EXPLICIT"` contributes a candidate, and only the inner `<USER_REQUEST>` block is extracted — the injected `<ADDITIONAL_METADATA>`/`<USER_SETTINGS_CHANGE>` framing can't leak into the vault by construction. cwd/project is resolved best-effort from `cache/last_conversations.json`.

### Changed
- **Gemini CLI is no longer a target for transcript ingestion.** Google is sunsetting the Gemini CLI (consumer cutoff 2026-06-18) in favour of the Antigravity CLI (`agy`), which agentcairn ingests instead. `cairn install gemini` (MCP server wiring) remains valid for Gemini-based MCP hosts; only Gemini CLI *transcript ingestion* is unsupported (it was never shipped).

## [0.12.0] - 2026-06-14

### Added
- **agentcairn is now a first-class Codex plugin** (alongside the Claude Code plugin). It bundles the MCP server (recall/search/`remember`), reuses the `using-agentcairn-memory` skill, and ships session hooks — installable from the Codex marketplace (`codex plugin marketplace add ccf/agentcairn && codex plugin add agentcairn@agentcairn`) or via `cairn install codex`. The Codex `.mcp.json` is a bare server map and sets `CAIRN_VAULT` explicitly (the MCP server has no vault default). Dogfood-verified: `codex mcp list` shows the bundled server registered and enabled.

### Changed
- **`cairn install` routes by host kind.** Plugin hosts (`claude-code`, `codex`) install the *plugin* by shelling to the host's own CLI — the MCP server is bundled in the plugin, never written to a config file — so a host with a plugin can't end up double-registering the MCP server. MCP hosts (`cursor`, `claude-desktop`, `vscode`, `gemini`, `antigravity`) still get a written MCP config as before. `cairn install codex` additionally removes any stale `[mcp_servers.agentcairn]` block from `~/.codex/config.toml` (only after a successful plugin install, so an aborted install never leaves you without memory). New `--source` flag overrides the plugin marketplace source (default `ccf/agentcairn`).

## [0.11.0] - 2026-06-13

### Added
- **Multi-harness transcript ingestion — Codex sessions are now ingested alongside Claude Code.** A new `HarnessAdapter` seam (`cairn.ingest.harness`) owns each harness's transcript location, container format, and structural classification; `cairn sweep`/`cairn ingest` **auto-detect every harness present on disk** (Claude Code + Codex) and ingest them in one pass, narrowable with `--harness` or `CAIRN_HARNESSES` (`--transcripts-dir` now requires a single explicit `--harness`). The Codex adapter maps `~/.codex/sessions/.../rollout-*.jsonl` with the same positive-identification, fail-closed discipline as Claude Code: only affirmatively-recognized authored user prose becomes a candidate, and a `role=user` row that is actually an injected block (`# AGENTS.md`, `<INSTRUCTIONS>`, `<environment_context>`, …) is demoted via a tag-backstop and never written. `NormalizedEvent` now carries its originating `harness` (provenance plumbing). The redaction → judge → consolidation → reindex pipeline is unchanged; Gemini and Cursor are designed-for but deferred to later cycles.

## [0.10.1] - 2026-06-13

### Fixed
- **Memory consolidation now works on incremental sweeps, not just full rebuilds.** Dogfooding 0.10.0 showed the cosine gate only separates duplicates on *distilled-vs-distilled* embeddings — comparing against the recall index's full-note-body chunk embeddings clustered by conversational genre (74% of notes scored ≥0.88, and the highest-cosine pairs were distinct). Consolidation's neighbor lookup now embeds each live note's distilled `[context]` line (excluding superseded notes), held in memory per sweep, dropping the DuckDB dependency from the consolidation path entirely. The gate is re-tuned to **0.75** on this signal (the genuine dup/supersession pairs sit ~0.67–0.78, near the distilled median, so the gate is a coarse pre-filter and the LLM adjudicates every above-gate pair; fail-safe means a low gate only costs classify calls, never a wrong drop). `scripts/eval_consolidate.py` is fixed (it OOM'd, and measured the wrong full-body signal).

## [0.10.0] - 2026-06-13

### Added
- **Memory consolidation during ingest (LLM judge tier).** A new memory that semantically duplicates an existing one is skipped, and a memory that is a strictly newer version of the same evolving fact marks the old one `superseded_by` (kept in the vault, demoted in recall). Detection is cosine-pre-gated against the existing index plus this-sweep's writes, then classified by the LLM; any uncertainty or error resolves to "distinct" (both kept), so a wrong call never silently drops a distinct memory. Off the LLM tier, behavior is unchanged. New `CAIRN_CONSOLIDATE` knob (default on) is a kill-switch; the sweep reports `N deduped, M superseded`.

## [0.9.8] - 2026-06-13

### Fixed
- **The LLM judge now retries a failed chunk before degrading, and tolerates trailing text in the response.** Dogfooding 0.9.7 showed that — contrary to the missing-item theory behind 0.9.7 — the real batch failures are *transient timeouts* and *malformed JSON* from the model (e.g. a valid array followed by trailing prose, or an occasional bad delimiter), which `json.loads` rejected wholesale, degrading the entire chunk to the embedding tier. Two changes: (1) a failed chunk is now **retried up to `_MAX_RETRIES` (2) times with linear backoff** — the model is non-deterministic, so a re-roll usually returns valid JSON, and a retry also rides out an occasional slow/timed-out call; (2) the response is parsed with `json.JSONDecoder().raw_decode()`, which **ignores trailing text** after the JSON array ("Extra data"). Truly unrecoverable responses still degrade after retries are exhausted, and the loud degradation warning (0.9.4) still reports them.

## [0.9.7] - 2026-06-13

### Fixed
- **A large judge batch no longer degrades wholesale when the model omits an item.** Antecedent resolution (0.9.6) roughly doubled per-batch input and lengthened each distillation, so a 40-item response sometimes dropped trailing items; the parser then raised `missing judgment for index N`, degrading the *entire* 40-item chunk to the embedding tier (a clean re-gate degraded 143 candidates). Three changes: (1) **tolerant parsing** — an omitted index now degrades only that one item (filled from the fallback judge, marked degraded so it re-judges next run), not the whole chunk; (2) **`_BATCH_SIZE` 40 → 20** so responses stay complete; (3) **`max_tokens` 8192 → 16384** for headroom on richer distillations. Per-item degrade is total: an item that's omitted *or* malformed (missing/garbled `i` or `durability`) degrades only that index. Only a top-level invalid/truncated JSON response degrades the whole chunk (unrecoverable per-item).

## [0.9.6] - 2026-06-13

### Fixed
- **Confirmation-style decisions now distill into self-contained memories.** A turn like "lock A" or "go with (i)" previously produced an accurate but context-orphaned note ("Approach A is the decided direction" — A of what?), because the referent lived in the assistant's prior turn, which the user-turns-only model excludes. The LLM judge now receives the nearest preceding assistant turn as **transient, redacted resolution context** and is instructed to use it *only* to resolve a referent already present in your turn — never to manufacture a decision from a bare acknowledgement. `[verbatim]` (your literal words) and the keep-iff-distilled rule are unchanged; the antecedent is never stored. The judged cache is invalidated (v3) so the next sweep re-resolves existing orphaned notes.

## [0.9.5] - 2026-06-12

### Fixed
- **The judged cache is now version-stamped, so a judge fix can't be undone by stale cached verdicts.** Each cached verdict records a `_JUDGE_CACHE_VERSION`; rows from an older version — and legacy rows with no version — are discarded on load and re-judged. Without this, a poisoned cache outlived the bug that created it: the silent-timeout era (pre-0.9.4) cached ~812 degraded embedding-fallback verdicts as tier `llm`, and a cache-*reuse* re-gate kept reusing them (82 notes), whereas a full from-scratch re-judge produced the true result (327 distilled notes). Bumping the version (v2) invalidates every pre-0.9.4 cache automatically — the next sweep re-judges once and self-heals.

## [0.9.4] - 2026-06-12

### Fixed
- **The LLM judge no longer silently degrades on every batch.** The default `judge_timeout` was 10s, but a full 40-message batch takes ~30s on Sonnet — so with the shipped defaults *every* batch timed out and fell back to the embedding tier, producing extractive (un-distilled) notes while appearing to work. The request timeout now **scales with batch size** (at least 2s per message, with the configured `judge_timeout` as a floor), and the default floor is raised to 90s. Found dogfooding 0.9.3: a clean re-gate came back entirely extractive until the timeout was the suspect.
- **Degraded LLM runs are now reported loudly.** The old "LLM tier unavailable" note only fired when the tier never resolved (missing key); a run where the tier resolved but every batch failed kept `judge_tier == "llm"` and stayed silent. `sweep`/`ingest` now emit a yellow warning naming the degraded count and the remedy (raise `judge_timeout` / check connectivity) whenever any candidate fell back.

## [0.9.3] - 2026-06-12

### Changed
- **On the LLM-judge tier, the LLM's decision to *distill* a turn is now the keep signal** (supersedes 0.9.2's durability threshold). Dogfooding showed the LLM's durability floats cluster around 0.3-0.5 and don't cleanly separate memories from chatter — a 0.5 threshold swept in hundreds of short junk turns ("1", "proceed", "take a look") the LLM rated ~0.5 but declined to distill. The distill-vs-null decision is the clean bimodal signal, so an LLM-tier note is kept iff the LLM produced a distillation. Result: the vault holds only crisp, distilled memories. (Embedding tier still blends durability with the heuristic.)

### Fixed
- **A degraded LLM chunk no longer poisons the judge cache.** When an LLM batch fails and falls back to the embedding/neutral judge, that verdict has no distillation but is *not* a real LLM verdict. It is now marked degraded: it gates by the embedding blend rule (not the distill-keep rule) and is never cached at the LLM tier, so a single transient API failure can no longer permanently drop a durable turn that a later successful run would have distilled. (Caught by Cursor Bugbot on #61.)

## [0.9.2] - 2026-06-12

### Changed
- **On the LLM-judge tier, the judge's durability now gates the keep directly** (instead of a 50/50 blend with the lexical heuristic). A turn the LLM rates ephemeral is dropped even when it's long and marker-heavy, so the paid LLM verdict is no longer diluted by the keyword heuristic — surviving memories are the ones the LLM judged durable (and distilled). The embedding tier keeps the heuristic blend (there the heuristic is the stronger signal). Found dogfooding 0.9.1: a clean LLM-judged rebuild kept ~85% of notes undistilled because high-heuristic process chatter slipped through the blend.

## [0.9.1] - 2026-06-12

### Fixed
- **Redaction: hyphenated vendor keys are no longer fragmented (security).** The named secret patterns now run *before* the entropy heuristic, so a key like `sk-ant-…` (or `sk-proj-…`, Slack `xox…-…`) is consumed whole by its precise pattern. Previously the entropy pass — narrowed in 0.7.1 to exclude hyphens — sliced the hyphen-free middle of such a key and replaced only that, leaving the key's hyphen-delimited prefix and tail to survive into the vault and the LLM judge's input. Found dogfooding 0.9.0 on a real key; verified the corpus now redacts it whole with zero fragments.
- **The LLM-judge cache is tier-aware.** `JudgedCache` records which tier (embedding/llm) produced each verdict; a run only reuses a cached entry whose tier is at least the current run's. So an embedding-fallback verdict cached while no API key was set no longer permanently suppresses the LLM tier once a key is configured (legacy cache rows default to embedding and auto-heal on the next LLM run).

## [0.9.0] - 2026-06-12

### Added
- **User config file: `~/.agentcairn/config.toml`.** Every setting can now live in one TOML file instead of shell exports; env vars override file values (precedence: CLI flag > env > file > default). Keys map mechanically to env names (`judge_model` → `CAIRN_JUDGE_MODEL`; `anthropic_api_key` and `ollama_host` pass through), so the file schema can never drift from the env surface. New `cairn config` shows every setting's effective value and source (secrets masked); `cairn config --init` scaffolds a fully-commented template (mode 0600). The plugin's detached SessionEnd sweep reads the file directly — enabling the LLM judge no longer requires any shell-profile exports.

## [0.8.0] - 2026-06-12

### Added
- **Layer B: semantic memory-worthiness judge.** Authored turns are now judged for durability (decision/preference/lesson vs ephemeral task chatter) and the score combines 50/50 with the importance heuristic at the same 0.5 gate. Default tier: a local **embedding-prototype judge** (cosine margin against curated exemplar sets, using the shipped FastEmbed model — no key, no new deps). Opt-in tier: `CAIRN_JUDGE=anthropic` (+`ANTHROPIC_API_KEY`) enables an **LLM judge** that additionally writes a descriptive title and a crisp distilled restatement — notes then carry `[context] <distilled>` plus the full `[verbatim]` original (non-lossy; enables future re-distillation). One batched LLM call per ingest run with a hard timeout (`CAIRN_JUDGE_TIMEOUT`, default 10s); any failure silently degrades a tier and is reported. `cairn ingest`/`sweep` report the judge tier; the plugin's SessionEnd sweep now runs detached so session close never waits.

### Fixed
- Note titles truncate at a word boundary with an ellipsis (no more mid-word "…Ca" fragments) and no longer fold across YAML lines.

## [0.7.2] - 2026-06-12

### Fixed
- **Legacy-transcript injection rows no longer ingest as memories.** Claude Code <=2.1.150 wrote slash-command markers (`<command-message>`), `/`-command output (`<local-command-stdout>`), and `<bash-stdout/stderr>` dumps as user rows with **no structural flags** (no `isMeta`/`origin`/`toolUseResult`), so the 0.7.0 structural classifier saw them as authored prose (19 such notes surfaced in the post-rebuild audit). A minimal tag-prefix backstop now classifies flag-less rows starting with a known harness tag as `meta_injection` — structure remains the primary signal; the backstop only fires when no markers exist at all.

## [0.7.1] - 2026-06-12

### Fixed
- **Redaction no longer swallows paths, URLs, branches, or identifiers.** The entropy heuristic's candidate token class no longer includes `/`, `-`, or `_`, so structured identifiers — file paths, GitHub URLs, git branches, hyphenated slugs, snake_case/dunder names — can't form a candidate by construction (a vault audit found the old class ~99% false-positive on such identifiers; a corpus replay over real transcripts went from 2,875 `high_entropy` hits to 78, the remainder being by-design redactions). A new guarded `aws_secret_value` pattern covers the one realistic separator-bearing bare secret shape (exactly-40-char base64 with upper+lower+digit), running before the entropy pass so it can't be partially consumed. All known vendor key shapes remain covered by the named patterns; the golden zero-leakage corpus is unchanged and passing.

## [0.7.0] - 2026-06-11

### Changed
- **Ingestion now selects candidates by transcript structure, not text patterns.** A new normalized `EventKind` taxonomy + a positive-identification, fail-closed Claude Code classifier (keyed on `isMeta`/`toolUseResult`/`isCompactSummary`/`isVisibleInTranscriptOnly`/`origin`) means only genuinely human-authored turns become memories. This deterministically excludes tool output, slash-command/skill injections, `<task-notification>` events, and compaction summaries — without enumerating their text. An unmapped entry type or new harness yields zero candidates (safe, loud) rather than noise. `cairn ingest` now reports a per-kind skip tally; event provenance (origin project) is preserved through the pipeline for future use.

### Removed
- The text-pattern `is_framing_noise` denylist (0.6.1/0.6.2) — subsumed by structural classification. `sanitize_text` (escape/control stripping) stays.

## [0.6.2] - 2026-06-11

### Fixed
- **Broaden harness-framing filter to the full family.** 0.6.1 filtered slash-command and tool-output turns; this also drops `<task-notification>` background-task events (by far the most common — they were a large fraction of ingested noise), `<local-command-caveat>` boilerplate, and `<user-prompt-submit-hook>` output. The `<local-command*>` variants are now matched by prefix so future ones are covered too.

## [0.6.1] - 2026-06-11

### Fixed
- **Terminal escape sequences no longer leak into the vault.** Ingestion now strips ANSI/OSC escape codes and stray C0 control bytes from transcript text before anything is hashed, scored, or written — slash-command output (e.g. `/context`) and tool dumps were previously stored with raw `\e[…m` sequences and box-drawing art.
- **Harness framing is no longer ingested as memories.** User-role turns that are mechanically injected by the harness — slash-command output/markers (`<local-command-stdout>`, `<command-name>`, …), tool-result dumps (`<bash-stdout>`/`<bash-stderr>`), and "This session is being continued from a previous conversation…" compaction summaries — are now filtered out at candidate selection. (They were clearing the importance gate because their length, inflated by escape-code digits, scored above threshold.)

## [0.6.0] - 2026-06-11

### Added
- `cairn install` now supports **VS Code (Copilot)** (`cairn install vscode`) and **Antigravity** (`cairn install antigravity`). VS Code's config uses a `servers` top-level key (not `mcpServers`); the JSON writer now takes a configurable `root_key` to handle it. Antigravity reads `~/.gemini/config/mcp_config.json`.

### Removed
- Dropped the **Windsurf** host — Windsurf was renamed to Devin Desktop (2026-06-02) and its Cascade agent is EOL; the old `~/.codeium/windsurf` config path is no longer current. (Use `cairn install … --print` to wire up any unsupported host by hand.)

## [0.5.0] - 2026-06-11

### Added
- `cairn install <host>` — wire the agentcairn MCP server into other MCP hosts beyond Claude Code. Supports **Cursor**, **Claude Desktop**, **Windsurf**, **Gemini CLI** (JSON `mcpServers`) and **Codex** (TOML `[mcp_servers.agentcairn]`). `cairn install` with no argument detects installed hosts and previews (writes nothing); `--all` configures every detected host; `--print` emits the snippet without touching disk; `--vault`/`--index` override paths (absolute-ized before writing). Writes are non-destructive (other servers + unrelated keys preserved), idempotent, backup-first (`<config>.bak`), and atomic (temp file + rename, so a crash mid-write can't corrupt a live config); a malformed existing config is backed up and reported without being clobbered. The vault stays a single global `~/agentcairn`, so memory is shared across hosts.

### Changed
- README: new **"Agents supported"** matrix (Claude Code first-class plugin vs `cairn install` MCP-server hosts, with an ambient-capture column); the benchmark section ("Benchmarks measured") now presents LongMemEval-S as a table alongside LoCoMo and context efficiency as a table.

### Dependencies
- Added **`tomlkit`** (round-trips Codex TOML comments/formatting when merging the MCP entry).

## [0.4.0] - 2026-06-10

### Added
- `cairn warm` — pre-downloads the configured embedder + reranker models (best-effort, config-aware). The plugin's detached first-run job calls it so the first SessionEnd `sweep` and first `recall` aren't slowed by a model download.

## [0.3.0] - 2026-06-10

### Added
- `cairn savings` — a local, no-telemetry cumulative token-savings ledger: records each real recall (`full_haystack_tokens` vs `recalled_tokens`) to `~/.cache/agentcairn/usage.jsonl` and reports how much context recall has saved vs. dumping the whole vault. Surfaced via `cairn savings` (`--json`/`--oneline`), the `/agentcairn:savings` plugin command, and a line in the SessionStart digest. On by default and local; disable with `CAIRN_USAGE=0`. (Estimated, ~4 chars/token — a model of context size, not a measured cost.)

## [0.2.0] - 2026-06-10

### Added
- `cairn recent` — most-recently-modified notes (`--project` path-substring filter, `-n`/`--num`, `--json`); powers the plugin's SessionStart digest.
- `cairn init` — scaffold an Obsidian-ready vault (idempotent, non-destructive).
- **Claude Code plugin** (in-repo marketplace): auto-wires the `uvx agentcairn` MCP server, surfaces recent memory at SessionStart (with zero-step vault auto-init), distills each session at SessionEnd, and adds the `using-agentcairn-memory` skill plus `/agentcairn:recall|remember|memory|ingest` commands.

## [0.1.0] - 2026-06-10

### Added
- Initial public release. Markdown **vault is the source of truth**; a rebuildable, ephemeral DuckDB index is a disposable cache.
- Hybrid retrieval — vector (cosine) + BM25 (FTS) + wikilink-graph boost, fused with RRF, with a cross-encoder reranker **on by default** (`CAIRN_RERANK=0` to disable).
- `cairn` CLI: `parse`, `reindex`, `index-status`, `recall`, `ingest`, `sweep`, `doctor`, `serve`; on-demand MCP server via `uvx agentcairn` (`recall`/`search`/`build_context`/`recent`/`remember`).
- Embedders: FastEmbed (default `nomic-embed-text-v1.5`, configurable via `CAIRN_EMBED_MODEL`) and an Ollama tier (`CAIRN_EMBEDDER=ollama`).
- Bi-temporal validity: `valid_from`/`valid_until`/`superseded_by` frontmatter; recall soft-demotes superseded/expired notes (non-lossy — never hidden).
- Out-of-band capture from coding-agent transcripts (redacted, non-lossy `remember`).
- Published to PyPI via GitHub Trusted Publishing (OIDC, no stored secrets).

[Unreleased]: https://github.com/ccf/agentcairn/compare/v0.9.6...HEAD
[0.10.1]: https://github.com/ccf/agentcairn/compare/v0.10.0...v0.10.1
[0.10.0]: https://github.com/ccf/agentcairn/compare/v0.9.8...v0.10.0
[0.9.8]: https://github.com/ccf/agentcairn/compare/v0.9.7...v0.9.8
[0.9.7]: https://github.com/ccf/agentcairn/compare/v0.9.6...v0.9.7
[0.9.6]: https://github.com/ccf/agentcairn/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/ccf/agentcairn/compare/v0.9.4...v0.9.5
[0.9.4]: https://github.com/ccf/agentcairn/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/ccf/agentcairn/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/ccf/agentcairn/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/ccf/agentcairn/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/ccf/agentcairn/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/ccf/agentcairn/compare/v0.7.2...v0.8.0
[0.7.2]: https://github.com/ccf/agentcairn/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/ccf/agentcairn/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/ccf/agentcairn/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/ccf/agentcairn/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/ccf/agentcairn/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/ccf/agentcairn/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/ccf/agentcairn/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/ccf/agentcairn/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ccf/agentcairn/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ccf/agentcairn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ccf/agentcairn/releases/tag/v0.1.0
