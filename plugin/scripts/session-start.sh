#!/bin/sh
# args: $1 = vault path, $2 = index path. stdin = hook JSON (unused).
# Emits SessionStart additionalContext with a compact recent-memory digest
# (global / cross-project — see the using-agentcairn-memory skill).
# Always exits 0 (never blocks/delays the session); no output when there's nothing.
set -u
VAULT=$(printf '%s' "${1:-$HOME/agentcairn}" | sed "s#^~#$HOME#")
INDEX=$(printf '%s' "${2:-$HOME/.cache/agentcairn/index.duckdb}" | sed "s#^~#$HOME#")
CAIRN="uvx --from agentcairn>=0.2 cairn"

# First run (no index yet): there is nothing to surface, and the very first
# `uvx` call cold-installs agentcairn — which can exceed the SessionStart hook
# timeout. So don't block the session: create the vault and warm the uvx cache
# in the BACKGROUND (stdout/stderr detached so the hook returns immediately),
# then exit. By the next session the index exists and the cache is warm, so the
# fast digest path below runs in well under a second.
if [ ! -f "$INDEX" ]; then
  # Make the vault dir exist immediately (uvx-free) so a mid-session `remember`
  # has somewhere to land; the full Obsidian scaffolding + cache-warm runs in
  # the background. Detach the job's stdin/stdout/stderr so it does NOT hold the
  # hook's pipes open — otherwise the caller would block on EOF for the whole
  # cold install, defeating the point.
  mkdir -p "$VAULT" 2>/dev/null || true
  ( $CAIRN init "$VAULT" ) </dev/null >/dev/null 2>&1 &
  exit 0
fi

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
