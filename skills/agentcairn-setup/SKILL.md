---
name: agentcairn-setup
description: Install, configure, verify, upgrade, or repair AgentCairn persistent cross-agent memory. Use when a user wants shared local-first memory in Claude Code, Codex, Cursor, OpenCode, Antigravity, VS Code, Claude Desktop, or another supported harness; when AgentCairn recall or remember tools are missing; or when its plugin, MCP server, skill, or ambient hooks need setup diagnostics.
---

# AgentCairn setup

Set up the full AgentCairn integration by delegating to AgentCairn's native,
preview-first installer. Treat this skill as a management layer only: installing
this skill does not install the AgentCairn runtime, MCP server, plugin, or hooks.

## Preserve the boundary

- Use `uvx --from agentcairn cairn install` as the source of truth for supported
  hosts and generated configuration.
- Do not copy plugin files, write MCP configuration by hand, or reproduce the
  installer's host-specific logic.
- Do not claim AgentCairn is ready merely because this setup skill is present.
- Do not use plain `uvx cairn`; that resolves an unrelated package. Use
  `uvx --from agentcairn cairn ...`.
- Use `--vault` only for MCP hosts. Configure plugin-host vaults through the
  plugin or AgentCairn's shared configuration.
- Do not install or repair every detected host unless the user explicitly asks.
- Do not write a test memory without the user's approval.

## 1. Establish the target

Check whether AgentCairn's `recall` and `remember` MCP tools are already loaded.
If they are available and the user did not request an upgrade or repair, report
that the full integration is active and move to verification.

Identify the current or requested host. Use these installer IDs:

| Host | Installer ID | Integration |
|---|---|---|
| Claude Code | `claude-code` | Native plugin, MCP tools, skill, and hooks |
| Codex | `codex` | Native plugin, MCP tools, skill, and hooks |
| Cursor | `cursor` | MCP configuration and memory skill |
| OpenCode | `opencode` | MCP configuration, commands, and native plugin |
| Antigravity | `antigravity` | Native plugin; requires a local plugin source |
| VS Code / Copilot | `vscode` | MCP configuration |
| Claude Desktop | `claude-desktop` | MCP configuration |
| Gemini CLI | `gemini` | MCP configuration |

Treat Hermes Agent separately because it uses a native `MemoryProvider`, not
`cairn install`:

```bash
hermes plugins install ccf/agentcairn/integrations/hermes
hermes memory setup agentcairn
```

If the target is still ambiguous after inspecting the current harness and
installed CLIs, ask the user which one to configure. Never choose `--all` as a
shortcut.

## 2. Check the prerequisite

Run:

```bash
uvx --version
```

If `uvx` is missing, explain that AgentCairn uses Astral's `uv` and ask before
installing a system-level prerequisite. Do not silently run a remote installer.

Confirm the AgentCairn package and CLI resolve:

```bash
uvx --from agentcairn cairn --version
```

## 3. Preview without changing host configuration

Use discovery mode to list detected hosts. It writes no AgentCairn host
configuration, but detection proves only that a host is present—not that
AgentCairn is installed or healthy:

```bash
uvx --from agentcairn cairn install
```

For a plugin host, preview the exact target:

```bash
uvx --from agentcairn cairn install <host> --print
```

This prints only the native plugin commands. For MCP hosts, the same `--print`
operation prints the complete merged configuration and may include credentials
from unrelated MCP servers. Do not run it directly through a chat-visible tool
or paste its output into an agent transcript. Ask the user to review it in
their own terminal, or redirect it to a mode-`0600` temporary file, inspect only
the AgentCairn entry locally, and remove the temporary file.

For Antigravity, first locate a local checkout of the AgentCairn plugin and
preview with its path:

```bash
uvx --from agentcairn cairn install antigravity --source <repo>/plugin --print
```

Summarize the plugin commands or configuration path shown by the preview
without exposing unrelated configuration values. If the user has not already
authorized the installation or repair, get approval before continuing.

## 4. Apply one targeted installation

Run the same operation without `--print`:

```bash
uvx --from agentcairn cairn install <host>
```

For Antigravity, retain the reviewed local source:

```bash
uvx --from agentcairn cairn install antigravity --source <repo>/plugin
```

For Claude Code and Codex, let the installer delegate to the host's plugin CLI.
Confirm the requested plugin host's CLI is available before applying. For MCP
hosts, let the installer perform its backup-first, merge-preserving
configuration write. Treat a failed command as a failed install; report its
last useful error instead of continuing as though setup succeeded.

## 5. Verify the full integration

Verify each layer that the host supports:

1. Confirm the install command exited successfully.
2. For Claude Code, run `claude plugin list --json`; for Codex, run
   `codex plugin list --json`. Confirm `agentcairn` is installed and enabled.
3. For an MCP-configured host, confirm its AgentCairn entry matches the
   privately reviewed targeted `--print` preview without removing unrelated
   configuration.
4. Restart the host. A running session cannot reliably hot-load a new plugin,
   MCP server, skill, or hook.
5. In a fresh session, confirm the AgentCairn `search`, `recall`,
   `build_context`, `recent`, and `remember` tools are present. Use `recent` or
   a focused read-only recall to exercise the MCP server. Ask before writing
   and recalling a synthetic memory.
6. If a vault index already exists, optionally run
   `uvx --from agentcairn cairn doctor --vault <vault>` to check index health.
   Do not treat an empty, not-yet-indexed vault as a plugin failure.

Report partial support honestly. MCP tools working does not prove ambient hooks
loaded; a setup skill loading does not prove the MCP tools exist.

## Repair or upgrade

Start again from the targeted `--print` preview and preserve the same host ID
and vault. For MCP hosts, rerun the targeted installer; its writes are
idempotent, backup-first, and preserve unrelated servers.

For native plugin hosts, refresh through the host's own plugin manager before
restarting:

```bash
# Codex
codex plugin marketplace upgrade agentcairn
codex plugin add agentcairn@agentcairn

# Claude Code
claude plugin marketplace update agentcairn
claude plugin update agentcairn@agentcairn
```

Do not remove an existing integration, delete a vault, or overwrite a malformed
configuration as a repair shortcut. Surface the conflict and ask before any
destructive recovery.
