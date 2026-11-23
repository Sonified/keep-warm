#!/usr/bin/env python3
"""keep-warm across threads, from any one window:

    warmlist.py list <caller-session>             number every thread keep-warm is on for, one line each
    warmlist.py cool <caller-session> <n> [n...]   switch keep-warm off for those numbers from the last list

"list" writes the numbering to ~/.claude/keep-warm/list.last (one session id per line), so "cool 2" means the
thread shown as 2 in the last list, even if another thread has expired since. Switching off is removing that
thread's on.<session> file, exactly what "cold" does there; its own window sees it within 30 seconds and its line
turns to back burner. Nothing here sends a request.
"""
import datetime as dt, json, os, re, sys, time

STATE = os.path.expanduser('~/.claude/keep-warm')
SEP = ' │ '   # between fields: the panel collapses padding, so no columns
LAST = os.path.join(STATE, 'list.last')


def setting(name, default):
    """A number from common.sh, overridden by ~/.claude/keep-warm/config, as ctl.sh reads them."""
    value = default
    for path in (os.path.join(os.path.dirname(os.path.abspath(__file__)), 'common.sh'), os.path.join(STATE, 'config')):
        try:
            with open(path) as fh:
                for line in fh:
                    m = re.match(rf'^{name}=(\d+)\s*(?:#.*)?$', line.strip())
                    if m:
                        value = int(m.group(1))
        except OSError:
            pass
    return value
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from last_request import last_reply, model_name  # noqa: E402


TTL = 3600   # the prompt cache's hour: a request more than this after the last one finds it gone, unless a ping kept it
# $ per million tokens: input, output, cache read. A one-hour cache write is 2x input, a five-minute one 1.25x.
PRICES = {'fable-5-2': (8, 40, 0.20), 'mythos-5-2': (8, 40, 0.20), 'fable-5-1': (10, 50, 0.25),
          'mythos-5-1': (10, 50, 0.25), 'fable-5': (10, 50, 1.00), 'mythos-5': (10, 50, 1.00),
          'opus-5-5': (4, 20, 0.20), 'opus-5': (5, 25, 0.50), 'sonnet-5-5': (2, 10, 0.10), 'sonnet-5': (2, 10, 0.20),
          'haiku-5-5': (0.10, 0.50, 0.01)}


def price(model):
    m = re.match(r'claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?!\d)', model or '')
    return PRICES.get(f'{m.group(1)}-{m.group(2)}' + (f'-{m.group(3)}' if m.group(3) else '')) if m else None


def cost(u, p):
    """What one request cost, from its usage record."""
    inp, out, read = p
    made = u.get('cache_creation') or {}
    hour, five = made.get('ephemeral_1h_input_tokens'), made.get('ephemeral_5m_input_tokens')
    if hour is None and five is None:
        hour, five = u.get('cache_creation_input_tokens') or 0, 0
    return ((u.get('input_tokens') or 0) * inp + (five or 0) * inp * 1.25 + (hour or 0) * inp * 2
            + (u.get('cache_read_input_tokens') or 0) * read + (u.get('output_tokens') or 0) * out) / 1e6


def ledger(transcript, since=0):
    """What keep-warm has come to in this thread since `since`: dollars saved minus dollars spent on pings.
    Spent: every ping's real cost. Saved: each time the person came back more than an hour after their last real
    request, with keep-warm having pinged in between, the cache they read would otherwise have been written again,
    so that return saved its cached tokens x (one-hour write price - read price). A thread nobody comes back to
    only spends, which is the honest number."""
    saved = spent = 0.0
    kind, last_real, pinged, seen = 'real', 0, False, set()
    try:
        fh = open(transcript, 'rb')
    except OSError:
        return 0.0
    with fh:
        for line in fh:
            is_user = b'"type":"user"' in line and b'"tool_result"' not in line
            if not is_user and b'"usage"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not isinstance(d, dict) or d.get('isSidechain'):
                continue
            if d.get('type') == 'user':
                if d.get('isMeta'):
                    continue
                if (d.get('pluginPrompt') or {}).get('name') == 'keep-warm' or \
                        (d.get('origin') or {}).get('name') == 'keep-warm':
                    kind = 'ping'
                else:
                    kind = 'real'
                continue
            m = d.get('message')
            if d.get('type') != 'assistant' or not isinstance(m, dict) or not m.get('usage') or not d.get('timestamp'):
                continue
            if m.get('id') in seen or m.get('model') == '<synthetic>':
                continue                                        # one request is written as several records
            seen.add(m.get('id'))
            p = price(m.get('model'))
            if p is None:
                continue
            t = dt.datetime.fromisoformat(d['timestamp'].replace('Z', '+00:00')).timestamp()
            if kind == 'ping':
                if t >= since:
                    spent += cost(m['usage'], p)
                    pinged = True
                continue
            if pinged and last_real and t - last_real > TTL and t >= since:
                saved += (m['usage'].get('cache_read_input_tokens') or 0) * (p[0] * 2 - p[2]) / 1e6
            last_real, pinged = t, False
    return saved - spent


def dot(n):
    """The size's colour, the same as the "tokens" line: green to 300k, yellow to 400k, orange to 500k, red above."""
    return '🟢' if n <= 300_000 else '🟡' if n <= 400_000 else '🟠' if n <= 500_000 else '🔴'


def money(v):
    return f"{'−' if v < -0.005 else ''}${abs(v):.2f}"


def safe_id(s):
    return re.sub(r'[^A-Za-z0-9._-]', '_', s)


def log(text):
    try:
        with open(os.path.join(STATE, 'log'), 'a') as fh:
            fh.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except OSError:
        pass


def title(transcript):
    """The thread's name: the one the person gave it, else the automatic one, else the project folder."""
    custom = auto = ''
    try:
        with open(transcript, 'rb') as fh:
            for line in fh:
                if b'-title"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get('type') == 'custom-title' and d.get('customTitle'):
                    custom = d['customTitle']
                elif d.get('type') == 'ai-title' and d.get('aiTitle'):
                    auto = d['aiTitle']
    except OSError:
        pass
    return custom or auto or '(untitled)'


def project(transcript):
    folder = os.path.basename(os.path.dirname(transcript))      # e.g. -Users-you-GitHub-myproject
    return folder.split('-')[-1] or folder


def warm_threads():
    """(switch-on time, session file id, transcript) for each thread keep-warm is on for, oldest first."""
    out = []
    for name in os.listdir(STATE):
        if not name.startswith('on.'):
            continue
        try:
            with open(os.path.join(STATE, name)) as fh:
                armed, transcript = (fh.read().split('\n') + ['', ''])[:2]
            out.append((int(armed or 0), name[3:], transcript))
        except (OSError, ValueError):
            continue
    return sorted(out)


def ago(seconds):
    """'12 min', '3h 05m', '1d 17h'."""
    m = int(seconds // 60)
    if m < 60:
        return f'{m} min'
    if m < 24 * 60:
        return f'{m // 60}h {m % 60:02d}m'
    return f'{m // 1440}d {m % 1440 // 60}h'


def row(i, sid, transcript, when, tokens, model, net, caller):
    """One row: number, model, size, warm idle, off-after, repo, name, split by dividers. No column padding: the VS Code panel collapses
    every kind of space (no-break ones too) and draws U+2800 as dots, so a divider is what stays readable."""
    cells = [model_name(model),
             f'{dot(tokens)} {round(tokens / 1000)}k' if tokens >= 1000 else '',
             f'⏱️ {ago(time.time() - when)}' if when else '',   # since warm or the last message
             f'❄️ {round(setting("MAX", 86400) / 3600)}h',     # switches itself off after this long without a message
             project(transcript),
             title(transcript) + (' ← 👋 this thread' if sid == caller else ''),
             f'{money(net)} saved']                                # since keep-warm was switched on here
    return f'{i}. ' + SEP.join(c for c in cells if c)


def do_list(caller):
    threads = []
    for armed, sid, t in warm_threads():
        _, tokens, model = last_reply(t) if os.path.exists(t) else (0, 0, '')
        try:                                             # the person's last message while warm (stamp.sh)
            with open(os.path.join(STATE, 'last_user.' + sid)) as fh:
                said = int(fh.read().strip() or 0)
        except (OSError, ValueError):
            said = 0
        # ⏱️ the warm idle timer, the same clock ctl.sh ends keep-warm by: from switch-on or the last message
        threads.append((sid, t, max(armed, said), tokens, model, ledger(t, armed)))
    if not threads:
        return 'keep-warm is not on for any thread'
    threads.sort(key=lambda th: -th[3])                         # most expensive first: each ping re-reads it all
    with open(LAST, 'w') as fh:
        fh.write(''.join(th[0] + '\n' for th in threads))
    lines = [row(i, *th, caller) for i, th in enumerate(threads, 1)]
    return '\n'.join([f'warm 🔥 ({len(threads)}):'] + lines +
                     [f'"cool 2 4" takes those off keep-warm{SEP}"com 2 4" compacts them{SEP}Total saved: {money(sum(th[5] for th in threads))}'])


def do_cool(caller, numbers):
    try:
        with open(LAST) as fh:
            listed = [s for s in fh.read().split('\n') if s]
    except OSError:
        return 'type "warm list" first, so the numbers mean something'
    done, skipped = [], []
    for n in numbers:
        if not 1 <= n <= len(listed):
            skipped.append(f'{n} (not in the last list, which had {len(listed)})')
            continue
        sid = listed[n - 1]
        on = os.path.join(STATE, 'on.' + sid)
        try:
            with open(on) as fh:
                transcript = (fh.read().split('\n') + ['', ''])[1]
            os.remove(on)
        except OSError:
            skipped.append(f'{n} (already off)')
            continue
        log(f'switched off for {sid} (cool {n}, from {caller})')
        done.append(f'{n}. {project(transcript)} · {title(transcript)}' + ('  ← 👋 this thread' if sid == caller else ''))
    parts = []
    if done:
        parts.append('cooled to back burner:\n' + '\n'.join(done))
    if skipped:
        parts.append('skipped: ' + ', '.join(skipped))
    return '\n'.join(parts) or 'nothing to cool'


def main(argv):
    if len(argv) < 3 or argv[1] not in ('list', 'cool'):
        return 'usage: warmlist.py list|cool <caller-session> [n...]'
    caller = safe_id(argv[2])
    if argv[1] == 'list':
        return do_list(caller)
    return do_cool(caller, [int(n) for n in argv[3:] if n.isdigit()])


if __name__ == '__main__':
    os.makedirs(STATE, exist_ok=True)
    print(main(sys.argv))
