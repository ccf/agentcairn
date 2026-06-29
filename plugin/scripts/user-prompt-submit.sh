#!/bin/sh
# UserPromptSubmit hook. Runs a hybrid recall against the user's prompt and
# prints it as additionalContext for this turn. SYNCHRONOUS — its stdout IS the
# injected context (do not detach it). Fail-open: `cairn recall-hook` always
# exits 0 and emits nothing on any problem, so it never blocks or breaks a
# prompt. The 10s hook timeout is the safety ceiling; SessionStart pre-warms the
# embedder so the steady-state path is ~1s. stdin = the UserPromptSubmit hook
# JSON (the prompt), inherited by the command.
set -u
VAULT=$(printf '%s' "${CLAUDE_PLUGIN_OPTION_VAULT_PATH:-${1:-$HOME/agentcairn}}" | sed "s#^~#$HOME#")
CAIRN="uvx --from agentcairn>=0.2 cairn"
$CAIRN recall-hook --vault "$VAULT" 2>/dev/null
exit 0
