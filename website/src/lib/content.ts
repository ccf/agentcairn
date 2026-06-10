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
