// agentcairn for OpenCode — thin shell over the `cairn` CLI (all memory logic is in Python).
// Recall-at-start via experimental.chat.system.transform; capture-at-end via the event hook
// listening for session.idle / session.compacted events.
//
// No npm dependencies; ships as a plain .ts file loaded directly by OpenCode.
// OpenCode loads .ts directly via Bun — no build step required.

import type { Plugin } from "@opencode-ai/plugin";
import { execFile } from "node:child_process";

// ---------------------------------------------------------------------------
// Vault configuration — substituted by `cairn install opencode --vault <path>`.
// When the placeholder is intact (manual install or no --vault given) the
// plugin inherits the process environment unchanged (default vault behaviour).
// ---------------------------------------------------------------------------

const CAIRN_VAULT = "__CAIRN_VAULT__";

// ---------------------------------------------------------------------------
// Pure helpers — isolated so they can be tested without any hook wiring
// ---------------------------------------------------------------------------

/**
 * Build the argv array for `cairn recall`.
 * Kept pure so tests can verify it without spawning a process.
 */
export function buildRecallArgs(query: string, k = 5): string[] {
  return ["recall", query, "--json", "--k", String(k)];
}

/**
 * Format a JSON array of memory notes into a markdown block suitable for
 * injection into a system prompt.  Returns "" when there is nothing to inject
 * (empty array, all-blank texts) so callers can skip-inject on falsy check.
 */
export function formatMemoryBlock(
  notes: Array<{ title?: string; text?: string }>,
): string {
  if (!notes?.length) return "";
  const items = notes
    .map((n) => (n.text ?? "").trim())
    .filter(Boolean);
  if (!items.length) return "";
  return "## Relevant memories (agentcairn)\n\n" + items.join("\n\n---\n\n");
}

// ---------------------------------------------------------------------------
// Internal: safe cairn CLI runner
// ---------------------------------------------------------------------------

function cairn(args: string[]): Promise<string> {
  // If CAIRN_VAULT was substituted by the installer, force it into the child
  // env so recall and sweep use the same vault as the MCP server.  If the
  // placeholder is still intact (manual/no-vault install), inherit process.env
  // unchanged so the default vault resolution applies.
  const env = CAIRN_VAULT.startsWith("__")
    ? process.env
    : { ...process.env, CAIRN_VAULT };
  return new Promise((resolve) => {
    execFile("cairn", args, { env, timeout: 30_000 }, (err, stdout) => {
      if (err) {
        // Fail-safe: a missing / erroring cairn binary must never crash OpenCode.
        console.error("[agentcairn]", err.message);
        resolve("");
      } else {
        resolve(stdout ?? "");
      }
    });
  });
}

// ---------------------------------------------------------------------------
// Session-keyed query buffer.
// Keyed by sessionID so interleaved/sequential sessions never overwrite each
// other's recall query.  Updated by chat.message; consumed by
// experimental.chat.system.transform; cleaned up by the event hook.
// ---------------------------------------------------------------------------

const queries = new Map<string, string>();

// ---------------------------------------------------------------------------
// Plugin
// ---------------------------------------------------------------------------

/**
 * agentcairn ambient plugin for OpenCode.
 *
 * Hooks used:
 *   - experimental.chat.system.transform  — inject relevant memories into the
 *     system prompt at the start of each turn (recall-at-start).
 *   - event (session.idle / session.compacted) — fire `cairn sweep` after the
 *     session finishes to ingest the transcript (capture-at-end).
 *
 * All hook bodies are wrapped so a cairn/parse failure NEVER throws into
 * OpenCode.
 */
export const agentcairn: Plugin = async (_ctx) => ({
  // ------------------------------------------------------------------
  // Recall-at-start: inject relevant vault memories into the system prompt.
  //
  // Hook shape (from @opencode-ai/plugin Hooks interface):
  //   "experimental.chat.system.transform"?(
  //     input: { sessionID?: string; model: Model },
  //     output: { system: string[] },
  //   ): Promise<void>
  //
  // `output.system` is a mutable string[] — push a string to append text to
  // the system prompt.  The query is extracted from the OpenCode context via
  // a separate chat.message hook that buffers the latest user text.
  // ------------------------------------------------------------------
  "experimental.chat.system.transform": async (
    input: { sessionID?: string; model: unknown },
    output: { system: string[] },
  ) => {
    // NOTE: system.transform fires before the LLM call but does not itself
    // carry the user message text.  We use a session-scoped "last query"
    // buffer populated by the chat.message hook below.  On the very first
    // turn the buffer is empty and we skip injection gracefully.
    try {
      const q = queries.get(input.sessionID ?? "") ?? "";
      if (!q) return;
      const raw = await cairn(buildRecallArgs(q));
      const block = formatMemoryBlock(raw ? JSON.parse(raw) : []);
      if (block) output.system.push(block);
    } catch (e) {
      // Never break the session on a recall error.
      console.error("[agentcairn] recall inject failed", e);
    }
  },

  // ------------------------------------------------------------------
  // Query buffer: capture the latest user message text so system.transform
  // can use it.
  //
  // Hook shape:
  //   "chat.message"?(
  //     input: { sessionID: string; agent?: string; model?: {...}; ... },
  //     output: { message: UserMessage; parts: Part[] },
  //   ): Promise<void>
  //
  // Parts with type === "text" carry the user's visible text.
  // ------------------------------------------------------------------
  "chat.message": async (
    input: { sessionID?: string },
    output: { message: unknown; parts: Array<{ type: string; text?: string }> },
  ) => {
    try {
      const textParts = output.parts
        .filter((p) => p.type === "text" && p.text)
        .map((p) => p.text ?? "");
      if (textParts.length) {
        const sid = input.sessionID ?? "";
        queries.set(sid, textParts.join(" ").slice(0, 512)); // cap to avoid huge CLI args
      }
    } catch (_) {
      // ignore — buffer update is best-effort
    }
  },

  // ------------------------------------------------------------------
  // Capture-at-end: ingest just-ended session via the OpenCodeAdapter.
  //
  // session.idle fires when the agent returns to idle (turn complete or user
  // closed the session). session.compacted fires after context compaction.
  // Both are dispatched through the generic `event` hook (not top-level keys).
  //
  // Hook shape:
  //   event?(input: { event: { type: string; properties: { sessionID?: string } } }): Promise<void>
  //
  // cairn sweep is fire-and-forget (non-blocking, best-effort).
  // ------------------------------------------------------------------
  event: async ({
    event,
  }: {
    event: { type: string; properties?: { sessionID?: string } };
  }) => {
    if (
      event.type === "session.idle" ||
      event.type === "session.compacted"
    ) {
      // Best-effort cleanup: remove the query buffer for this session so it
      // doesn't linger in memory after the session ends.
      try {
        const sid = event.properties?.sessionID ?? "";
        if (sid) queries.delete(sid);
      } catch (_) {
        // ignore — cleanup is best-effort
      }
      void cairn(["sweep"]); // non-blocking, fire-and-forget
    }
  },
});
