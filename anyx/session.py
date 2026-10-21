"""A Session is one browser, opened as one X account.

    with anyx.Session("alice") as x:
        x.tweet("https://x.com/jack/status/20")
        x.like("https://x.com/jack/status/20")

The browser comes from ~/.anyx/config.json (chromium unless said otherwise) and the account's cookies
from the vault. Before anything is done the session checks which account the browser actually shows;
if it is not the one asked for, it stops without touching anything.
"""

import re

from . import browsers, config, vault
from .errors import NotFound, SessionExpired, UIChanged, UsageError, WrongAccount


def tweet_id(ref):
    m = re.search(r"(\d{6,})", str(ref))
    if not m:
        raise UsageError(f"not a post URL or id: {ref}")
    return m.group(1)


def handle_of(ref):
    s = str(ref).strip()
    m = re.search(r"x\.com/([A-Za-z0-9_]{1,15})", s) or re.search(r"twitter\.com/([A-Za-z0-9_]{1,15})", s)
    return (m.group(1) if m else s).lstrip("@")


class Session:
    def __init__(self, handle=None, headless=True, browser=None, verify=True):
        self.handle = handle.lstrip("@").lower() if handle else None
        self.headless = headless
        self.verify = verify
        self.account = config.account(self.handle or "")
        if browser:
            self.account["browser"] = browser
        self.driver = browsers.get(self.account["browser"])
        self._pw = None
        self.ctx = None
        self.me = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        cookies = vault.load(self.handle) if self.handle else None
        if self.handle and cookies is None and self.account["browser"] != "cdp":
            raise UsageError(f"no cookies for @{self.handle} (anyx accounts add {self.handle})")
        self._pw = sync_playwright().start()
        try:
            self.ctx = self.driver.open(self._pw, self.handle or "_", self.account, cookies, self.headless)
            self.ctx.set_default_timeout(45000)
            if self.verify:
                self._check(cookies)
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc):
        try:
            if self.driver:
                self.driver.close()
        finally:
            if self._pw:
                self._pw.stop()
                self._pw = None

    # ---- pages ----

    def page(self):
        p = self.ctx.new_page()
        p.set_viewport_size({"width": 1280, "height": 1000})
        return p

    def goto(self, page, url):
        page.goto(url, wait_until="domcontentloaded")
        if "/login" in page.url or "/i/flow/login" in page.url:
            raise SessionExpired(f"@{self.handle or '?'} is logged out (anyx accounts add {self.handle or '<handle>'})")
        if "/account/access" in page.url:
            raise SessionExpired(f"@{self.handle or '?'} is locked by X ({page.url})")
        return page

    def _check(self, cookies):
        """Make sure the browser is the account asked for, before anything is done.
        With cookies from the vault this is free: the browser's twid cookie must carry the same user id.
        Without them (an attached browser) the home page is loaded and asked."""
        want = vault.user_id(cookies) if cookies else None
        if want and self.verify != "full":
            got = vault.user_id(vault.normalize(self.driver.cookies()))
            if got != want:
                raise WrongAccount(f"the browser holds user {got}, not @{self.handle} ({want}). Nothing was done.")
            self.me = self.handle
            return
        self.me = self.whoami()
        if self.handle and self.me.lower() != self.handle:
            raise WrongAccount(f"the browser is @{self.me}, not @{self.handle}. Nothing was done.")

    def whoami(self):
        """The handle the browser is logged in as, asked from X itself (its Viewer query)."""
        from . import graphql
        from .errors import RateLimited
        p = self.ctx.pages[0] if self.ctx.pages else self.page()
        cap = graphql.Capture(p)
        try:
            self.goto(p, "https://x.com/home")
            b = cap.wait_for(("Viewer", "ViewerAccountSync"), timeout=30)
            if b is None and any(n in ("Viewer",) for n in cap.limited):
                raise RateLimited("X answered 429 for Viewer: this account is rate limited, wait ~15 minutes")
            _, us = graphql.walk([b] if b else [])
            if us and us[0].get("handle"):
                return us[0]["handle"]
            link = p.locator('[data-testid="AppTabBar_Profile_Link"]')
            try:
                link.wait_for(state="attached", timeout=20000)
            except Exception:
                if "/login" in p.url or "/i/flow/login" in p.url:
                    raise SessionExpired("logged out")
                if cap.limited:
                    raise RateLimited(f"X answered 429 ({cap.limited[0]}): rate limited, wait ~15 minutes")
                raise UIChanged("could not find who is logged in on the home page")
            return (link.get_attribute("href") or "").strip("/")
        finally:
            cap.detach()

    def article(self, page, tid):
        """The <article> of the post `tid` itself, not its parents, replies or quote cards."""
        for sel in (f'article[data-testid="tweet"]:has(a[href$="/status/{tid}/analytics"])',
                    f'article[data-testid="tweet"]:has(a[href*="/status/{tid}"]:has(time))'):
            a = page.locator(sel).first
            try:
                a.wait_for(state="visible", timeout=20000)
                return a
            except Exception:
                continue
        raise NotFound(f"post {tid} is not visible to @{self.me or self.handle}")

    def cookies(self):
        return self.driver.cookies()

    # ---- reading and acting: see read.py, act.py, compose.py ----

    from .read import (tweet, thread, quotes, user, timeline, search, notifications, home,  # noqa: E402
                       bookmarks, likes, followers, following, shot)
    from .act import (like, unlike, retweet, unretweet, bookmark, unbookmark, delete,  # noqa: E402
                      follow, unfollow, mute, unmute, block, unblock, pin, unpin, edit_profile)
    from .compose import post, scheduled, delete_scheduled  # noqa: E402
