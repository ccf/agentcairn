export const site = {
  name: "agentcairn",
  title: "Shared Memory for AI Coding Agents — agentcairn",
  description:
    "One durable memory across supported coding agents. Your Markdown vault is canonical; a rebuildable local index provides fast, cited recall.",
  url: "https://agentcairn.dev",
  repo: "https://github.com/ccf/agentcairn",
  pypi: "https://pypi.org/project/agentcairn/",
  obsidian: "https://community.obsidian.md/plugins/agentcairn",
  ogImageAlt: "agentcairn — one memory trail across connected coding agents",
  themeColor: "#2563eb",
};

// Homepage section anchors. Prefixed with "/" so they also resolve from
// sub-pages (e.g. /hermes → /#how), not only from the homepage.
export const nav = [
  { label: "How it works", href: "/#how" },
  { label: "Install", href: "/#install" },
  { label: "Evidence", href: "/#measured" },
  { label: "Agents", href: "/#hosts" },
];

// Guide / niche pages surfaced in the top nav (and footer). Helps users find
// them and gives the niche pages internal-link equity for SEO.
export const navGuides = [
  { label: "Claude import", href: "/claude-code-memory/" },
  { label: "Obsidian", href: "/obsidian-ai-memory/" },
  { label: "GitHub", href: site.repo },
];

export const hero = {
  signal: "Local-first · open source · plain Markdown",
  h1: "One memory trail across your connected coding agents.",
  subhead:
    "AgentCairn keeps durable context in Markdown you can inspect, edit, and carry between Claude Code, Codex, Cursor, OpenCode, and other MCP hosts. A rebuildable local index gives each connected agent fast, cited recall from the same vault.",
  primaryCta: { label: "Choose your agent", href: "#install" },
  secondaryCta: { label: "See the index disappear", href: "#survives" },
  proof: "No hosted account · no external database · Apache-2.0",
};

export const footer = {
  license: "Apache-2.0",
  copyright: "© 2026 Charles C. Figueiredo",
  definition:
    "a stack of stones raised to mark a trail or a place worth remembering, left for whoever comes next.",
  guides: [
    { label: "Bring Claude Code memory with you", href: "/claude-code-memory/" },
    { label: "What is agent memory?", href: "/agent-memory/" },
    { label: "AI memory in Obsidian", href: "/obsidian-ai-memory/" },
    { label: "agentcairn for Hermes", href: "/hermes/" },
    { label: "Compare memory approaches", href: "/alternatives/" },
  ],
};

export const inversion = {
  eyebrow: "The contract",
  h2: "The files are canonical. The index is replaceable.",
  body: [
    "Every durable fact lives in an ordinary Markdown file. Open it in your editor or Obsidian, correct it by hand, sync it however you choose, and the next reconciled read honors the change.",
    "DuckDB is only the fast retrieval layer. Delete it and rebuild it from the vault without sacrificing the memory trail your agents share.",
  ],
};

export const contractPoints = [
  { key: "Keep", title: "Readable files", body: "Notes, frontmatter, and [[wikilinks]] remain the durable record." },
  { key: "Rebuild", title: "Disposable index", body: "BM25, vectors, graph signals, and reranking can be regenerated from Markdown." },
  { key: "Recall", title: "Cited context", body: "Project, currency, and a permalink travel with each retrieved memory." },
];

export const vaultProof = {
  image: "/obsidian-list.png",
  imageWebp: "/obsidian-list.webp",
  imageAlt:
    "AgentCairn's Memory view in Obsidian showing Markdown memories with project, harness, date, importance, and supersession metadata.",
  caption: "A real AgentCairn vault in Obsidian. The list is a view over the files—not a second memory store.",
};

export const installPaths = [
  {
    id: "claude-code",
    label: "Claude Code",
    title: "Install the first-class Claude Code plugin",
    summary: "The plugin bundles the MCP server, memory skill, ambient recall and capture hooks, and AgentCairn slash commands.",
    prerequisite: "uvx and the Claude Code CLI",
    command: "uvx --from agentcairn cairn install claude-code",
    restart: "Restart Claude Code completely after installation.",
    verify: "In a fresh session, confirm that remember, recall, search, build_context, and recent are available—then remember a fact and recall it.",
    href: "/claude-code-memory/",
    hrefLabel: "Bring Claude Code's existing auto-memory too",
  },
  {
    id: "codex",
    label: "Codex",
    title: "Install the first-class Codex plugin",
    summary: "The plugin bundles the MCP server, memory skill, verified SessionStart recall, SessionEnd capture, and the sweep backstop.",
    prerequisite: "uvx and the Codex CLI",
    command: "uvx --from agentcairn cairn install codex",
    restart: "Restart Codex completely after installation.",
    verify: "In a fresh session, confirm that remember, recall, search, build_context, and recent are available—then remember a fact and recall it.",
    href: `${site.repo}#install`,
    hrefLabel: "Read the complete Codex integration notes",
  },
  {
    id: "other",
    label: "Other agents",
    title: "Connect another coding agent",
    summary: "Pick a host to get its exact setup path. AgentCairn preserves unrelated MCP servers and backs up supported configuration files before writing.",
    prerequisite: "Choose a host below",
    command: "uvx --from agentcairn cairn install cursor",
    restart: "Restart the selected host after installation.",
    verify: "Open a fresh session and confirm that the five AgentCairn memory tools are available.",
    href: "#hosts",
    hrefLabel: "Compare integration and ambient-memory support",
  },
  {
    id: "standalone",
    label: "Standalone",
    title: "Install the CLI and MCP server directly",
    summary: "Use AgentCairn from your shell, run the MCP server on demand, or wire it into a host manually.",
    prerequisite: "uv and Python 3.11+",
    command: "uv tool install agentcairn\ncairn init ~/agentcairn\ncairn reindex ~/agentcairn\ncairn doctor --vault ~/agentcairn",
    restart: "No restart is required. The first reindex may download and warm the configured local embedding and reranking models.",
    verify: "Run cairn --version; after initializing and indexing the vault, cairn doctor should report status: OK.",
    href: `${site.repo}#using-it-directly`,
    hrefLabel: "See standalone ingestion and maintenance commands",
  },
];

export const otherInstallHosts = [
  {
    id: "cursor",
    label: "Cursor",
    command: "uvx --from agentcairn cairn install cursor",
    prerequisite: "uvx and Cursor",
    installs: "MCP tools plus the AgentCairn memory skill. Transcript capture is out-of-band.",
    restart: "Restart Cursor.",
    verify: "Start a new chat and confirm the five AgentCairn memory tools and skill are available.",
  },
  {
    id: "opencode",
    label: "OpenCode",
    command: "uv tool install agentcairn\ncairn install opencode",
    prerequisite: "uv and OpenCode",
    installs: "Persistent CLI, MCP configuration, ambient plugin, and the working `/recall` slash command. Save facts through the MCP `remember` tool.",
    restart: "Restart OpenCode.",
    verify: "Run cairn --version, then start a new session and confirm the AgentCairn MCP tools are available.",
  },
  {
    id: "vscode",
    label: "VS Code (Copilot)",
    command: "uvx --from agentcairn cairn install vscode",
    prerequisite: "uvx and VS Code",
    installs: "The portable MCP server configuration. Memory use is explicit rather than ambient.",
    restart: "Restart VS Code.",
    verify: "Open a fresh Copilot session and confirm the five AgentCairn memory tools are available.",
  },
  {
    id: "claude-desktop",
    label: "Claude Desktop",
    command: "uvx --from agentcairn cairn install claude-desktop",
    prerequisite: "uvx and Claude Desktop",
    installs: "The portable MCP server configuration. Memory use is explicit rather than ambient.",
    restart: "Restart Claude Desktop.",
    verify: "Open a fresh chat and confirm the five AgentCairn memory tools are available.",
  },
  {
    id: "gemini",
    label: "Gemini CLI",
    command: "uvx --from agentcairn cairn install gemini",
    prerequisite: "uvx and Gemini CLI",
    installs: "The portable MCP server configuration. Memory use is explicit rather than ambient.",
    restart: "Restart Gemini CLI.",
    verify: "Start a fresh session and confirm the five AgentCairn memory tools are available.",
  },
  {
    id: "hermes",
    label: "Hermes Agent",
    command: "hermes plugins install ccf/agentcairn/integrations/hermes\nhermes memory setup agentcairn",
    prerequisite: "Hermes Agent",
    installs: "The native AgentCairn MemoryProvider in Hermes' managed Python environment.",
    restart: "Start a new Hermes session.",
    verify: "Save a memory, inspect the resulting Markdown, then recall it in the new session.",
  },
  {
    id: "antigravity",
    label: "Antigravity",
    command: "uvx --from agentcairn cairn install antigravity --source /path/to/agentcairn/plugin",
    prerequisite: "uvx, the agy CLI, and a local AgentCairn checkout",
    installs: "Replace `/path/to/agentcairn/plugin` with your checkout's plugin directory. Capture remains sweep-based because Antigravity has no recognized plugin hooks.",
    restart: "Restart Antigravity.",
    verify: "Confirm the plugin is present and the AgentCairn memory tools are loaded.",
  },
  {
    id: "mcp",
    label: "Manual MCP setup",
    command: "uvx agentcairn",
    prerequisite: "uvx and a host that supports custom stdio MCP servers",
    installs: "This launches AgentCairn as a stdio MCP server. Add it to the host's MCP configuration; the command alone does not install a host integration or ambient hooks.",
    restart: "Restart the host after adding the server.",
    verify: "Confirm that remember, recall, search, build_context, and recent appear as MCP tools.",
  },
];

export const benchmark = {
  // Numbers mirror the benchmarks/README.md tables (source of truth: the
  // benchmarks/ harness). Keep them in sync — do not edit here alone.
  locomoCaption:
    "LoCoMo retrieval, turn-level macro-avg, FastEmbed nomic-embed-text-v1.5 (the default).",
  rows: [
    { arm: "BM25 only", r5: "0.527", r10: "0.604", mrr: "0.459", strong: false },
    { arm: "vector only", r5: "0.536", r10: "0.637", mrr: "0.433", strong: false },
    { arm: "hybrid (RRF)", r5: "0.562", r10: "0.648", mrr: "0.477", strong: false },
    { arm: "hybrid + reranker", r5: "0.662", r10: "0.735", mrr: "0.608", strong: true },
  ],
  longmemevalCaption: "LongMemEval-S, full 500-instance set. Full turn r@10/MRR in benchmarks/README.md.",
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

export const agents = {
  h2: "One vault, different levels of automation.",
  body:
    "Every supported host resolves the same configured Markdown vault. What varies is how much of the host lifecycle AgentCairn can automate: some recall and capture ambiently, some capture out-of-band, and MCP-only hosts expose explicit memory tools.",
  rows: [
    { host: "Claude Code", support: "Plugin", setup: "cairn install claude-code", ambient: "full" },
    { host: "Codex", support: "Plugin", setup: "cairn install codex", ambient: "full" },
    { host: "Cursor", support: "MCP server + skill + ingest", setup: "cairn install cursor", ambient: "partial" },
    { host: "OpenCode", support: "Plugin + MCP + ingest", setup: "cairn install opencode", ambient: "full" },
    { host: "Hermes Agent", support: "MemoryProvider plugin (Python 3.11+)", setup: "see the Hermes guide", ambient: "full" },
    { host: "Antigravity", support: "Plugin + ingest", setup: "cairn install antigravity --source /path/to/agentcairn/plugin", ambient: "partial" },
    { host: "VS Code (Copilot)", support: "MCP server", setup: "cairn install vscode", ambient: "none" },
    { host: "Claude Desktop", support: "MCP server", setup: "cairn install claude-desktop", ambient: "none" },
    { host: "Gemini CLI", support: "MCP server", setup: "cairn install gemini", ambient: "none" },
  ],
  note:
    "Plugin hosts (Claude Code, Codex, Antigravity) install via the host's own CLI — the MCP " +
    "server is bundled in the plugin. MCP configuration is merged using each host's native " +
    "schema, non-destructively, idempotently, and backup-first. Ambient recall-at-start + " +
    "capture-at-end is fully wired on Claude Code and Codex; Codex SessionStart recall is verified " +
    "live, and capture also runs out-of-band via `cairn sweep`. Antigravity has " +
    "no recognized plugin hooks — capture runs out-of-band via `cairn sweep` (◐). `agy plugin " +
    "install` takes a local directory (not a git repo), so install with `cairn install antigravity " +
    "--source /path/to/agentcairn/plugin` after replacing that path with your checkout's plugin directory; it also removes any stale mcp_config.json entry. Cursor has no " +
    "plugin hooks either — `cairn sweep` ingests sessions out-of-band from Cursor's global " +
    "`state.vscdb` SQLite store (`cursorDiskKV` user bubbles); Cursor remains an MCP host (not a " +
    "plugin host), but `cairn install cursor` also installs the `using-agentcairn-memory` skill to " +
    "`~/.cursor/skills/` alongside writing `~/.cursor/mcp.json`. Hermes gets in-process recall and " +
    "capture through its native MemoryProvider lifecycle in the standard managed Python 3.11 " +
    "environment; see the Hermes guide.",
};
export const trust = [
  { k: "Plaintext by design", v: "readable Markdown, not encrypted storage" },
  { k: "Secret-aware writes", v: "recognized patterns redacted; review unknown patterns and hand edits" },
  { k: "Local by default", v: "stdio MCP; no required daemon or external database" },
  { k: "Cloud only by opt-in", v: "enabled cloud features may send redacted text or queries off-device" },
];
