#!/bin/sh
# args: $1 = vault path (index is vault-derived; no index arg is passed).
# stdin = hook JSON (has "cwd").
# Distills the current session into the vault (incremental; dedup-ledger gated).
# Wired to both SessionEnd and PreCompact (hooks.json): PreCompact captures
# long/resumed sessions at each compaction boundary, before context is discarded
# — without it capture would only fire when a session formally ends.
# Always exits 0; never blocks teardown/compaction beyond the hook timeout.
set -u
VAULT=$(printf '%s' "${1:-$HOME/agentcairn}" | sed "s#^~#$HOME#")
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
