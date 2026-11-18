#!/usr/bin/env bash
# keep-warm switch:  ctl.sh on | off | status | arm | tick | peek | size   [session-id]
#   on, off, status   run by the plugin's "on" and "off" skills, from the Bash tool (the shell knows the session id)
#   arm, tick, peek   run by the plugin's hooks module (hooks/register.ts), which passes the session id:
#     arm    switch on, one line of output ("on")
#     tick   the module's look, every 30 s. Prints one word and, while on, the switch-on time:
#              off | wait <since> <tokens> | ping <since> <tokens> | expired     (tokens: the thread's size)
#            "ping" means the module should send the ping now (the last request is IDLE seconds old and nothing
#            has pinged within IDLE); "expired" means MAX seconds passed without a message from the user, and
#            keep-warm has just been switched off.
#     peek   the same look without ever answering "ping" (used while a turn is running)
#     size   the thread's size in tokens, whether keep-warm is on or off (for the back-burner line)
#   Each tick or peek also writes alive.<session>: the proof that the hooks module is watching this session.
set -uo pipefail
. "$(dirname "$0")/common.sh"
raw="${2:-${CLAUDE_CODE_SESSION_ID:-${CLAUDE_SESSION_ID:-}}}"
[ -n "$raw" ] || { echo "keep-warm: the shell has no session id (CLAUDE_CODE_SESSION_ID), so it cannot tell which conversation to keep warm"; exit 1; }
sid="$(safe_id "$raw")"
on="$STATE/on.$sid"
pf="$STATE/monitor.$sid.pid"; mark="$STATE/monitor.$sid.once"; alive="$STATE/alive.$sid"
ALIVE_FOR=90   # the module looks every 30 s; older than this and it is not watching
watcher() {
  if [ -f "$alive" ] && [ $(( $(date +%s) - $(num "$(cat "$alive" 2>/dev/null)") )) -lt "$ALIVE_FOR" ]; then
    echo "running (in-app timer)"
  elif [ -f "$pf" ] && kill -0 "$(num "$(cat "$pf" 2>/dev/null)")" 2>/dev/null; then
    if [ -f "$mark" ]; then echo "running (pid $(cat "$pf"), background task)"; else echo "running (pid $(cat "$pf"))"; fi
  else echo "not running"; fi
}
# A watcher started as a background task (monitor.sh --once) is stopped outright: unlike a plugin monitor, the skill
# can start a new one whenever it is needed, and a leftover one may belong to a Claude Code process that is gone.
stop_task_watcher() {
  [ -f "$mark" ] || return 0
  p="$(num "$(cat "$pf" 2>/dev/null)")"
  [ "$p" -gt 1 ] && kill "$p" 2>/dev/null
  rm -f "$mark" "$pf"
}
# Write on.<session>: line 1 the time it was switched on, line 2 the session's transcript file.
arm() {
  transcript="$(ls "$HOME"/.claude/projects/*/"$raw".jsonl 2>/dev/null | head -n 1)"
  [ -n "$transcript" ] || { echo "keep-warm: no transcript file found for session $raw; not switched on"; return 1; }
  printf '%s\n%s\n' "$(date +%s)" "$transcript" > "$on"
  rm -f "$STATE/last_ping.$sid"
  find "$STATE" -name 'alive.*' -mmin +1440 -delete 2>/dev/null || true     # sessions that ended long ago
  log "switched on for $sid"
}
case "${1:-status}" in
  on)
    stop_task_watcher
    arm || exit 1
    for _ in 1 2 3 4 5 6; do                # in the terminal the plugin's watcher claims the session within seconds
      case "$(watcher)" in running*) break ;; esac
      sleep 1
    done
    echo "keep-warm: ON for this conversation"
    echo "  first ping: about 50 minutes after the last request here; then again each time it has been idle that long"
    echo "  switches itself off after $((MAX / 3600)) hours without a message from the user"
    echo "  settings: idle ${IDLE}s"
    echo "  watcher: $(watcher)"
    ;;
  arm)
    arm || exit 1
    echo "on"
    ;;
  off)
    rm -f "$on"
    stop_task_watcher
    log "switched off for $sid"
    echo "keep-warm: OFF for this conversation"
    ;;
  tick|peek)
    now="$(date +%s)"
    printf '%s' "$now" > "$alive"
    [ -f "$on" ] || { echo "off"; exit 0; }
    armed="$(num "$(sed -n 1p "$on")")"
    transcript="$(sed -n 2p "$on")"
    lu="$(num "$(cat "$STATE/last_user.$sid" 2>/dev/null)")"
    [ "$lu" -ge "$armed" ] || lu="$armed"
    if [ $((now - lu)) -ge "$MAX" ]; then
      rm -f "$on"
      log "no message from the user for $(( (now - lu) / 3600 )) h: keep-warm switched off for $sid"
      echo "expired"; exit 0
    fi
    # The cache's hour runs from the last request, which is the last reply in the transcript. The file's own
    # change time can be later than that (a message a hook stopped is written with no request made), so it is
    # only the fallback. The same reply's record gives the thread's size in tokens.
    read -r last tok <<< "$(python3 "$(dirname "$0")/last_request.py" "$transcript" 2>/dev/null)"
    last="$(num "${last:-}")"; tok="$(num "${tok:-}")"
    [ "$last" -gt 0 ] || last="$(num "$(mtime "$transcript")")"
    if [ "$1" = tick ]; then
      lp="$(num "$(cat "$STATE/last_ping.$sid" 2>/dev/null)")"
      # For testing: test_idle.<session> holds a number of seconds to use in place of IDLE for ONE ping of this
      # session; it is removed when that ping goes out, so it can never leave a session pinging every minute.
      idle="$IDLE"; tf="$STATE/test_idle.$sid"
      if [ -f "$tf" ]; then t="$(num "$(cat "$tf" 2>/dev/null)")"; [ "$t" -gt 0 ] && idle="$t"; fi
      if [ "$last" -gt 0 ] && [ $((now - last)) -ge "$idle" ] && [ $((now - lp)) -ge "$idle" ]; then
        rm -f "$tf"
        printf '%s' "$now" > "$STATE/last_ping.$sid"
        log "idle $(( (now - last) / 60 )) min: in-app ping for $sid"
        echo "ping $armed $tok"; exit 0
      fi
    fi
    echo "wait $armed $tok"
    ;;
  size)
    # The thread's size in tokens, on or off (the back-burner line). Run after a turn, not on every look.
    if [ -f "$on" ]; then transcript="$(sed -n 2p "$on")"
    else transcript="$(ls "$HOME"/.claude/projects/*/"$raw".jsonl 2>/dev/null | head -n 1)"; fi
    [ -n "$transcript" ] || { echo 0; exit 0; }
    read -r _ tok <<< "$(python3 "$(dirname "$0")/last_request.py" "$transcript" 2>/dev/null)"
    num "${tok:-}"
    ;;
  status)
    if [ -f "$on" ]; then
      now="$(date +%s)"; last="$(num "$(mtime "$(sed -n 2p "$on")")")"
      echo "keep-warm: ON; idle $(( (now - last) / 60 )) min; watcher $(watcher)"
    else
      echo "keep-warm: OFF; watcher $(watcher)"
    fi
    tail -n 5 "$STATE/log" 2>/dev/null
    ;;
  *) echo "usage: ctl.sh on|off|status|arm|tick|peek|size [session-id]" >&2; exit 2 ;;
esac
