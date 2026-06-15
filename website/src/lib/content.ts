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
  { label: "Any host", href: "#hosts" },
  { label: "GitHub", href: site.repo },
];

export const hero = {
  eyebrow: "Local-first memory for AI agents",
  h1: "Most agent memory makes a database the source of truth. We made it your files.",
  subhead:
    "agentcairn inverts the stack: human-readable Markdown with [[wikilinks]] is the truth, and a rebuildable DuckDB index gives your agent fast hybrid retrieval. Hand-edit a fact in Obsidian and the agent picks it up.",
  install: [
    "claude plugin marketplace add ccf/agentcairn",
    "claude plugin install agentcairn@agentcairn",
  ],
};

export const footer = {
  license: "Apache-2.0",
  copyright: "© 2026 Charles C. Figueiredo",
  definition:
    "a stack of stones raised to mark a trail or a place worth remembering, left for whoever comes next.",
};

export const inversion = {
  eyebrow: "The inversion",
  h2: "Most systems make the database the truth. We made it your files.",
  body: [
    "Mem0 and Zep keep your memory in a cloud database. Letta and agentmemory keep it in a database too, and treat files — if any — as a one-way export. agentcairn is the only one where the Markdown vault *is* the source of truth.",
    "So your memory survives a model upgrade, a corrupted index, a schema change — even uninstalling the tool. There is nothing to lose, because the truth was never trapped in the database.",
  ],
};

export const differentiators = [
  { title: "Obsidian Vault is the source of truth", body: "Human-readable Markdown with frontmatter and [[wikilinks]]. Edit it by hand; the index honors your edits." },
  { title: "The index is disposable", body: "DuckDB is a rebuildable cache. `cairn reindex` restores everything — zero data loss." },
  { title: "Non-lossy by construction", body: "The full note is always retained. Distillation only adds derived notes that link back." },
  { title: "Redaction before every write", body: "Secrets scrubbed (regex + entropy + URL-cred) before body, title, or tags reach the vault." },
  { title: "A free, deterministic graph", body: "Your [[wikilinks]] are the graph — no LLM extraction, no hallucinated entities." },
  { title: "Daemonless, zero external DB", body: "One embedded DuckDB does vector + BM25 + graph. No server, no Neo4j/Postgres/Qdrant." },
];

export const howItWorks = {
  body: "Capture reads your agent's session transcripts out-of-band, then redacts → dedups → importance-gates → distills into the vault. Retrieval fuses BM25 + vectors with RRF, with an optional cross-encoder reranker. The vault and the index reconcile on spawn; the MCP server exposes remember · recall · search · build_context · recent.",
};

export const benchmark = {
  // Numbers mirror the README "Benchmarks measured" tables (source of truth:
  // benchmarks/ harness). Keep them in sync with the README — do not edit here alone.
  locomoCaption:
    "LoCoMo retrieval, turn-level macro-avg, FastEmbed nomic-embed-text-v1.5 (the default).",
  rows: [
    { arm: "BM25 only", r5: "0.527", r10: "0.604", mrr: "0.459", strong: false },
    { arm: "vector only", r5: "0.536", r10: "0.637", mrr: "0.433", strong: false },
    { arm: "hybrid (RRF)", r5: "0.562", r10: "0.648", mrr: "0.477", strong: false },
    { arm: "hybrid + reranker", r5: "0.662", r10: "0.735", mrr: "0.608", strong: true },
  ],
  longmemevalCaption: "LongMemEval-S, full 500-instance set. Full turn r@10/MRR in the README.",
  longmemevalRows: [
    { arm: "BM25 only", sessionR5: "0.920", sessionMrr: "0.918", turnR5: "0.680", strong: false },
    { arm: "vector only", sessionR5: "0.936", sessionMrr: "0.916", turnR5: "0.507", strong: false },
    { arm: "hybrid (RRF)", sessionR5: "0.954", sessionMrr: "0.938", turnR5: "0.640", strong: false },
    { arm: "hybrid + reranker", sessionR5: "0.969", sessionMrr: "0.963", turnR5: "0.788", strong: true },
  ],
  contextCaption: "Context the default config recalls vs the full history. Estimate (~4 chars/tok).",
  contextRows: [
    { dataset: "LoCoMo", haystack: "25,646 tok", recalled: "529 tok", reduction: "51.1×" },
    { dataset: "LongMemEval-S", haystack: "136,552 tok", recalled: "2,207 tok", reduction: "64.7×" },
  ],
  caveats: [
    "No single headline number — these are relative ablation signals.",
    "graph-boost is inert on chat corpora (no native wikilink graph); it's for real vaults.",
    "QA-accuracy numbers use an Anthropic judge, not GPT-4o — not comparable to published leaderboards.",
  ],
};

export const cli = [
  "# Claude Code plugin (recommended)",
  "claude plugin marketplace add ccf/agentcairn",
  "claude plugin install agentcairn@agentcairn",
  "",
  "# Codex plugin",
  "codex plugin marketplace add ccf/agentcairn",
  "codex plugin add agentcairn@agentcairn",
  "",
  "# ...or use it directly — MCP server + CLI, any host",
  "uvx agentcairn                      # on-demand MCP server",
  "cairn install cursor                # wire the server into another host",
  "cairn recall \"how did we fix auth?\"  # hybrid recall",
  "cairn savings                       # context recall has saved you",
  "cairn doctor                        # health-check the index",
];

export const agents = {
  eyebrow: "Use it in any MCP host",
  h2: "First-class in Claude Code, Codex, and Antigravity. Portable everywhere else.",
  body:
    "Claude Code, Codex, and Antigravity get a first-class plugin — a bundled MCP server, " +
    "a memory skill, and (on Claude Code and Codex) ambient session hooks (recall at session " +
    "start, capture at session end). Antigravity has no plugin hooks, so capture runs out-of-band " +
    "via `cairn sweep`. Every other MCP host gets the same recall/search/`remember` tools via the " +
    "portable server; `cairn install` wires it in non-destructively (your other servers are " +
    "preserved, the original backed up to `<config>.bak`). One global `~/agentcairn` vault, " +
    "shared across every host.",
  rows: [
    { host: "Claude Code", support: "Plugin", setup: "cairn install claude-code", ambient: "full" },
    { host: "Codex", support: "Plugin", setup: "cairn install codex", ambient: "partial" },
    { host: "Cursor", support: "MCP server + skill + ingest", setup: "cairn install cursor", ambient: "partial" },
    { host: "Claude Desktop", support: "MCP server", setup: "cairn install claude-desktop", ambient: "none" },
    { host: "VS Code (Copilot)", support: "MCP server", setup: "cairn install vscode", ambient: "none" },
    { host: "Gemini CLI", support: "MCP server", setup: "cairn install gemini", ambient: "none" },
    { host: "Antigravity", support: "Plugin + ingest", setup: "cairn install antigravity", ambient: "partial" },
  ],
  install: [
    "cairn install                 # detect installed agents + preview (writes nothing)",
    "cairn install codex           # install the Codex plugin (shells to `codex plugin …`)",
    "cairn install cursor          # write MCP config for an MCP host",
    "cairn install --all           # configure every detected agent",
  ],
  note:
    "Plugin hosts (Claude Code, Codex, Antigravity) install via the host's own CLI — the MCP " +
    "server is bundled in the plugin. MCP hosts take a JSON mcpServers entry (VS Code uses its " +
    "servers key), written non-destructively, idempotent, backup-first. Ambient recall-at-start + " +
    "capture-at-end is fully wired on Claude Code; on Codex the hooks ship and capture also " +
    "runs out-of-band via `cairn sweep`, with live recall-at-start being verified. Antigravity has " +
    "no recognized plugin hooks — capture runs out-of-band via `cairn sweep` (◐). `agy plugin " +
    "install` takes a local directory (not a git repo), so install with `cairn install antigravity " +
    "--source <plugin dir>`; it also removes any stale mcp_config.json entry. Cursor has no " +
    "plugin hooks either — `cairn sweep` ingests sessions out-of-band from Cursor's global " +
    "`state.vscdb` SQLite store (`cursorDiskKV` user bubbles); Cursor remains an MCP host (not a " +
    "plugin host), but `cairn install cursor` also installs the `using-agentcairn-memory` skill to " +
    "`~/.cursor/skills/` alongside writing `~/.cursor/mcp.json`. Gemini CLI ingest " +
    "is not supported — Google is sunsetting it (2026-06-18) in favour of Antigravity; " +
    "`cairn install gemini` (MCP wiring) still works for Gemini-based MCP hosts.",
};
export const trust = [
  { k: "Redaction before write", v: "regex + entropy + URL-credential" },
  { k: "Localhost-only MCP", v: "READ_ONLY queries, no exposed ports" },
  { k: "No telemetry", v: "nothing phones home" },
  { k: "Index outside the vault", v: "the .duckdb cache is never synced" },
];
