"""The cookie vault. One file per account, holding only that account's x.com cookies.

X keeps every logged-in account of a browser in one cookie jar: `auth_token` is the account in use,
`auth_multi` lists the others as `<user id>:<token>|...`. anyx stores each account on its own, with
its own `auth_token` and `twid`, so any browser can be turned into any account by pouring that set in.
Switching never goes through X's account switcher.
"""

import json
import os
import urllib.parse

from . import config

# Cookies that belong to the browser session rather than to an account. Every set carries them.
SHARED = ("ct0", "guest_id", "guest_id_ads", "guest_id_marketing", "personalization_id", "kdt",
          "lang", "dnt", "des_opt_in", "night_mode", "twtr_pixel_opt_in", "external_referer", "att",
          "__cf_bm", "gt")
DOMAIN = ".x.com"


def path(handle):
    return os.path.join(config.ACCOUNTS, handle.lstrip("@").lower() + ".json")


def load(handle):
    p = path(handle)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(handle, cookies):
    os.makedirs(config.ACCOUNTS, exist_ok=True)
    p = path(handle)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(normalize(cookies), f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)
    return p


def handles():
    if not os.path.isdir(config.ACCOUNTS):
        return []
    return sorted(f[:-5] for f in os.listdir(config.ACCOUNTS) if f.endswith(".json") and not f.endswith(".meta.json"))


def normalize(cookies):
    """Accept a list of cookie dicts (Playwright, CDP or browser-extension exports) or a plain
    {name: value} dict, and return Playwright cookies on .x.com."""
    if isinstance(cookies, dict):
        cookies = [{"name": k, "value": v} for k, v in cookies.items()]
    out = {}
    for c in cookies:
        dom = c.get("domain") or DOMAIN
        if not (dom.endswith("x.com") or dom.endswith("twitter.com")):
            continue
        same = str(c.get("sameSite") or "Lax").capitalize()
        out[c["name"]] = {
            "name": c["name"], "value": str(c["value"]), "domain": DOMAIN, "path": c.get("path") or "/",
            "expires": float(c.get("expires") or c.get("expirationDate") or -1),
            "httpOnly": bool(c.get("httpOnly", c["name"] in ("auth_token", "kdt"))),
            "secure": bool(c.get("secure", True)),
            "sameSite": same if same in ("Strict", "Lax", "None") else "Lax",
        }
    return list(out.values())


def user_id(cookies):
    for c in cookies:
        if c["name"] == "twid":
            v = urllib.parse.unquote(c["value"].strip('"'))
            return v[2:] if v.startswith("u=") else v
    return None


def split(cookies):
    """Split a browser's cookie jar into one set per logged-in account: {user id: cookies}."""
    cookies = normalize(cookies)
    by = {c["name"]: c for c in cookies}
    shared = [c for c in cookies if c["name"] in SHARED]
    tokens = {}
    uid = user_id(cookies)
    if uid and "auth_token" in by:
        tokens[uid] = by["auth_token"]["value"]
    multi = urllib.parse.unquote(by.get("auth_multi", {}).get("value", "").strip('"'))
    for pair in multi.split("|"):
        if ":" in pair:
            u, _, t = pair.partition(":")
            tokens.setdefault(u.strip(), t.strip())
    base = by.get("auth_token") or {"domain": DOMAIN, "path": "/", "expires": -1,
                                     "httpOnly": True, "secure": True, "sameSite": "None"}
    sets = {}
    for u, tok in tokens.items():
        sets[u] = normalize([dict(base, name="auth_token", value=tok, httpOnly=True),
                             dict(base, name="twid", value="u%3D" + u, httpOnly=False)] + shared)
    return sets


# ---- where a cookie set was born ----
# A login belongs to the browser it happened in. Using its cookies in another browser looks to X like one
# session jumping between devices, which X answers by refusing things (seen: unblock -> 34). So each set
# records its birthplace, and a Session opened anywhere else prints a note.

def _meta_path(handle):
    return path(handle)[:-5] + ".meta.json"


def born(handle):
    try:
        with open(_meta_path(handle), encoding="utf-8") as f:
            return json.load(f).get("born")
    except (OSError, ValueError):
        return None


def set_born(handle, browser, profile=None):
    os.makedirs(config.ACCOUNTS, exist_ok=True)
    with open(_meta_path(handle), "w", encoding="utf-8") as f:
        json.dump({"born": {"browser": browser, "profile": profile}}, f)


def same_place(b, account):
    """Is the browser `account` would open the one the cookies were born in?"""
    if not b:
        return True                 # sets saved before birthplaces were recorded
    if b.get("browser") != account.get("browser"):
        return False
    return b.get("browser") != "adspower" or b.get("profile") == account.get("profile")
