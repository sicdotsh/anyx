"""anyx: use X from any browser, as any of your accounts. No API keys.

    import anyx
    with anyx.Session("alice") as x:
        post = x.tweet("https://x.com/jack/status/20")
        x.like(post["id"])
        x.post("hello", quote=post["url"])
"""

from .errors import (ActionFailed, AnyxError, NotFound, RateLimited, SessionExpired, UIChanged,  # noqa: F401
                     UsageError, WrongAccount)
from .session import Session  # noqa: F401

__version__ = "0.1.1"
