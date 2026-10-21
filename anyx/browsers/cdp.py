from ..errors import UsageError


class CDP:
    """A browser that is already running with --remote-debugging-port. anyx attaches to it and
    leaves it running afterwards. If the vault has cookies for the account they are poured in,
    which switches that browser's x.com login to the account."""

    def __init__(self):
        self.browser = None
        self.ctx = None
        self.pages = []

    def open(self, pw, handle, account, cookies, headless=True):
        url = account.get("cdp")
        if not url:
            raise UsageError(f"@{handle} uses the cdp browser but has no cdp address "
                             f"(anyx accounts set {handle} --browser cdp --cdp http://127.0.0.1:9222)")
        self.browser = pw.chromium.connect_over_cdp(url)
        self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        if cookies:
            self.ctx.add_cookies(cookies)
        before = list(self.ctx.pages)
        self._keep = before
        return self.ctx

    def close(self):
        # Close only the tabs anyx opened, never the browser.
        if self.ctx:
            for p in self.ctx.pages:
                if p not in self._keep:
                    try:
                        p.close()
                    except Exception:
                        pass
        self.ctx = None
        self.browser = None

    def cookies(self):
        return self.ctx.cookies("https://x.com") if self.ctx else []
