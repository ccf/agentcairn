---
name: using-agentcairn-memory
description: Use when AgentCairn memory tools are loaded and starting a non-trivial task or finishing a decision/fix — recall prior memory before working, and remember durable facts worth carrying across sessions.
metadata:
  internal: true
---

# Using agentcairn memory

You have a persistent memory backed by agentcairn (a Markdown vault the user owns). Use it.

## Require the full integration

This skill assumes AgentCairn's MCP tools are already loaded. Before following
the memory workflow, confirm that `recall` and `remember` are available.

If the tools are missing:

- Do not invent tool calls or imply that this behavior skill installed the
  AgentCairn runtime, MCP server, plugin, or hooks.
- Explain that the full AgentCairn integration is not loaded.
- Invoke `$agentcairn-setup` if available, or direct the user to the native
  AgentCairn installation instructions.
- Stop the memory workflow until installation is complete and the host has been
  restarted.

## Recall before you work
Before designing, debugging, or re-deriving something non-trivial, **search memory first**:
- Use the `recall` tool (hybrid search) with a focused query — "how did we fix the auth token refresh?", "what did we decide about the migration order?".
- Expand a promising hit with `build_context` to read the full note.
- Recall is cross-project: prior solutions in *any* repo can help. Cite notes by permalink.
- Recall automatically prefers your current project's memories while still surfacing relevant cross-project ones (marked `[from: <project>]`); pass a project to target another repo, or `--scope project` to limit a query to just this one.

## Remember durable facts
After a decision, a non-obvious fix, a gotcha, or a stated user preference, **persist it** with the
`remember` tool — a short, self-contained fact. Good memories: "We rotate jwt-secret on deploy via
X.", "User prefers rebase-merges.", "DuckDB TIMESTAMP stores naive-UTC — bind accordingly."
Skip the trivial — the SessionEnd hook already captures the session in bulk; `remember` is for the
high-value things worth pinning deliberately.

The vault is plain Markdown the user can read and edit; treat it as shared, durable knowledge.
