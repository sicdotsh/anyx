"""Browsers anyx can drive. Each one only has to open a Playwright BrowserContext with the given
cookies poured in, and close it again. Everything X-specific lives above this layer.

    chromium   Playwright's own Chromium with a profile under ~/.anyx/profiles/<handle>/. The default.
    chrome     The Google Chrome installed on this machine, same profile layout.
    cdp        A browser you already started with --remote-debugging-port. anyx attaches and leaves it open.
    adspower   An AdsPower profile, started through AdsPower's Local API.

Adding a browser is one class with open() and close().
"""

from ..errors import UsageError


def get(name):
    if name in ("chromium", "chrome"):
        from .chromium import Chromium
        return Chromium(channel="chrome" if name == "chrome" else None)
    if name == "cdp":
        from .cdp import CDP
        return CDP()
    if name == "adspower":
        from .adspower import AdsPower
        return AdsPower()
    raise UsageError(f"unknown browser: {name} (chromium, chrome, cdp, adspower)")


NAMES = ("chromium", "chrome", "cdp", "adspower")
