# agentcairn — Claude Code plugin

Local-first agent memory inside Claude Code: auto-wires the agentcairn MCP server, surfaces recent
memory at the start of each session, and distills each session into a Markdown vault you own.

## Install
```bash
claude plugin marketplace add ccf/agentcairn
claude plugin install agentcairn@agentcairn
```
On install you'll be asked for a **vault path** (default `~/agentcairn`). The vault is **auto-created**
(Obsidian-ready) on the first session — no Obsidian setup needed.

## What you get
- **MCP tools:** `recall`, `search`, `build_context`, `recent`, `remember`.
- **Ambient memory:** SessionStart surfaces recent memories; SessionEnd distills the session.
- **Skill:** `using-agentcairn-memory` (recall-before-work, remember durable facts).
- **Commands:** `/agentcairn:recall`, `/agentcairn:remember`, `/agentcairn:memory`, `/agentcairn:ingest`.

The plugin runs the published `agentcairn` PyPI package via `uvx` — nothing to pip-install.
You can also scaffold a vault yourself: `uvx --from agentcairn cairn init ~/agentcairn`.
