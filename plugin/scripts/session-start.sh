#!/bin/sh
# args: $1 = vault path, $2 = index path. stdin = hook JSON (unused).
# Emits SessionStart additionalContext with a compact recent-memory digest
# (global / cross-project — see the using-agentcairn-memory skill).
# Always exits 0 (never blocks/delays the session); no output when there's nothing.
set -u
VAULT=$(printf '%s' "${1:-$HOME/agentcairn}" | sed "s#^~#$HOME#")
INDEX=$(printf '%s' "${2:-$HOME/.cache/agentcairn/index.duckdb}" | sed "s#^~#$HOME#")
CAIRN="uvx --from agentcairn>=0.2 cairn"

# Zero-step onboarding: create the vault if missing.
[ -d "$VAULT" ] || $CAIRN init "$VAULT" >/dev/null 2>&1 || true

# Fetch recent memories as JSON (best-effort, cross-project).
JSON=$($CAIRN recent --index "$INDEX" -n 5 --json 2>/dev/null || echo '{"notes":[]}')

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

[ -z "$LINES" ] && exit 0

CTX="## agentcairn — recent memory
$LINES

(Use the \`recall\` tool to pull full notes.)"
python3 -c '
import json,sys
print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":sys.argv[1]}}))
' "$CTX" 2>/dev/null || true
exit 0
