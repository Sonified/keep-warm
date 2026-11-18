#!/usr/bin/env bash
# keep-warm watcher. It runs in one of two ways:
#   monitor.sh          a plugin monitor (the terminal). Claude Code starts it the first time the "on" skill runs in
#                       a session and keeps it for the life of the session. Every line it prints starts a turn.
#   monitor.sh --once   a background task (the VS Code panel, which does not start plugin monitors). The "on" skill
#                       starts it with the Bash tool, run_in_background. It prints one ping and EXITS, and the
#                       finished task is what starts the turn. Exit codes: 0 = ping (the skill starts it again);
#                       3 = keep-warm ended or was switched off; 4 = stopped by a signal; 2 = could not start.
# It pings only when all of these hold:
#   - keep-warm is on for this session (the file on.<session> exists);
#   - the session's transcript has not changed for IDLE seconds (nothing was said or done, so the cache is ageing);
#   - it has not pinged within the last IDLE seconds.
# So while the user and Claude are talking it stays silent. After MAX seconds without a message from the user it
# switches keep-warm off for the session. As a plugin monitor it then stays alive and silent, so a later "keep warm"
# in the same session works again; with --once it exits.
# No network, no credentials; it reads and writes only ~/.claude/keep-warm/.
set -uo pipefail
. "$(dirname "$0")/common.sh"
once=0; [ "${1:-}" = "--once" ] && once=1

sid=""
if [ "$once" = 1 ]; then
  # A background task runs in the session's own shell, which knows the session id.
  raw="${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-}}"
  [ -n "$raw" ] || { echo "keep-warm: the shell has no session id, so the watcher cannot tell which conversation to watch"; exit 2; }
  sid="$(safe_id "$raw")"
  [ -f "$STATE/on.$sid" ] || { echo "keep-warm is off for this conversation; watcher not started"; exit 2; }
  old="$(num "$(cat "$STATE/monitor.$sid.pid" 2>/dev/null)")"
  if [ "$old" -gt 1 ] && [ "$old" != "$$" ] && kill -0 "$old" 2>/dev/null; then
    kill "$old" 2>/dev/null; log "replaced an earlier watcher (pid $old) for $sid"
    for _ in $(seq 1 30); do kill -0 "$old" 2>/dev/null || break; sleep 0.1; done   # let it remove its own files first
  fi
else
  # A plugin monitor is not told its session. It claims the session that switched keep-warm on just now: the newest
  # on.<session> written around the moment this watcher started and not already watched by a live process.
  start="$(date +%s)"
  for _ in $(seq 1 90); do
    for f in "$STATE"/on.*; do
      [ -f "$f" ] || continue
      s="${f##*/on.}"
      [ "$(mtime "$f")" -ge $((start - 120)) ] || continue
      pf="$STATE/monitor.$s.pid"
      if [ -f "$pf" ] && kill -0 "$(num "$(cat "$pf" 2>/dev/null)")" 2>/dev/null; then continue; fi
      sid="$s"; break
    done
    [ -n "$sid" ] && break
    sleep 2
  done
  [ -n "$sid" ] || { log "watcher found no session to watch within 3 minutes; exiting"; exit 0; }
fi

pf="$STATE/monitor.$sid.pid"; mark="$STATE/monitor.$sid.once"; sp=""
printf '%s' "$$" > "$pf"
if [ "$once" = 1 ]; then : > "$mark"; else rm -f "$mark"; fi
cleanup() { if [ "$(cat "$pf" 2>/dev/null)" = "$$" ]; then rm -f "$pf" "$mark"; fi; }   # never remove a newer watcher's files
trap cleanup EXIT
if [ "$once" = 1 ]; then
  trap '[ -n "$sp" ] && kill "$sp" 2>/dev/null; exit 4' INT TERM
  log "watcher started as a background task for session $sid (idle ${IDLE}s, look every ${TICK}s, off after ${MAX}s)"
else
  trap 'exit 0' INT TERM
  log "watcher started for session $sid (idle ${IDLE}s, look every ${TICK}s, off after ${MAX}s)"
fi

while :; do
  sleep "$TICK" & sp=$!
  wait "$sp" 2>/dev/null || true
  on="$STATE/on.$sid"
  if [ ! -f "$on" ]; then                                    # off
    if [ "$once" = 1 ]; then log "watcher stopped for $sid: keep-warm is off"; echo "keep-warm watcher stopped: it was switched off"; exit 3; fi
    continue                                                 # a plugin monitor stays alive and silent
  fi
  now="$(date +%s)"
  armed="$(num "$(sed -n 1p "$on")")"
  transcript="$(sed -n 2p "$on")"
  last="$(num "$(mtime "$transcript")")"
  [ "$last" -gt 0 ] || { log "transcript not found for $sid: $transcript"; continue; }
  lp="$(num "$(cat "$STATE/last_ping.$sid" 2>/dev/null)")"
  lu="$(num "$(cat "$STATE/last_user.$sid" 2>/dev/null)")"
  [ "$lu" -ge "$armed" ] || lu="$armed"
  if [ $((now - lu)) -ge "$MAX" ]; then
    rm -f "$on"
    log "no message from the user for $(( (now - lu) / 3600 )) h: keep-warm switched off for $sid"
    if [ "$once" = 1 ]; then echo "keep-warm ended: $((MAX / 3600)) hours without a message from the user"; exit 3; fi
    continue
  fi
  if [ $((now - last)) -ge "$IDLE" ] && [ $((now - lp)) -ge "$IDLE" ]; then
    log "idle $(( (now - last) / 60 )) min: ping for $sid"
    printf '%s' "$now" > "$STATE/last_ping.$sid"
    printf '%s\n' "$PING"
    [ "$once" = 1 ] && exit 0                                # the finished task starts the turn
  fi
done
