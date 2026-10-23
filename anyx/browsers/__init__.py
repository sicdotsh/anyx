"""Browsers anyx can drive. Each one only has to open a Playwright BrowserContext with the given
cookies poured in, and close it again. Everything X-specific lives above this layer.

    chromium   Playwright's own Chromium with a profile under ~/.anyx/profiles/<handle>/. The default.
    chrome     The Google Chrome installed on this machine, same profile layout.
    cdp        A browser you already started with --remote-debugging-port. anyx attaches and leaves it open.
    adspower   An AdsPower profile, started through AdsPower's Local API.

Adding a browser is one class with open() and close().

Every browser may already be logged in to accounts, and may be in use by a person. So anyx works in a
tab of its own (window.name "anyx"), and switches accounts only through switch() below.
"""

import re

from ..errors import UsageError

MARK = "anyx"
XCOM = re.compile(r"(^|\.)x\.com$")


def put(ctx, cookies):
    """Replace these x.com cookies (by name) and nothing else."""
    if not cookies:
        return
    names = "|".join(sorted({re.escape(c["name"]) for c in cookies}))
    ctx.clear_cookies(name=re.compile(f"^({names})$"), domain=XCOM)
    ctx.add_cookies(cookies)


def restore(ctx, jar):
    """Give the browser back exactly the login cookies it had in `jar` (from ctx.cookies)."""
    from ..vault import AUTH
    ctx.clear_cookies(name=re.compile("^(" + "|".join(AUTH) + ")$"), domain=XCOM)
    ctx.add_cookies([c for c in jar if c["name"] in AUTH])


def key(where):
    return "browser-" + re.sub(r"\W+", "_", where)


def check_free(ctx, handle, account, where, now):
    """Stop rather than change who the browser is while a person is using it: it shows an account anyx
    did not put there, or x.com is open in a tab that is not anyx's."""
    from .. import run, vault
    if account.get("_force") or not now:
        return
    who = "@" + (vault.holder(now) or now)
    if now != run.load(key(where)).get("user"):
        raise UsageError(f"{where} shows {who}, which anyx did not put there; anyx does not switch it to "
                         f"@{handle} under whoever is using it (--force to switch anyway)")
    if others_tabs(ctx):
        raise UsageError(f"{where} has x.com open in another tab as {who}; anyx does not switch it to "
                         f"@{handle} while someone is using it (--force to switch anyway)")


def others_tabs(ctx):
    """x.com tabs that are not anyx's own: someone is using the browser."""
    out = []
    for p in ctx.pages:
        if p.url.startswith("https://x.com"):
            try:
                name = p.evaluate("window.name")
            except Exception:
                name = None
            if name != MARK:
                out.append(p)
    return out


def switch(ctx, handle, account, cookies, where, started=False):
    """Make the browser `handle`, the way X's own account switcher does: the accounts already logged
    in stay logged in. Never under a person: when the browser shows an account anyx did not put there,
    or someone has x.com open in another tab, anyx stops (unless --force, or anyx has just started the
    browser itself). Returns True when the account changed."""
    from .. import run, vault
    k = key(where)
    jar = ctx.cookies("https://x.com")
    have = {c["name"]: c["value"] for c in jar}
    now, want = vault.user_id(vault.normalize(jar)), vault.user_id(cookies)
    merged = vault.switched(jar, cookies)
    if have.get("auth_token") == {c["name"]: c["value"] for c in cookies}.get("auth_token"):
        multi = [c for c in merged if c["name"] == "auth_multi"]
        if multi and multi[0]["value"] != have.get("auth_multi"):
            put(ctx, multi)                 # the same account, with the others put back beside it
        run.save(k, user=want)
        return False
    if now != want and not started:
        check_free(ctx, handle, account, where, now)
    put(ctx, merged)
    run.save(k, user=want)
    return True


def get(name):
    if name in ("chromium", "chrome"):
        from .chromium import Chromium
        return Chromium(channel="chrome" if name == "chrome" else None)
    if name == "cdp":
        from .cdp import CDP
        return CDP()
    if name == "adspower":
        from .adspower import AdsPower
        return AdsPower()
    raise UsageError(f"unknown browser: {name} (chromium, chrome, cdp, adspower)")


NAMES = ("chromium", "chrome", "cdp", "adspower")
