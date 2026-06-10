#!/bin/sh
# args: $1 = vault path, $2 = index path. stdin = hook JSON (has "cwd").
# Distills the just-ended session into the vault (incremental; dedup-ledger gated).
# Always exits 0; never blocks teardown beyond the hook timeout.
set -u
VAULT=$(printf '%s' "${1:-$HOME/agentcairn}" | sed "s#^~#$HOME#")
INDEX=$(printf '%s' "${2:-$HOME/.cache/agentcairn/index.duckdb}" | sed "s#^~#$HOME#")
CAIRN="uvx --from agentcairn>=0.2 cairn"
INPUT=$(cat 2>/dev/null || true)
CWD=$(printf '%s' "$INPUT" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

[ -d "$VAULT" ] || $CAIRN init "$VAULT" >/dev/null 2>&1 || true
$CAIRN sweep --vault "$VAULT" --index "$INDEX" ${CWD:+--project "$CWD"} >/dev/null 2>&1 || true
exit 0
