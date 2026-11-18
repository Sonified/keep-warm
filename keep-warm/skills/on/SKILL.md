---
name: on
description: Keep this conversation's prompt cache alive while the user is away, so their next message is a cheap cache read instead of a full rewrite. Use when the user says "keep warm", "keep this thread warm", "keep the cache warm" or similar.
---

The scripts are in the plugin's `scripts` folder, two levels up from this skill's base directory (named above
as "Base directory for this skill"). Below, `SCRIPTS` stands for that folder: write the real path in each command.

The user is stepping away and wants this conversation to stay cheap to come back to. A conversation's prompt cache
lasts one hour from the start of the last request; after that the next message pays to write the whole conversation
again instead of reading it. A do-nothing turn before the hour is up is a cheap read, and a read restarts the hour.

Those turns come from a watcher that pings only when the last request here is 50 minutes old. While the user and
Claude are talking it stays silent. The user asked for the watcher; nothing else counts as keep-warm working.

Usually the user never reaches this skill. When their whole message is `warm` or `keep warm`, the plugin's in-app code
(hooks/register.ts) switches keep-warm on by itself and stops the message before it reaches the model; `cold` and
`cool` switch it off the same way. This skill is for when they ask in other words, or when this Claude Code has
not loaded that code.

## 1. Switch it on

```bash
bash "SCRIPTS/ctl.sh" on
```

If the last line says `watcher: running`, something is already watching this conversation: the plugin's in-app timer
(`running (in-app timer)`) or, in the terminal, the plugin's monitor. Go to step 3.

## 2. Start the watcher yourself (only when nothing is watching)

If the last line says `watcher: not running`, start it with the Bash tool and `run_in_background: true`:

- command: `bash "SCRIPTS/monitor.sh" --once`
- description: `keep-warm watcher (exit code 0 is a ping: start it again and reply "ok")`

Run that command alone, with nothing added to it. The script exits when it is time to ping, and a finished background
task starts a turn in an idle conversation. Then check it:

```bash
bash "SCRIPTS/ctl.sh" status
```

The first line must say `watcher running`. If it does not, tell the user plainly that the watcher did not start and
what the command printed. Never report keep-warm as working without a running watcher, and never substitute scheduled
(cron) pings without asking first: they fire on the clock whether or not the conversation is idle.

## 3. Tell the user

`CronList`, and `CronDelete` any job whose prompt begins `Cache keep-alive` or `Keep-warm stop` (left by an older
version). Then two short lines: it is on; the first ping comes about 50 minutes after the last request and none come
while they are talking; it switches itself off after 24 hours without a message from them, and "stop warm" ends it
sooner. With the in-app timer, add that typing just `warm` does this at no cost next time, and that it carries on by
itself after a window reload. If step 2 started the watcher, add instead that it ends when Claude Code restarts or
the window reloads, so "keep warm" has to be said again after that.

## When the watcher fires

- A message from the keep-warm plugin, or a line, beginning `keep-warm ping:`: reply `ok` and nothing else.
- A notice that the background task `keep-warm watcher` completed with exit code 0 (started in step 2): start it again
  exactly as in step 2, one Bash call and no other tool, then reply `ok` and nothing else. Do not read its output file.
- The same task ending with any other exit code: do not start it again. Reply with one line: `keep-warm ended`
  (3 = 24 hours passed or it was switched off, 4 = it was stopped, 2 = it could not start).
- A line beginning `Cache keep-alive:` (an older cron job): reply `ok` and nothing else.

Every extra token and every tool call is paid for on each ping.

## Do not start it when it cannot help

If the session is on the 5-minute cache (this happens in usage overage; a tool description in the session says which
cache is in use), every ping would miss and pay a full rewrite. Say so instead of starting it.
