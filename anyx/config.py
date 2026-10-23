"""Where anyx keeps things.

    ~/.anyx/config.json          which browser opens which account
    ~/.anyx/accounts/<handle>.json   the account's x.com cookies
    ~/.anyx/profiles/<handle>/   the Playwright Chromium profile, when that browser is used

ANYX_HOME moves all of it.

config.json:

    {
      "default_browser": "chromium",
      "accounts": {
        "alice": {"browser": "chromium"},
        "bob":   {"browser": "adspower", "profile": "x-bob"},
        "carol": {"browser": "cdp", "cdp": "http://127.0.0.1:9222"}
      },
      "adspower": {"api": "http://local.adspower.net:50325", "key": "..."},
      "pace": {"page": [2, 5], "write": [6, 14]}
    }

`anyx config set <name> <value>` writes it (anyx config set adspower.key KEY), so the AdsPower key
is given once. Without adspower.key the key is read from $ADSPOWER_API_KEY (or the variable named
by adspower.key_env).

Optional, off unless written:
  "pace": {"page": [2, 5], "write": [6, 14]}   pause (random seconds in the range) before page changes / writes
  "rest": true                                  rest an account after X pushes back (429, unexplained refusal)
"""

import json
import os

HOME = os.path.expanduser(os.environ.get("ANYX_HOME", "~/.anyx"))
CONFIG = os.path.join(HOME, "config.json")
ACCOUNTS = os.path.join(HOME, "accounts")
PROFILES = os.path.join(HOME, "profiles")


def load():
    if not os.path.exists(CONFIG):
        return {"default_browser": "chromium", "accounts": {}}
    with open(CONFIG, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("default_browser", "chromium")
    cfg.setdefault("accounts", {})
    return cfg


def save(cfg):
    os.makedirs(HOME, exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, 0o600)            # it can hold the AdsPower key
    os.replace(tmp, CONFIG)


def set_value(name, value):
    """`anyx config set adspower.key KEY`: a dotted name into config.json. Values that read as JSON
    (true, 3, [2, 5]) are stored as such, anything else as text. None removes the name."""
    cfg = load()
    *path, last = name.split(".")
    node = cfg
    for k in path:
        node = node.setdefault(k, {})
        if not isinstance(node, dict):
            raise ValueError(f"{name}: {k} is not a group")
    if value is None:
        node.pop(last, None)
    else:
        try:
            node[last] = json.loads(value)
        except ValueError:
            node[last] = value
    save(cfg)
    return cfg


def shown():
    """config.json as `anyx config` prints it, with the AdsPower key hidden."""
    cfg = load()
    ap = cfg.get("adspower") or {}
    if ap.get("key"):
        cfg["adspower"] = dict(ap, key=ap["key"][:4] + "…")
    return cfg


def account(handle):
    """The browser settings for one account. Unknown accounts use the default browser."""
    cfg = load()
    a = dict(cfg["accounts"].get(handle.lstrip("@").lower(), {}))
    a.setdefault("browser", cfg["default_browser"])
    a["_adspower"] = cfg.get("adspower", {})
    return a


def set_account(handle, **fields):
    cfg = load()
    a = cfg["accounts"].setdefault(handle.lstrip("@").lower(), {})
    a.update({k: v for k, v in fields.items() if v is not None})
    save(cfg)
    return a
