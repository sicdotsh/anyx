"""A Session is one browser, opened as one X account.

    with anyx.Session("alice") as x:
        x.tweet("https://x.com/jack/status/20")
        x.like("https://x.com/jack/status/20")

The browser comes from ~/.anyx/config.json (chromium unless said otherwise) and the account's cookies
from the vault. Before anything is done the session checks which account the browser actually shows;
if it is not the one asked for, it stops without touching anything.
"""

import re
import time

from . import browsers, config, vault
from .errors import NotFound, SessionExpired, UIChanged, UsageError, WrongAccount


SHOWN_JS = """() => {
    const b = document.querySelector('[data-testid="SideNav_AccountSwitcher_Button"]');
    const at = b && [...b.querySelectorAll('span')].map(s => s.textContent.trim()).find(t => /^@\\w+$/.test(t));
    if (at) return at.slice(1);
    const l = document.querySelector('[data-testid="AppTabBar_Profile_Link"]');   // the older side bar
    return l ? (l.getAttribute('href') || '').replace(/\\//g, '') : null;
}"""


def shown_handle(page):
    """The handle X's side bar shows as logged in, or None while it is not drawn."""
    try:
        return (page.evaluate(SHOWN_JS) or "").lower() or None
    except Exception:
        return None


BOOTED_JS = """() => {
    window.__anyx = 1;
    window.name = 'anyx';
    // X's floating chat button sits over the bottom right of every page and takes clicks meant for
    // what is under it (the Save of the muted-word form): hide it in anyx's tab
    if (!document.getElementById('anyx-style')) {
        const s = document.createElement('style');
        s.id = 'anyx-style';
        s.textContent = '[data-testid^="chat-drawer"] { display: none !important; }';
        document.head.appendChild(s);
    }
}"""


def booted(page):
    """Mark a page that has just loaded X's app for real as anyx's own."""
    try:
        page.evaluate(BOOTED_JS)
    except Exception:
        pass


def _dismiss(dialog):
    try:
        if dialog.type == "beforeunload":
            dialog.accept()                 # "leave this page?": yes, anyx is moving on
        else:
            dialog.dismiss()
    except Exception:
        pass


def tweet_id(ref):
    """A post URL (…/status/<id>, …/statuses/<id>) or a bare id."""
    s = str(ref).strip()
    m = re.search(r"/status(?:es)?/(\d+)", s) or re.fullmatch(r"(\d+)", s)
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
        if cookies:
            cookies = vault.with_others(self.handle, cookies, config.sharing(self.handle))
        self.account["_force"] = self.force
        if self.handle and cookies is None and self.account["browser"] != "cdp":
            how = (f"anyx accounts harvest --browser adspower --profile {self.account.get('profile') or '<profile>'}"
                   if self.account["browser"] == "adspower" else f"anyx accounts login {self.handle} --file <creds>")
            raise UsageError(f"no cookies for @{self.handle} yet ({how})")
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
            # answer page dialogs (leave-page prompts) ourselves: left to Playwright, a dialog the
            # browser has already closed brings its driver down
            self.ctx.on("dialog", _dismiss)
            if getattr(self.driver, "fresh", False):
                self._forget_previous_account()
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

    def _forget_previous_account(self):
        """The browser was switched to this account. X's app in an open tab still runs as the previous
        one: make the next page change load it anew. x.com's storage is left alone: X keeps it per
        account (user:<id>:..., xchat_user<id>_...), as its own account switcher does."""
        for p in self.ctx.pages:
            try:
                if p.url.startswith("https://x.com"):
                    p.evaluate("window.__anyx = 0")
            except Exception:
                pass

    def page(self):
        """anyx's own tab (window.name "anyx", the same one every time). Tabs a person has open are left
        alone; a browser anyx has just started lends its first tab."""
        from .browsers import MARK
        if self._tab is None:
            pg = None
            for p in self.ctx.pages:
                try:
                    if p.evaluate("window.name") == MARK:
                        pg = p
                        break
                except Exception:
                    pass
            if pg is None:
                first = self.ctx.pages[0] if getattr(self.driver, "started", False) and self.ctx.pages else None
                # a browser anyx has just started lends its first tab while it is still blank (AdsPower
                # loads its own start page into it a moment later)
                pg = first if first is not None and first.url in ("about:blank", "") else self.ctx.new_page()
                pg.evaluate(f"window.name = '{MARK}'")
            self._fit(pg)
            self._tab = Tab(pg)
        return self._tab

    def _fit(self, pg):
        """Size the tab. A browser anyx launches itself opens at 1280x1000, so the tab is set to that. A
        browser with a person's window (AdsPower, cdp) is drawn at whatever size that window has: a
        viewport set larger than the window puts the bottom of the page (the composer's Post button)
        outside it, where a click lands nowhere and anyx waits forever. So there the tab follows the
        window, and only the width is ensured (under ~1280 px X folds its side bar away)."""
        if self.account["browser"] not in ("adspower", "cdp"):
            pg.set_viewport_size({"width": 1280, "height": 1000})
            return
        try:
            cdp = self.ctx.new_cdp_session(pg)
            try:
                # an override left by an earlier connection outlives it, and clearing only clears this
                # session's own: set one here first, then clearing drops both
                cdp.send("Emulation.setDeviceMetricsOverride", {"width": 0, "height": 0, "deviceScaleFactor": 0, "mobile": False})
                cdp.send("Emulation.clearDeviceMetricsOverride")
                w = cdp.send("Browser.getWindowForTarget")
                b = w.get("bounds") or {}
                if b.get("windowState") == "minimized":
                    cdp.send("Browser.setWindowBounds", {"windowId": w["windowId"], "bounds": {"windowState": "normal"}})
                    b = cdp.send("Browser.getWindowBounds", {"windowId": w["windowId"]}).get("bounds") or {}
                if b.get("windowState", "normal") == "normal" and (b.get("width") or 0) < 1300:
                    cdp.send("Browser.setWindowBounds", {"windowId": w["windowId"], "bounds": {"width": 1300}})
            finally:
                cdp.detach()
        except Exception:
            pass

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
        running = False
        try:
            running = page.url.startswith("https://x.com/") and page.evaluate("window.__anyx === 1")
            # a sheet or modal left open by an earlier step would sit over the next page: load for real
            if running and page.locator('#layers [data-testid="mask"]').count():
                running = False
        except Exception:
            running = False
        if running and url.startswith("https://x.com/") and "/i/flow/" not in url:
            path = url[len("https://x.com"):]
            page.evaluate("p => { history.pushState({}, '', p); dispatchEvent(new PopStateEvent('popstate', {state: {}})); }", path)
            page.wait_for_timeout(400)
            booted(page)
            if isinstance(page, Tab):
                page.spa_url = url
        else:
            page.goto(url, wait_until="domcontentloaded")
            booted(page)
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
        booted(page)
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
        p = self.page()
        cap = graphql.Capture(p)
        try:
            self.goto(p, "https://x.com/home")
            # X answers with its Viewer query on a fresh load, and only draws the side bar's account
            # button when it serves the app from cache: take whichever comes first
            end = time.monotonic() + 30
            while time.monotonic() < end:
                _, us = graphql.walk(cap.of("Viewer", "ViewerAccountSync"))
                if us and us[0].get("handle"):
                    return us[0]["handle"]
                shown = shown_handle(p)
                if shown:
                    return shown
                if "/login" in p.url or "/i/flow/login" in p.url:
                    raise SessionExpired("logged out")
                if cap.limited:
                    raise cap.limit_error(cap.limited[0])
                p.wait_for_timeout(250)
            raise UIChanged("could not find who is logged in on the home page")
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
