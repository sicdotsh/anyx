"""Getting accounts into the vault.

    add       log in by hand once in a browser window anyx opens; anyx keeps the cookies
    import    take cookies exported from any browser (JSON list or {name: value})
    harvest   read every account logged into a browser (an AdsPower profile, a CDP browser, ...)
              and store each one separately
    check     open the account and see whether X still accepts it
"""

import time

from . import browsers, config, vault
from .errors import AnyxError, SessionExpired, UsageError


def add(handle, timeout=600):
    """Open a visible Chromium on X's login page and wait until `handle` is logged in."""
    from playwright.sync_api import sync_playwright
    handle = handle.lstrip("@").lower()
    drv = browsers.get("chromium")
    with sync_playwright() as pw:
        ctx = drv.open(pw, handle, {"browser": "chromium"}, None, headless=False)
        try:
            p = ctx.pages[0] if ctx.pages else ctx.new_page()
            p.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")
            end = time.monotonic() + timeout
            who = None
            while time.monotonic() < end:
                from .session import shown_handle
                who = shown_handle(p)
                if who:
                    break
                p.wait_for_timeout(1500)
            if not who:
                raise AnyxError("gave up waiting for the login")
            if who != handle:
                raise AnyxError(f"logged in as @{who}, not @{handle}; nothing saved")
            cookies = [c for c in ctx.cookies("https://x.com")]
            vault.save(handle, cookies)
            vault.set_born(handle, "chromium")
            config.set_account(handle, browser="chromium")
            return {"user": handle, "result": "saved"}
        finally:
            drv.close()
            drv.stop(handle)          # the login window; the next command starts the account's own browser


def import_file(handle, path, browser=None, profile=None):
    """Cookies exported from a browser. Say which browser they came from (--browser, --profile): anyx
    will use them only there."""
    if not browser:
        raise UsageError("say which browser these cookies were made in: --browser chromium|chrome|cdp|adspower "
                         "[--profile <adspower profile>]; anyx uses them only there")
    import json
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and handle in data:
        data = data[handle]
    cookies = vault.normalize(data)
    if not any(c["name"] == "auth_token" for c in cookies):
        raise UsageError("no auth_token in that file")
    vault.save(handle, cookies)
    vault.set_born(handle, browser, profile)
    config.set_account(handle, browser=browser, profile=profile)
    return {"user": handle.lstrip("@").lower(), "result": "saved"}


# X's web app sends this public token with every API call; the account comes from the cookies
WEB_BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"

MULTI_JS = """async bearer => {
    const ct0 = (document.cookie.match(/(?:^|; )ct0=([^;]+)/) || [])[1];
    const r = await fetch('/i/api/1.1/account/multi/list.json', {credentials: 'include', headers: {
        authorization: 'Bearer ' + bearer, 'x-csrf-token': ct0 || '', 'x-twitter-auth-type': 'OAuth2Session',
        'x-twitter-active-user': 'yes'}});
    return r.ok ? (await r.json()).users || [] : [];
}"""


def harvest(browser, profile=None, cdp=None, set_browser=True):
    """Store every account logged into one browser. The handles come from X's own account-switcher list
    (one request, as the account in use): no account is switched, and the browser is left untouched."""
    from playwright.sync_api import sync_playwright
    acct = {"browser": browser, "profile": profile, "cdp": cdp, "_adspower": config.load().get("adspower", {})}
    drv = browsers.get(browser)
    with sync_playwright() as pw:
        ctx = drv.open(pw, "_harvest", acct, None, headless=True)
        p = ctx.new_page()                      # a tab of its own; the browser's tabs are left alone
        try:
            jar = drv.cookies()
            sets = vault.split(jar)
            if not sets:
                raise AnyxError("no logged-in X account in that browser")
            p.goto("https://x.com/robots.txt", wait_until="domcontentloaded")    # any x.com page, for the cookies
            users = {str(u.get("user_id")): u for u in p.evaluate(MULTI_JS, WEB_BEARER)}
        finally:
            p.close()
            drv.close()
            if getattr(drv, "started", False):
                drv.stop("_harvest")            # a browser started only for this goes away again
    out = []
    for uid, cookies in sets.items():
        u = users.get(uid) or {}
        who = (u.get("screen_name") or "").lower() or None
        if who and u.get("is_auth_valid") is not False:
            vault.save(who, cookies)
            vault.set_born(who, browser, profile)
            if set_browser:
                config.set_account(who, browser=browser, profile=profile, cdp=cdp)
        out.append({"user_id": uid, "user": who, "result": "stored" if who and u.get("is_auth_valid") is not False
                    else "logged out" if who else "unknown"})
    return out


def check(handle, force=False):
    """Is the account usable right now? Opens it once and reports what X shows:
    ok, expired (logged out), locked (X wants a check), suspended, rate_limited, wrong_account."""
    from .errors import NotFound, RateLimited, WrongAccount
    from .session import Session
    out = {"user": handle.lstrip("@").lower()}
    try:
        with Session(handle, verify="full", force=force) as x:
            out["browser"] = x.account["browser"]
            try:
                u = x.user(x.me)
                out.update(result="ok", followers=u["followers"], following=u["following"], posts=u["posts"],
                           protected=u["protected"], verified=u["verified"])
            except NotFound as e:
                out.update(result="suspended" if "Suspended" in str(e) else "unavailable", detail=str(e))
            if out.get("result") == "ok" and (u.get("posts") or 0) > 0:
                try:
                    hits = x.search(f"from:{x.me}", limit=3)
                    out["in_search"] = len(hits) > 0
                except Exception as e:  # noqa: BLE001
                    out["in_search"] = None
                    out["search_error"] = str(e)
    except SessionExpired as e:
        out.update(result="locked" if "locked" in str(e) else "expired", detail=str(e))
    except RateLimited as e:
        out.update(result="rate_limited", detail=str(e))
    except WrongAccount as e:
        out.update(result="wrong_account", detail=str(e))
    return out


def listing():
    cfg = config.load()
    rows = []
    for h in sorted(set(vault.handles()) | set(cfg["accounts"])):
        a = cfg["accounts"].get(h, {})
        rows.append({"user": h, "browser": a.get("browser", cfg["default_browser"]),
                     "profile": a.get("profile"), "cdp": a.get("cdp"), "cookies": vault.load(h) is not None})
    return rows
