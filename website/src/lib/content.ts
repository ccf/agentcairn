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
