"""Chromium (Playwright's, or the installed Chrome) that stays open between commands.

The first command for an account starts the browser as its own process with a debugging port and a
profile under ~/.anyx/profiles/<handle>/; later commands attach to the same browser and the same tab.
`anyx stop <handle>` closes it.
"""

import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.request

from .. import config, run
from ..errors import AnyxError


def _alive(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=2) as r:
            return json.load(r)
    except Exception:
        return None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _executable(pw, channel):
    if channel == "chrome":
        for p in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  shutil.which("google-chrome") or "", shutil.which("chrome") or ""):
            if p and os.path.exists(p):
                return p
        raise AnyxError("Google Chrome is not installed")
    return pw.chromium.executable_path


class Chromium:
    def __init__(self, channel=None):
        self.channel = channel
        self.browser = None
        self.ctx = None
        self.handle = None
        self.fresh = False
        self.started = False

    def open(self, pw, handle, account, cookies, headless=True):
        self.handle = handle
        info = (run.load(handle).get("browser") or {})
        port = info.get("port") if info.get("kind") == (self.channel or "chromium") else None
        if not (port and _alive(port)):
            port = self._start(pw, handle, account, headless)
            self.fresh = self.started = True
        self.browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        if cookies:
            from . import switch
            if switch(self.ctx, handle, account, cookies, f"the {self.channel or 'chromium'} profile of @{handle}",
                      started=self.fresh):
                self.fresh = True
        return self.ctx

    def _start(self, pw, handle, account, headless):
        d = os.path.join(config.PROFILES, handle.lower())
        os.makedirs(d, exist_ok=True)
        port = _free_port()
        args = [_executable(pw, self.channel), f"--remote-debugging-port={port}", f"--user-data-dir={d}",
                "--no-first-run", "--no-default-browser-check", "--window-size=1280,1000",
                "--disable-blink-features=AutomationControlled"]
        if headless:
            args.append("--headless=new")
        if account.get("proxy"):
            args.append(f"--proxy-server={account['proxy']}")
        args.append("about:blank")
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        for _ in range(60):
            if _alive(port):
                break
            time.sleep(0.25)
        else:
            proc.kill()
            raise AnyxError("the browser did not start")
        run.save(handle, browser={"kind": self.channel or "chromium", "pid": proc.pid, "port": port,
                                  "headless": headless})
        return port

    def close(self):
        # leave the browser running for the next command; only let go of it
        self.browser = None
        self.ctx = None

    def cookies(self):
        return self.ctx.cookies("https://x.com") if self.ctx else []

    @staticmethod
    def stop(handle):
        info = run.load(handle).get("browser") or {}
        if info.get("pid"):
            try:
                os.killpg(os.getpgid(info["pid"]), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        run.forget(handle, "browser")
        return bool(info)
