#!/usr/bin/env python3
"""com across threads: "com 1 3" compacts the threads numbered 1 and 3 in the last "warm list".

    comlist.py ask <caller-session> <n> [n...]

Reads the numbering keep-warm's list wrote (~/.claude/keep-warm/list.last), and for each other thread leaves a
request file, compact.<session>. That thread's own window (the com plugin) sees it within 15 seconds, removes it,
and runs /compact there. Prints JSON: {"self": bool, "asked": [{"n", "sid", "label"}], "skipped": [str]}.
"""
import json, os, re, sys, time

STATE = os.path.expanduser('~/.claude/keep-warm')


# The next three are the same as keep-warm's scripts/warmlist.py. They are copied, not imported: an installed
# plugin lives in its own folder, so this one cannot count on finding keep-warm's scripts next to it.
def safe_id(s):
    return re.sub(r'[^A-Za-z0-9._-]', '_', s)


def title(transcript):
    """The thread's name: the one the person gave it, else the automatic one."""
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


def label(sid):
    try:
        with open(os.path.join(STATE, 'on.' + sid)) as fh:
            transcript = (fh.read().split('\n') + ['', ''])[1]
        return f'{project(transcript)} · {title(transcript)}'
    except OSError:
        return sid[:8]


def ask(caller, numbers):
    out = {'self': False, 'asked': [], 'skipped': []}
    try:
        with open(os.path.join(STATE, 'list.last')) as fh:
            listed = [s for s in fh.read().split('\n') if s]
    except OSError:
        out['skipped'].append('type "warm list" first, so the numbers mean something')
        return out
    for n in dict.fromkeys(numbers):
        if not 1 <= n <= len(listed):
            out['skipped'].append(f'{n} (not in the last list, which had {len(listed)})')
            continue
        sid = listed[n - 1]
        if sid == caller:
            out['self'] = True
        else:
            with open(os.path.join(STATE, 'compact.' + sid), 'w') as fh:
                fh.write(f'{int(time.time())}\n{caller}\n')
        out['asked'].append({'n': n, 'sid': sid, 'label': label(sid)})
    return out


if __name__ == '__main__':
    if len(sys.argv) < 3 or sys.argv[1] != 'ask':
        print(json.dumps({'self': False, 'asked': [], 'skipped': ['usage: comlist.py ask <caller> <n...>']}))
    else:
        print(json.dumps(ask(safe_id(sys.argv[2]), [int(n) for n in sys.argv[3:] if n.isdigit()])))
