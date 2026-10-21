import os

from .. import config


class Chromium:
    """Playwright's Chromium (or the installed Chrome) with a persistent profile per account."""

    def __init__(self, channel=None):
        self.channel = channel
        self.ctx = None

    def open(self, pw, handle, account, cookies, headless=True):
        d = os.path.join(config.PROFILES, handle.lower())
        os.makedirs(d, exist_ok=True)
        kw = {"headless": headless, "viewport": {"width": 1280, "height": 900}}
        if self.channel:
            kw["channel"] = self.channel
        if account.get("proxy"):
            kw["proxy"] = {"server": account["proxy"]}
        self.ctx = pw.chromium.launch_persistent_context(d, **kw)
        if cookies:
            self.ctx.clear_cookies(domain=".x.com")
            self.ctx.add_cookies(cookies)
        return self.ctx

    def close(self):
        if self.ctx:
            self.ctx.close()
            self.ctx = None

    def cookies(self):
        return self.ctx.cookies("https://x.com") if self.ctx else []
