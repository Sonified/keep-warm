#!/usr/bin/env python3
"""tokens: a UserPromptSubmit hook. When the user's whole message is the word "tokens", it reads the size of the
conversation from the usage record of the last reply in the session's transcript and answers the user directly,
blocking the message so it never reaches the model (no request, no cost). Any other message passes through untouched.

The answer is one line to glance at:  dot, tokens, re-read cost, lapse cost, cache minutes left, keep-warm state.
The dot is green to 300k tokens, yellow to 400k, orange to 500k, red above (where compacting pays off fast).

Size = the tokens the last request sent: read from the cache + written to the cache + uncached input. Prices are
list prices per million tokens for the model of that request (cache read, one-hour cache write); a plan may differ.
"""
import datetime as dt, json, os, re, sys

PRICES = {'fable-5-2': (0.20, 16.0), 'mythos-5-2': (0.20, 16.0), 'fable-5-1': (0.25, 20.0), 'opus-5-5': (0.20, 8.0),
          'opus-5': (0.50, 10.0), 'sonnet-5-5': (0.10, 4.0), 'sonnet-5': (0.20, 4.0), 'haiku-5-5': (0.01, 0.20)}


def last_usage(path):
    """(usage, model, timestamp) of the last main-thread reply, reading the file from the end."""
    size = os.path.getsize(path)
    with open(path, 'rb') as fh:
        for back in (2 << 20, 16 << 20, size):
            fh.seek(max(0, size - back))
            chunk = fh.read()
            lines = chunk.split(b'\n')
            if back < size:
                lines = lines[1:]                                # the first line is probably cut
            for line in reversed(lines):
                if b'"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                m = d.get('message') if isinstance(d, dict) else None
                if isinstance(m, dict) and d.get('type') == 'assistant' and not d.get('isSidechain') and m.get('usage') \
                        and m.get('model') != '<synthetic>':
                    return m['usage'], m.get('model', ''), d.get('timestamp', '')
            if back >= size:
                break
    return None, '', ''


def keepwarm(session):
    """'on' when keep-warm is switched on for this session and something is watching it: the plugin's in-app timer
    (it writes alive.<session> every 30 s) or a watcher process. 'stalled' when it is switched on with nothing
    watching, so nothing will ping. 'off' otherwise. Read from the keep-warm plugin's state files."""
    state = os.path.expanduser('~/.claude/keep-warm')
    sid = re.sub(r'[^A-Za-z0-9._-]', '_', session or '')
    if not sid or not os.path.exists(os.path.join(state, 'on.' + sid)):
        return 'off'
    try:
        if dt.datetime.now().timestamp() - int(open(os.path.join(state, 'alive.' + sid)).read().strip()) < 90:
            return 'on \U0001F525'
    except Exception:
        pass
    try:
        os.kill(int(open(os.path.join(state, f'monitor.{sid}.pid')).read().strip()), 0)
        return 'on \U0001F525'
    except Exception:
        return 'stalled \u26A0\uFE0F'


def main():
    try:
        data = json.loads(sys.stdin.read() or '{}')
    except Exception:
        return
    if str(data.get('prompt', '')).strip().strip('?!.').lower() != 'tokens':
        return                                                   # not for us: say nothing, the message goes on
    path = data.get('transcript_path') or ''
    msg = 'tokens: no transcript found for this thread yet.'
    if path and os.path.exists(path):
        u, model, ts = last_usage(path)
        if u:
            n = (u.get('cache_read_input_tokens') or 0) + (u.get('cache_creation_input_tokens') or 0) + (u.get('input_tokens') or 0)
            dot = '\U0001F7E2' if n <= 300_000 else '\U0001F7E1' if n <= 400_000 else '\U0001F7E0' if n <= 500_000 else '\U0001F534'
            parts = [f'{dot} {round(n/1000):,}k tokens' if n >= 1000 else f'{dot} {n} tokens']
            price = next((p for k, p in PRICES.items() if k in model), None)
            if price:
                parts += [f're-read ${n*price[0]/1e6:.3f}', f'lapse ${n*price[1]/1e6:.2f}']
            try:
                age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(ts.replace('Z', '+00:00'))).total_seconds()/60
                parts.append(f'cache {60 - age:.0f} min' if age < 60 else 'cache lapsed \u26A0\uFE0F')
            except Exception:
                pass
            parts.append('keep-warm: ' + keepwarm(data.get('session_id') or os.path.basename(path)[:-len('.jsonl')]))
            msg = ' \u00B7 '.join(parts)
        else:
            msg = 'tokens: this thread has no reply yet, so there is nothing to measure.'
    print(json.dumps({'decision': 'block', 'reason': msg}))


main()
