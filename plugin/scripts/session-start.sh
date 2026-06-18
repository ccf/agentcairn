#!/bin/sh
# args: $1 = vault path (index is vault-derived; no index arg is passed).
# stdin = hook JSON (unused).
# Emits SessionStart additionalContext with a compact recent-memory digest
# (global / cross-project — see the using-agentcairn-memory skill).
# Always exits 0 (never blocks/delays the session); no output when there's nothing.
set -u
VAULT=$(printf '%s' "${1:-$HOME/agentcairn}" | sed "s#^~#$HOME#")
CAIRN="uvx --from agentcairn>=0.2 cairn"

# Ensure the vault dir exists on every session (uvx-free, instant) so a
# mid-session `remember` always has somewhere to land — even on the warm path
# where the user removed the vault while a stale index remains.
mkdir -p "$VAULT" 2>/dev/null || true

# First run (no index yet): there is nothing to surface, and the very first
# `uvx` call cold-installs agentcairn — which can exceed the SessionStart hook
# timeout. So don't block the session: scaffold the vault (.obsidian) and warm
# the uvx cache in a fully-detached background job (stdin/stdout/stderr detached
# so it can't hold the hook's pipes open and block the caller on EOF), then
# exit. By the next session the index exists and the cache is warm, so the fast
# digest path below runs in well under a second.
# We can't derive the vault-scoped index path in shell, so use a cheap proxy:
# if neither the scoped-index dir nor the legacy index exist, it's a genuine
# first run. After the first sweep, indexes/<key>.duckdb is created so the dir
# exists and the fast digest path runs on every subsequent session.
if [ ! -d "$HOME/.cache/agentcairn/indexes" ] && [ ! -f "$HOME/.cache/agentcairn/index.duckdb" ]; then
  ( $CAIRN init "$VAULT"; $CAIRN warm ) </dev/null >/dev/null 2>&1 &
  exit 0
fi

# Fetch recent memories as JSON (best-effort, cross-project).
JSON=$($CAIRN recent --vault "$VAULT" -n 5 --json 2>/dev/null || echo '{"notes":[]}')

# Format a compact digest; emit nothing if no notes.
LINES=$(printf '%s' "$JSON" | python3 -c '
import json,sys
try:
    notes=json.load(sys.stdin).get("notes",[])
except Exception:
    notes=[]
for n in notes:
    t=n.get("title") or n.get("permalink")
    print(f"- {t}")
' 2>/dev/null || true)

# Cumulative savings one-liner (empty when there are no recorded recalls).
SAVINGS=$($CAIRN savings --oneline 2>/dev/null || true)

# Nothing to surface at all → emit nothing.
[ -z "$LINES" ] && [ -z "$SAVINGS" ] && exit 0

CTX=""
[ -n "$SAVINGS" ] && CTX="$SAVINGS
"
if [ -n "$LINES" ]; then
  CTX="$CTX## agentcairn — recent memory
$LINES

(Use the \`recall\` tool to pull full notes.)"
fi
python3 -c '
import json,sys
print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":sys.argv[1]}}))
' "$CTX" 2>/dev/null || true
exit 0
