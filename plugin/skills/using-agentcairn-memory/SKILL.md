---
name: using-agentcairn-memory
description: Use when starting a non-trivial task or finishing a decision/fix — recall prior memory before working, and remember durable facts worth carrying across sessions.
---

# Using agentcairn memory

You have a persistent memory backed by agentcairn (a Markdown vault the user owns). Use it.

## Recall before you work
Before designing, debugging, or re-deriving something non-trivial, **search memory first**:
- Use the `recall` tool (hybrid search) with a focused query — "how did we fix the auth token refresh?", "what did we decide about the migration order?".
- Expand a promising hit with `build_context` to read the full note.
- Recall is cross-project: prior solutions in *any* repo can help. Cite notes by permalink.

## Remember durable facts
After a decision, a non-obvious fix, a gotcha, or a stated user preference, **persist it** with the
`remember` tool — a short, self-contained fact. Good memories: "We rotate jwt-secret on deploy via
X.", "User prefers rebase-merges.", "DuckDB TIMESTAMP stores naive-UTC — bind accordingly."
Skip the trivial — the SessionEnd hook already captures the session in bulk; `remember` is for the
high-value things worth pinning deliberately.

The vault is plain Markdown the user can read and edit; treat it as shared, durable knowledge.
