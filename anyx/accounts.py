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
                link = p.locator('[data-testid="AppTabBar_Profile_Link"]')
                if link.count():
                    who = (link.get_attribute("href") or "").strip("/").lower()
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


def harvest(browser, profile=None, cdp=None, set_browser=True):
    """Store every account logged into one browser. With AdsPower each account is opened once to learn
    its handle, then the browser is left as the first account it was."""
    from playwright.sync_api import sync_playwright
    acct = {"browser": browser, "profile": profile, "cdp": cdp, "_adspower": config.load().get("adspower", {})}
    drv = browsers.get(browser)
    out = {}
    with sync_playwright() as pw:
        ctx = drv.open(pw, "_harvest", acct, None, headless=True)
        jar = drv.cookies()
        drv.close()
        sets = vault.split(jar)
        if not sets:
            raise AnyxError("no logged-in X account in that browser")
        active = vault.user_id(vault.normalize(jar))
        for uid, cookies in sets.items():
            drv = browsers.get(browser)
            ctx = drv.open(pw, "_harvest", acct, cookies, headless=True)
            try:
                p = ctx.pages[0] if ctx.pages else ctx.new_page()
                p.set_viewport_size({"width": 1280, "height": 1000})   # narrower windows fold the side bar away
                p.goto("https://x.com/home", wait_until="domcontentloaded")
                link = p.locator('[data-testid="AppTabBar_Profile_Link"]')
                try:
                    link.wait_for(state="attached", timeout=40000)
                    who = (link.get_attribute("href") or "").strip("/").lower()
                except Exception:
                    who = None
            finally:
                drv.close()
            if not who:
                out[uid] = None
                continue
            vault.save(who, cookies)
            vault.set_born(who, browser, profile)
            if set_browser:
                config.set_account(who, browser=browser, profile=profile, cdp=cdp)
            out[uid] = who
        if browser == "adspower" and active in sets:
            # leave the profile logged in as the account it showed before
            drv = browsers.get(browser)
            drv._setup(acct)
            import json as _j
            drv.call("/api/v1/user/update", method="POST", user_id=drv.profile_id(profile),
                     cookie=_j.dumps(sets[active]))
    return [{"user_id": u, "user": h} for u, h in out.items()]


def check(handle):
    """Is the account usable right now? Opens it once and reports what X shows:
    ok, expired (logged out), locked (X wants a check), suspended, rate_limited, wrong_account."""
    from .errors import NotFound, RateLimited, WrongAccount
    from .session import Session
    out = {"user": handle.lstrip("@").lower()}
    try:
        with Session(handle, verify="full") as x:
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
