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


class Tab:
    """The account's one tab. Everything anyx does happens in it, like a person with one tab open.
    close() is a no-op so code written for throwaway pages keeps the tab."""

    def __init__(self, page):
        object.__setattr__(self, "_page", page)
        object.__setattr__(self, "spa_url", None)

    def __getattr__(self, name):
        return getattr(self._page, name)

    def __setattr__(self, name, value):
        if name in ("spa_url",):
            object.__setattr__(self, name, value)
        else:
            setattr(self._page, name, value)

    def close(self):
        pass


class Session:
    def __init__(self, handle=None, headless=True, browser=None, verify=True, force=False):
        self.handle = handle.lstrip("@").lower() if handle else None
        self.headless = headless
        self.verify = verify
        self.force = force
        self.account = config.account(self.handle or "")
        if browser:
            self.account["browser"] = browser
        self.driver = browsers.get(self.account["browser"])
        self._pw = None
        self.ctx = None
        self.me = None
        self._tab = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        from . import run
        from .errors import RateLimited
        c = run.cooling(self.handle) if (self.handle and config.load().get("rest")) else None
        if c and not self.force:
            import time as _t
            left = int(c[0] - _t.time())
            raise RateLimited(f"@{self.handle} is resting for {left // 60}m{left % 60:02d}s after: {c[1]} "
                              f"(anyx does nothing as this account until then)", c[0])
        cookies = vault.load(self.handle) if self.handle else None
        if self.handle and cookies is None and self.account["browser"] != "cdp":
            raise UsageError(f"no cookies for @{self.handle} (anyx accounts login {self.handle} --file <creds>)")
        b = vault.born(self.handle) if self.handle else None
        if cookies and not vault.same_place(b, self.account):
            import sys as _s
            where = b["browser"] + (f" profile {b['profile']}" if b.get("profile") else "")
            print(f"anyx: note: @{self.handle}'s login was made in {where} and is being used in "
                  f"{self.account['browser']}; X may refuse some actions from it", file=_s.stderr)
        self._pw = sync_playwright().start()
        try:
            self.ctx = self.driver.open(self._pw, self.handle or "_", self.account, cookies, self.headless)
            self.ctx.set_default_timeout(45000)
            if self.verify:
                self._check(cookies)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type=None, exc=None, tb=None):
        from . import run
        from .errors import ActionFailed, RateLimited
        try:
            if not config.load().get("rest"):
                pass
            elif self.handle and isinstance(exc, RateLimited) and not str(exc).startswith(f"@{self.handle} is resting"):
                run.cool(self.handle, until=(exc.reset + 60) if exc.reset else None, reason=str(exc))
            elif self.handle and isinstance(exc, ActionFailed) and "refused" in str(exc) and "duplicate" not in str(exc):
                run.cool(self.handle, seconds=run.COOL_REFUSED, reason=str(exc))
            elif self.handle and isinstance(exc, SessionExpired):
                run.cool(self.handle, seconds=24 * 3600, reason=str(exc))
        finally:
            try:
                if self.driver:
                    self.driver.close()
            finally:
                if self._pw:
                    self._pw.stop()
                    self._pw = None

    # ---- the tab ----

    def page(self):
        """The account's tab (the same one every time)."""
        if self._tab is None:
            pages = [p for p in self.ctx.pages if p.url.startswith("https://x.com")] or self.ctx.pages
            pg = pages[0] if pages else self.ctx.new_page()
            pg.set_viewport_size({"width": 1280, "height": 1000})
            self._tab = Tab(pg)
        return self._tab

    def scratch(self):
        """A separate throwaway page, for things that are not X (rendering an image, ...)."""
        return self.ctx.new_page()

    def before_write(self):
        from . import run
        if self.handle:
            run.pace(self.handle, write=True)

    def goto(self, page, url):
        """Show `url` in the tab. When X's app is already running in it, the page changes inside the app
        (as clicking a link does) instead of reloading it, which costs a fraction of the requests."""
        from . import run
        if self.handle:
            run.pace(self.handle)
        booted = False
        try:
            booted = page.url.startswith("https://x.com/") and page.evaluate("window.__anyx === 1")
        except Exception:
            booted = False
        if booted and url.startswith("https://x.com/") and "/i/flow/" not in url:
            path = url[len("https://x.com"):]
            page.evaluate("p => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate', {state: {}})); }", path)
            page.wait_for_timeout(400)
            if isinstance(page, Tab):
                page.spa_url = url
        else:
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.evaluate("window.__anyx = 1")
            except Exception:
                pass
            if isinstance(page, Tab):
                page.spa_url = None
        if "/login" in page.url or "/i/flow/login" in page.url:
            raise SessionExpired(f"@{self.handle or '?'} is logged out (anyx accounts add {self.handle or '<handle>'})")
        if "/account/access" in page.url:
            raise SessionExpired(f"@{self.handle or '?'} is locked by X ({page.url})")
        return page

    def reload(self, page):
        """Load the current address for real (used when the app showed cached data and fetched nothing)."""
        url = getattr(page, "spa_url", None) or page.url
        page.goto(url, wait_until="domcontentloaded")
        try:
            page.evaluate("window.__anyx = 1")
        except Exception:
            pass
        if isinstance(page, Tab):
            page.spa_url = None

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
        p = self.page()
        cap = graphql.Capture(p)
        try:
            self.goto(p, "https://x.com/home")
            b = cap.wait_for(("Viewer", "ViewerAccountSync"), timeout=30)
            if b is None and any(n in ("Viewer",) for n in cap.limited):
                raise cap.limit_error("Viewer")
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
                    raise cap.limit_error(cap.limited[0])
                raise UIChanged("could not find who is logged in on the home page")
            return (link.get_attribute("href") or "").strip("/")
        finally:
            cap.detach()

    def article(self, page, tid):
        """The <article> of the post `tid` itself, not its parents, replies or quote cards."""
        # either marker will do; wait for whichever shows first
        sel = (f'article[data-testid="tweet"]:has(a[href$="/status/{tid}/analytics"]), '
               f'article[data-testid="tweet"]:has(a[href*="/status/{tid}"]:has(time))')
        a = page.locator(sel).first
        try:
            a.wait_for(state="visible", timeout=25000)
            return a
        except Exception:
            pass
        raise NotFound(f"post {tid} is not visible to @{self.me or self.handle}")

    def cookies(self):
        return self.driver.cookies()

    @staticmethod
    def clear_layers(page, tries=3):
        """Close whatever sheet or popover X has laid over the page (a mask in #layers eats clicks)."""
        for _ in range(tries):
            if page.locator('#layers [data-testid="mask"]').count() == 0:
                return
            page.keyboard.press("Escape")
            page.wait_for_timeout(700)

    # ---- reading and acting: see read.py, act.py, compose.py ----

    from .read import (tweet, thread, quotes, user, timeline, search, notifications, home, trends,  # noqa: E402
                       bookmarks, likes, followers, following, shot, analytics)
    from .act import (like, unlike, retweet, unretweet, bookmark, unbookmark, delete,  # noqa: E402
                      follow, unfollow, mute, unmute, block, unblock, pin, unpin, edit_profile)
    from .compose import post, scheduled, delete_scheduled, drafts, delete_drafts, reschedule  # noqa: E402
    from .chat import chat_setup, chats, messages, dm  # noqa: E402
    from .communities import community, community_posts, join, leave  # noqa: E402
    from .settings import settings, set_setting, muted_words, mute_word, unmute_word, set_handle  # noqa: E402
    from .lists import lists, list_create, list_add, list_remove, list_posts, list_members, list_delete  # noqa: E402
