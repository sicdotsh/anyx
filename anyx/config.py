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
      "adspower": {"api": "http://local.adspower.net:50325", "key_env": "ADSPOWER_API_KEY"},
      "pace": {"page": [2, 5], "write": [6, 14]}
    }

pace is the pause, in seconds (a random value between the two), before each page change and before each
write. Lower it at your accounts' risk.
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
    os.replace(tmp, CONFIG)


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
