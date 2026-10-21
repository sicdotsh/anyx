"""Logging in with a password (and a TOTP secret, when the account has two-factor on).

Credentials never go on the command line. They come from a file with one account per line:

    <handle> TAB <password> [TAB <base32 TOTP secret>]

Each login starts from a browser with no x.com cookies, so X's limit of 5 accounts per session does
not apply. On success the account's cookies go into the vault like `accounts add` does.
"""

import base64
import hashlib
import hmac
import os
import struct
import time

from . import browsers, config, vault
from .errors import AnyxError, UsageError

CODE = ('input[data-testid="ocfEnterTextTextInput"], input[name="text"], input[autocomplete="one-time-code"], '
        'input[inputmode="numeric"], input[name="challenge_response"]')
WRONG = ("パスワードが間違っています", "パスワードが正しくありません", "The password you entered was incorrect",
         "Wrong password", "情報が正しくありません", "we could not find your account", "アカウントが見つかりません",
         "Could not log you in now")


def totp(secret, period=30, digits=6):
    raw = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(raw + "=" * (-len(raw) % 8))
    h = hmac.new(key, struct.pack(">Q", int(time.time()) // period), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


def read_file(path):
    out = []
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = [c.strip() for c in line.split("\t") if c.strip()]
            if len(parts) < 2:
                raise UsageError("each line is <handle> TAB <password> [TAB <totp secret>]")
            out.append((parts[0].lstrip("@"), parts[1], parts[2] if len(parts) > 2 else None))
    return out


def _submit(p, sel):
    f = p.locator(sel).filter(visible=True)
    if f.count():
        try:
            f.first.press("Enter")
        except Exception:
            pass
    p.wait_for_timeout(1500)
    if p.locator(sel).filter(visible=True).count():
        for label in ("続ける", "Continue", "ログイン", "Log in", "次へ", "Next"):
            b = p.get_by_text(label, exact=True).filter(visible=True)
            if b.count():
                try:
                    b.first.click(timeout=6000, no_wait_after=True)
                except Exception:
                    pass
                break


def _fill(p, sel, value):
    p.locator(sel).filter(visible=True).first.fill(value)


def _home(p):
    return p.url.rstrip("/").endswith("/home") or p.locator('[data-testid="AppTabBar_Profile_Link"]').count() > 0


def _wrong(p):
    try:
        body = p.locator("body").inner_text(timeout=2000)
    except Exception:
        return None
    return next((m for m in WRONG if m in body), None)


def login(handle, password, secret=None, browser="chromium", headless=True, wait=120):
    """Log `handle` in and store its cookies. Returns {"user", "result"}."""
    from playwright.sync_api import sync_playwright
    handle = handle.lstrip("@")
    acct = config.account(handle)
    acct["browser"] = browser or acct["browser"]
    drv = browsers.get(acct["browser"])
    with sync_playwright() as pw:
        ctx = drv.open(pw, handle.lower(), acct, None, headless=headless)
        try:
            for d in ("x.com", ".x.com", "twitter.com", ".twitter.com"):
                ctx.clear_cookies(domain=d)
            p = ctx.pages[0] if ctx.pages else ctx.new_page()
            p.set_viewport_size({"width": 1280, "height": 900})
            p.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")
            p.wait_for_timeout(2000)
            one = p.locator('input[name="username_or_email"]').filter(visible=True)
            try:
                one.first.wait_for(state="visible", timeout=8000)
                _fill(p, 'input[name="username_or_email"]', handle)
                _submit(p, 'input[name="username_or_email"]')
                p.wait_for_selector('input[name="username"]', timeout=45000)
            except Exception:
                p.wait_for_selector('input[autocomplete="username"]', timeout=45000)
                p.fill('input[autocomplete="username"]', handle)
                _submit(p, 'input[autocomplete="username"]')
                p.wait_for_timeout(2500)
                ocf = p.locator('input[data-testid="ocfEnterTextTextInput"]')
                if ocf.count() and not p.locator('input[name="password"]').count():
                    ocf.first.fill(handle)
                    _submit(p, 'input[data-testid="ocfEnterTextTextInput"]')
                    p.wait_for_timeout(2500)
            p.wait_for_selector('input[name="password"]', timeout=45000)
            p.wait_for_timeout(800)
            _fill(p, 'input[name="password"]', password)
            _submit(p, 'input[name="password"]')
            tries = 0
            end = time.monotonic() + wait
            while time.monotonic() < end and not _home(p):
                bad = _wrong(p)
                if bad:
                    raise AnyxError(f"@{handle}: X says the login is wrong ({bad})")
                if "verify_code" in p.url or "login_challenge" in p.url or "/account/access" in p.url:
                    raise AnyxError(f"@{handle}: X wants a verification step anyx cannot answer ({p.url})")
                field = p.locator(CODE).filter(visible=True)
                if field.count() and secret and tries < 4:
                    field.first.fill(totp(secret))
                    _submit(p, CODE)
                    tries += 1
                    p.wait_for_timeout(4000)
                    continue
                if field.count() and not secret:
                    raise AnyxError(f"@{handle}: X asks for a code and no TOTP secret was given")
                p.wait_for_timeout(2500)
            if not _home(p):
                raise AnyxError(f"@{handle}: did not reach home ({p.url})")
            link = p.locator('[data-testid="AppTabBar_Profile_Link"]')
            who = (link.first.get_attribute("href") or "").strip("/").lower() if link.count() else handle.lower()
            vault.save(who, ctx.cookies("https://x.com"))
            vault.set_born(who, acct["browser"], acct.get("profile"))
            config.set_account(who, browser=acct["browser"], profile=acct.get("profile"))
            return {"user": who, "result": "logged in"}
        finally:
            drv.close()
            if acct["browser"] in ("chromium", "chrome"):
                drv.stop(handle.lower())
