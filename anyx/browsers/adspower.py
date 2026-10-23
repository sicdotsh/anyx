"""AdsPower, through its Local API.

The API key comes from `adspower.key` in config (`anyx config set adspower.key KEY`), or else from
the environment variable named by `adspower.key_env` (default ADSPOWER_API_KEY). Calls are serialized per machine with a lock file and
spaced at least 1.1 s apart, which is what AdsPower tolerates.
"""

import fcntl
import json
import os
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager

from ..errors import AnyxError, UsageError

LOCK = "/tmp/adspower-api.lock"     # shared with other tools that call AdsPower, so all of them keep the gap
GAP = 1.1
DEFAULT_API = "http://local.adspower.net:50325"


@contextmanager
def _gate():
    # O_NOFOLLOW: a link planted at that /tmp path is not followed
    fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o666)
    with os.fdopen(fd, "a") as f:
        deadline = time.monotonic() + 150
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise AnyxError("AdsPower API is busy (another process holds the lock)")
                time.sleep(0.2)
        try:
            age = time.time() - os.path.getmtime(LOCK)
            if age < GAP:
                time.sleep(GAP - age)
            yield
        finally:
            os.utime(LOCK, None)
            fcntl.flock(f, fcntl.LOCK_UN)


class AdsPower:
    def __init__(self):
        self.uid = None
        self.ctx = None
        self.fresh = False
        self.started = False
        self.api = DEFAULT_API
        self.key = None

    def call(self, path, method="GET", **q):
        url = self.api + path
        data = None
        headers = {"Authorization": "Bearer " + (self.key or "")}
        if method == "POST":
            data = json.dumps(q).encode()
            headers["Content-Type"] = "application/json"
        elif q:
            url += "?" + urllib.parse.urlencode(q)
        with _gate():
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers), timeout=120) as r:
                return json.load(r)

    def _setup(self, account):
        ap = account.get("_adspower") or {}
        self.api = ap.get("api") or os.environ.get("ADSPOWER_API") or DEFAULT_API
        self.key = ap.get("key") or os.environ.get(ap.get("key_env") or "ADSPOWER_API_KEY")
        if not self.key:
            raise UsageError("AdsPower API key missing: anyx config set adspower.key <key> (or set ADSPOWER_API_KEY)")

    def profile_id(self, name):
        page = 1
        while True:
            users = self.call("/api/v1/user/list", page=page, page_size=100)["data"]["list"]
            for u in users:
                if u["name"] == name or u["user_id"] == name:
                    return u["user_id"]
            if len(users) < 100:
                break
            page += 1
        raise UsageError(f"no AdsPower profile named {name}")

    def open(self, pw, handle, account, cookies, headless=True):
        """Attach to the profile if it is already open (left open by an earlier command, or by hand);
        start it otherwise, with its window (adspower.headless in config starts it without). It is left
        open afterwards.

        The profile is shared with whoever else opens it: accounts are switched through browsers.switch()."""
        from .. import run
        self._setup(account)
        if not account.get("profile"):
            raise UsageError(f"@{handle} uses adspower but has no profile "
                             f"(anyx accounts set {handle} --browser adspower --profile <name>)")
        last = run.load(handle).get("browser") or {}
        self.uid = last.get("user_id") if last.get("profile") == account["profile"] else None
        self.uid = self.uid or self.profile_id(account["profile"])
        st = self.call("/api/v1/browser/active", user_id=self.uid)
        ws = ((st.get("data") or {}).get("ws") or {}).get("puppeteer") if (st.get("data") or {}).get("status") == "Active" else None
        if not ws:
            # x.com's cookies are put in after the start, through the browser: the profile's own cookie
            # list (other sites' logins among it) is left alone
            hidden = bool((account.get("_adspower") or {}).get("headless"))
            r = self.call("/api/v1/browser/start", user_id=self.uid, headless="1" if hidden else "0", open_tabs=1)
            if r.get("code") != 0:
                raise AnyxError(f"AdsPower could not start {account['profile']}: {r.get('msg')}")
            ws = r["data"]["ws"]["puppeteer"]
            self.fresh = self.started = True
        browser = pw.chromium.connect_over_cdp(ws)
        self.ctx = browser.contexts[0]
        if cookies:
            from . import switch
            if switch(self.ctx, handle, account, cookies, f"AdsPower profile {account['profile']}", started=self.fresh):
                self.fresh = True
        from . import key
        k = key(f"AdsPower profile {account['profile']}")
        if self.started:
            run.save(k, started=True, ws=ws)
        elif run.load(k).get("ws") != ws:
            run.save(k, started=False, ws=ws)       # opened by someone else since: theirs to close
        run.save(handle, browser={"kind": "adspower", "profile": account["profile"], "user_id": self.uid})
        return self.ctx

    def close(self):
        # the profile stays open for the next command; `anyx stop` closes it
        self.ctx = None

    def cookies(self):
        return self.ctx.cookies("https://x.com") if self.ctx else []

    @staticmethod
    def stop(handle):
        """Close the profile if anyx started it. One a person opened is theirs: it is left open."""
        from .. import config, run
        from . import key
        info = run.load(handle).get("browser") or {}
        run.forget(handle, "browser")
        if not info.get("user_id"):
            return None
        k = key(f"AdsPower profile {info.get('profile')}")
        st = run.load(k)
        if not st.get("ws"):
            return None                     # already closed
        if not st.get("started"):
            return "left open (not started by anyx)"
        a = AdsPower()
        a._setup(config.account(handle))
        a.call("/api/v1/browser/stop", user_id=info["user_id"])
        run.save(k, started=False, ws=None)
        return "stopped"
