#!/usr/bin/env python3
"""Print two numbers for a Claude Code transcript, from the last main-thread reply that carries a usage record:
the time of that request (epoch seconds: the moment the prompt cache's hour was last restarted) and the size of
the thread in tokens (what that request sent: read from the cache + written to it + uncached input).
Prints "0 0" when there is none. Reads the file from the end, so a long transcript costs no more than a short one.

    python3 last_request.py <transcript.jsonl>
"""
import datetime as dt, json, os, re, sys


def last_request(path):
    when, size, _ = last_reply(path)
    return when, size


def model_name(model):
    """'claude-fable-5-2' -> 'Fable 5.2', 'claude-opus-5-5' -> 'Opus 5.5', 'claude-fable-5' -> 'Fable 5'."""
    m = re.match(r'claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?!\d)', model or '')
    if not m:
        return model or ''
    return f"{m.group(1).capitalize()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else '')


def last_reply(path):
    """(time, size in tokens, model id) of the last main-thread reply with a usage record; (0, 0, '') if none."""
    size = os.path.getsize(path)
    with open(path, 'rb') as fh:
        for back in (2 << 20, 16 << 20, size):
            fh.seek(max(0, size - back))
            lines = fh.read().split(b'\n')
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
                        and m.get('model') != '<synthetic>' and d.get('timestamp'):
                    u = m['usage']
                    size = (u.get('cache_read_input_tokens') or 0) + (u.get('cache_creation_input_tokens') or 0) + (u.get('input_tokens') or 0)
                    return int(dt.datetime.fromisoformat(d['timestamp'].replace('Z', '+00:00')).timestamp()), size, \
                        m.get('model') or ''
            if back >= size:
                break
    return 0, 0, ''


if __name__ == '__main__':                              # warmlist.py imports last_request()
    try:
        print(*last_request(sys.argv[1]))
    except Exception:
        print(0, 0)
