#!/usr/bin/env bash
# keep-warm: UserPromptSubmit hook. Notes when the user last sent a message, so the watcher can switch itself off
# after 24 hours without one. Does nothing unless keep-warm is on for the session, and never counts the watcher's
# own ping as a message from the user. Prints nothing.
set -uo pipefail
. "$(dirname "$0")/common.sh"
input="$(cat 2>/dev/null || true)"
raw="${CLAUDE_CODE_SESSION_ID:-}"
[ -n "$raw" ] || raw="$(printf '%s' "$input" | grep -o '"session_id"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n 1 | sed 's/.*"\([^"]*\)"$/\1/')"
[ -n "$raw" ] || exit 0
sid="$(safe_id "$raw")"
[ -f "$STATE/on.$sid" ] || exit 0
case "$input" in *"keep-warm ping:"*) exit 0 ;; esac
printf '%s' "$(date +%s)" > "$STATE/last_user.$sid" 2>/dev/null || true
exit 0
