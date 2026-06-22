# agentcairn — OpenCode plugin

Ambient recall-and-capture for [OpenCode](https://opencode.ai).  A thin TypeScript shell over the `cairn` CLI; all memory logic stays in Python.

## What it does

| Hook | Behaviour |
|------|-----------|
| `experimental.chat.system.transform` | Before each LLM call, recalls relevant vault memories and injects them into the system prompt. |
| `chat.message` | Buffers the latest user message text so the recall hook has a query string. |
| `event` (session.idle / session.compacted) | After the session ends or is compacted, fires `cairn sweep` to ingest the transcript and reindex. Sweep is non-blocking and fire-and-forget. |

No background daemon, no cloud dependency.  The vault lives on disk (plain Markdown).

## Prerequisites

`cairn` CLI must be on `$PATH`:

```bash
pip install agentcairn   # or: uv tool install agentcairn
cairn --version
```

Set `CAIRN_VAULT` (or accept the default `~/agentcairn`) and ensure the OpenCode harness is registered:

```bash
cairn install opencode   # installs this plugin + slash commands into ~/.config/opencode/, and writes the MCP server into opencode.json
```

`cairn install opencode` does everything below. To install manually instead:

```bash
# Copy the plugin into OpenCode's plugin directory (auto-loaded at startup)
mkdir -p ~/.config/opencode/plugin
cp /path/to/integrations/opencode/agentcairn.ts ~/.config/opencode/plugin/agentcairn.ts
```

(Optionally reference it explicitly in `~/.config/opencode/opencode.json`:)

```json
{
  "plugin": ["~/.config/opencode/plugin/agentcairn.ts"]
}
```

## Slash commands

Copy the command files to make `/recall` and `/remember` available in OpenCode:

```bash
mkdir -p ~/.config/opencode/commands
cp integrations/opencode/commands/recall.md    ~/.config/opencode/commands/
cp integrations/opencode/commands/remember.md  ~/.config/opencode/commands/
```

- `/recall <query>` — search vault and surface relevant notes
- `/remember <fact>` — write a durable note immediately

## Running the tests

The pure-logic tests use Node's built-in test runner (no extra deps):

```bash
node --test integrations/opencode/agentcairn.test.ts
```

Node 22+ strips TypeScript types natively; no build step required.

## API assumptions

OpenCode's plugin API is under active development.  The following assumptions are made and isolated in the hook wiring in `agentcairn.ts`; a signature change only requires updating those hooks:

| Assumption | Basis |
|------------|-------|
| `experimental.chat.system.transform` receives `(input: { sessionID?: string; model: Model }, output: { system: string[] })`. Mutating `output.system` appends text to the system prompt. | `@opencode-ai/plugin` source, `packages/plugin/src/index.ts`, inspected 2026-06. |
| `chat.message` receives `output.parts` as `Part[]`; text parts have `{ type: "text", text: string }`. | `@opencode-ai/sdk` `types.gen.ts` + plugin `Hooks` interface, inspected 2026-06. |
| `session.idle` and `session.compacted` are **event types** dispatched through the single `event` hook (`event.type === "session.idle"` / `"session.compacted"`), not separate top-level `Hooks` keys. | OpenCode docs notification example + `packages/opencode/src/session/status.ts` (`Idle` event defined as `type: "session.idle"`), inspected 2026-06. |
| OpenCode loads `.ts` files directly via Bun — no pre-compilation needed. | OpenCode plugin docs + loader behaviour, inspected 2026-06. |

Hook wiring should be verified in a live OpenCode session before shipping.  See the inline `// NOTE:` comments in `agentcairn.ts` for per-hook reasoning.
