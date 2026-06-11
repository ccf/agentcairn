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
  { label: "Any host", href: "#hosts" },
  { label: "Quickstart", href: "#quickstart" },
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
  contextEfficiency:
    "On LongMemEval-S (~136k-token sessions), agentcairn answers from ~2,200 tokens of recalled " +
    "context per query — a ~65× reduction in what the model has to read. Estimate; full 500-set.",
  longmemeval:
    "On LongMemEval-S's full 500-instance set, session-level retrieval reaches 0.969 recall@5 with " +
    "the reranker (right alongside prior work's ≈0.95), and turn-level reaches 0.788 — the " +
    "cross-encoder reranker is the lever.",
};

export const cli = [
  "# Claude Code plugin (recommended)",
  "claude plugin marketplace add ccf/agentcairn",
  "claude plugin install agentcairn@agentcairn",
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
  h2: "First-class in Claude Code. Portable everywhere else.",
  body:
    "Claude Code gets the full plugin — ambient recall at session start, capture at " +
    "session end, a memory skill, and slash commands. Every other MCP host gets the same " +
    "recall/search/`remember` tools via the portable server; `cairn install` wires it in " +
    "non-destructively (your other servers are preserved, the original backed up to " +
    "`<config>.bak`). One global `~/agentcairn` vault, shared across every host.",
  rows: [
    { host: "Claude Code", support: "First-class plugin", setup: "claude plugin install agentcairn@agentcairn", ambient: true },
    { host: "Cursor", support: "MCP server", setup: "cairn install cursor", ambient: false },
    { host: "Claude Desktop", support: "MCP server", setup: "cairn install claude-desktop", ambient: false },
    { host: "Windsurf", support: "MCP server", setup: "cairn install windsurf", ambient: false },
    { host: "Gemini CLI", support: "MCP server", setup: "cairn install gemini", ambient: false },
    { host: "Codex CLI", support: "MCP server", setup: "cairn install codex", ambient: false },
  ],
  install: [
    "cairn install                 # detect installed hosts + preview (writes nothing)",
    "cairn install cursor          # configure one host",
    "cairn install --all           # configure every detected host",
    "cairn install codex --print   # just print the snippet, change nothing",
  ],
  note:
    "Cursor / Claude Desktop / Windsurf / Gemini take a JSON mcpServers entry; Codex takes a " +
    "TOML table (comments and other servers preserved). Writes are non-destructive, idempotent, " +
    "and backup-first. Ambient capture (auto recall-at-start, capture-at-end) is Claude-Code-only today.",
};
export const trust = [
  { k: "Redaction before write", v: "regex + entropy + URL-credential" },
  { k: "Localhost-only MCP", v: "READ_ONLY queries, no exposed ports" },
  { k: "No telemetry", v: "nothing phones home" },
  { k: "Index outside the vault", v: "the .duckdb cache is never synced" },
];
