# keep-warm: shared by monitor.sh, ctl.sh and stamp.sh. State lives in ~/.claude/keep-warm/ (small text files):
#   on.<session>          line 1: when it was switched on (epoch seconds); line 2: the session's transcript file
#   last_user.<session>   when the user last sent a message (written by the UserPromptSubmit hook)
#   last_ping.<session>   when the watcher last pinged
#   monitor.<session>.pid the watcher's process id
#   log                   one line per event
#   config                optional, numbers only:  IDLE=3000  TICK=300  MAX=86400   (for testing with short times)
STATE="$HOME/.claude/keep-warm"
mkdir -p "$STATE" 2>/dev/null || true
IDLE=3000      # seconds without activity before a ping (50 min: the cache lasts 60)
TICK=300       # how often the watcher looks (so a ping fires 50 to 55 minutes after the last activity)
MAX=86400      # switch off after this long without a message from the user (24 h)
PING='keep-warm ping: reply with the single word "ok" and do nothing else. No tool calls.'
if [ -f "$STATE/config" ]; then                     # read numbers only; the file is never run as code
  for k in IDLE TICK MAX; do
    v="$(sed -n "s/^$k=\([0-9][0-9]*\)[[:space:]]*$/\1/p" "$STATE/config" | tail -n 1)"
    [ -n "$v" ] && eval "$k=$v"
  done
fi
log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$STATE/log" 2>/dev/null || true; }
mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0; }
num() { case "${1:-}" in ''|*[!0-9]*) echo 0 ;; *) echo "$1" ;; esac; }
safe_id() { printf '%s' "${1:-}" | tr -c 'A-Za-z0-9._-' '_'; }
