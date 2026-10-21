"""Per-account running state, shared by every anyx process: ~/.anyx/run/<handle>.json

    browser      how to reach the browser that stays open for this account (pid, port / ws)
    last         when anyx last made X load something as this account (for pacing)
    cool_until   epoch second before which anyx refuses to act as this account
    cool_reason  why (a rate limit, a refusal X did not explain)

The browser stays open between commands and pages change inside X's app instead of reloading it.
Pausing between actions ("pace") and resting an account after X pushes back ("rest") are off unless
config.json turns them on: how hard to drive an account is the user's call.
"""

import json
import os
import random
import time

from . import config

RUN = os.path.join(config.HOME, "run")
GAP = (0, 0)               # seconds between page changes, unless config.json says "pace"
WRITE_GAP = (0, 0)         # seconds before a write
COOL_LIMIT = 20 * 60       # a 429 without a reset time
COOL_REFUSED = 60 * 60     # X refused something without a clear reason


def _path(handle):
    return os.path.join(RUN, (handle or "_").lower() + ".json")


def load(handle):
    try:
        with open(_path(handle), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(handle, **fields):
    os.makedirs(RUN, exist_ok=True)
    d = load(handle)
    d.update(fields)
    tmp = _path(handle) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, _path(handle))
    return d


def forget(handle, *keys):
    d = load(handle)
    for k in keys:
        d.pop(k, None)
    os.makedirs(RUN, exist_ok=True)
    with open(_path(handle), "w", encoding="utf-8") as f:
        json.dump(d, f)


def pace(handle, write=False):
    """Sleep so that page changes for one account are spaced like a person's.
    Off unless ~/.anyx/config.json has e.g. {"pace": {"page": [2, 5], "write": [6, 14]}}."""
    p = (config.load().get("pace") or {})
    lo, hi = (p.get("write") or WRITE_GAP) if write else (p.get("page") or GAP)
    if not hi:
        return
    last = load(handle).get("last") or 0
    wait = last + random.uniform(lo, hi) - time.time()
    if wait > 0:
        time.sleep(wait)
    save(handle, last=time.time())


def cooling(handle):
    d = load(handle)
    until = d.get("cool_until") or 0
    if until > time.time():
        return until, d.get("cool_reason")
    return None


def cool(handle, seconds=None, until=None, reason=""):
    until = until or (time.time() + (seconds or COOL_LIMIT))
    save(handle, cool_until=until, cool_reason=reason)
    return until


def accounts():
    if not os.path.isdir(RUN):
        return []
    return sorted(f[:-5] for f in os.listdir(RUN) if f.endswith(".json"))
