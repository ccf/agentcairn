#!/bin/sh
# Vault resolution: $CLAUDE_PLUGIN_OPTION_VAULT_PATH (Claude Code exports the
# user's `vault_path` userConfig as this env var) → legacy $1 → ~/agentcairn.
# We do NOT take the vault from a `${user_config.vault_path}` hook arg: Claude
# Code hard-fails that interpolation when the user never set it (fresh install),
# whereas an unset env var simply falls through to the default here.
# stdin = hook JSON (has "cwd").
# Distills the current session into the vault (incremental; dedup-ledger gated).
# Wired to both SessionEnd and PreCompact (hooks.json): PreCompact captures
# long/resumed sessions at each compaction boundary, before context is discarded
# — without it capture would only fire when a session formally ends.
# Always exits 0; never blocks teardown/compaction beyond the hook timeout.
set -u
VAULT=$(printf '%s' "${CLAUDE_PLUGIN_OPTION_VAULT_PATH:-${1:-$HOME/agentcairn}}" | sed "s#^~#$HOME#")
CAIRN="uvx --from agentcairn>=0.2 cairn"
INPUT=$(cat 2>/dev/null || true)
CWD=$(printf '%s' "$INPUT" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

[ -d "$VAULT" ] || $CAIRN init "$VAULT" >/dev/null 2>&1 || true
# Detach: the sweep (and any LLM judge call inside it) must never block session
# teardown. nohup + & detaches fine without an inner `sh -c` — which would
# re-parse the `>=0.2` pin as a redirection and make $CWD/$VAULT an injection
# surface. $CAIRN stays unquoted on purpose (word-splits into argv, no re-parse).
nohup $CAIRN sweep --vault "$VAULT" ${CWD:+--project "$CWD"} >/dev/null 2>&1 &
exit 0
