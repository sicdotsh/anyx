"""Reading X without an API key.

When x.com renders a page it fetches its content as JSON from /i/api/graphql/<id>/<Operation>.
anyx listens to those responses while the page loads and scrolls, and turns the tweets and users
in them into plain dicts. The page's look can change freely; the JSON is what X's own app reads,
so it changes far less often.
"""

import time
from datetime import datetime, timezone

from .errors import RateLimited


class Capture:
    """Collects GraphQL (and REST v1.1) responses from a page while it is attached."""

    def __init__(self, page):
        self.page = page
        self.items = []          # (operation, json)
        self.rest = []           # (path, json)
        self.limited = []        # operations (or REST paths) X answered 429 for
        self.reset = None        # epoch second the limit lifts, from x-rate-limit-reset
        page.on("response", self._on)

    @staticmethod
    def _ours(url):
        # x.com serves its JSON from x.com/i/api/... and, for some REST calls, from api.x.com/1.1/...
        return "/i/api/" in url or url.startswith("https://api.x.com/")

    def _on(self, r):
        url = r.url
        if not self._ours(url):
            return
        if r.status == 429:
            self.limited.append(self._name(url))
            try:
                self.reset = max(self.reset or 0, int(r.headers.get("x-rate-limit-reset") or 0)) or None
            except (TypeError, ValueError):
                pass
            return
        try:
            body = r.json()
        except Exception:
            return
        if "/graphql/" in url:
            self.items.append((self._name(url), body))
        else:
            self.rest.append((self._name(url), body))

    @staticmethod
    def _name(url):
        u = url.split("?", 1)[0]
        if "/graphql/" in u:
            return u.split("/graphql/", 1)[1].split("/", 2)[1]
        return u.split("/i/api/", 1)[1] if "/i/api/" in u else u.split("api.x.com/", 1)[1]

    def ops(self):
        return [op for op, _ in self.items]

    def of(self, *names):
        return [b for op, b in self.items if not names or op in names]

    def wait_for(self, names, timeout=30.0, after=0):
        """Wait until a response whose operation is in `names` arrives (counting from index `after`).
        If the page was changed inside X's app and X answered from its cache (nothing fetched), the page
        is loaded for real once, a third of the way into the wait."""
        start = time.monotonic()
        end = start + timeout
        reloaded = False
        while time.monotonic() < end:
            # X answers revisits from its cache without fetching; don't wait long before loading for real
            if not reloaded and getattr(self.page, "spa_url", None) and time.monotonic() - start > 4:
                reloaded = True
                url = self.page.spa_url
                self.page.goto(url, wait_until="domcontentloaded")
                try:
                    self.page.evaluate("window.__anyx = 1")
                except Exception:
                    pass
                self.page.spa_url = None
            for op, b in self.items[after:]:
                if op in names:
                    return b
            hit = [n for n in self.limited if n in names]
            if hit:
                raise self.limit_error(hit[0])
            self.page.wait_for_timeout(200)
        return None

    @staticmethod
    def refusal(body):
        """X's own error message when it answered a request with errors and no data, else None."""
        if not isinstance(body, dict) or not body.get("errors"):
            return None
        data = body.get("data")
        if data and any(v for v in (data.values() if isinstance(data, dict) else [data])):
            return None
        e = body["errors"][0]
        return f"{e.get('message')} ({e.get('code')})" if e.get("code") else e.get("message")

    def confirm(self, names, what, after=0, timeout=20):
        """Wait for X's own request among `names`, and fail unless it came back without errors."""
        from .errors import ActionFailed
        b = self.wait_for(tuple(names), timeout=timeout, after=after)
        if b is None:
            raise ActionFailed(f"X did not confirm {what}")
        if self.refusal(b):
            raise ActionFailed(f"X refused {what}: {self.refusal(b)}")
        return b

    def limit_error(self, what):
        if self.reset:
            left = max(0, int(self.reset - time.time()))
            return RateLimited(f"X answered 429 for {what}; the limit lifts in {left // 60}m{left % 60:02d}s", self.reset)
        return RateLimited(f"X answered 429 for {what} (rate limited; usually ~15 minutes)")

    def wait_rest(self, path_part, timeout=30.0, after=0):
        start = time.monotonic()
        end = start + timeout
        reloaded = False
        while time.monotonic() < end:
            if not reloaded and getattr(self.page, "spa_url", None) and time.monotonic() - start > 4:
                reloaded = True
                url = self.page.spa_url
                self.page.goto(url, wait_until="domcontentloaded")
                try:
                    self.page.evaluate("window.__anyx = 1")
                except Exception:
                    pass
                self.page.spa_url = None
            for p, b in self.rest[after:]:
                if path_part in p:
                    return b
            self.page.wait_for_timeout(200)
        return None

    def detach(self):
        try:
            self.page.remove_listener("response", self._on)
        except Exception:
            pass


# ---- parsing ----

def _when(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").astimezone(timezone.utc).isoformat()
    except ValueError:
        return s


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def unwrap(r):
    """Tweet results come wrapped in TweetWithVisibilityResults and similar shells."""
    while isinstance(r, dict) and r.get("__typename") != "Tweet" and isinstance(r.get("tweet"), dict):
        r = r["tweet"]
    return r


def user(u):
    """A GraphQL User result as a plain dict. X serves two shapes: the older one with everything under
    `legacy`, and a newer one split into core, relationship_counts, tweet_counts, profile_bio, ...
    Both are read."""
    if not isinstance(u, dict) or u.get("__typename") not in ("User", None) or "rest_id" not in u:
        return None
    L = u.get("legacy") or {}
    core = u.get("core") or {}
    rc = u.get("relationship_counts") or {}
    tc = u.get("tweet_counts") or {}
    rp = u.get("relationship_perspectives") or {}
    pick = lambda *vals: next((v for v in vals if v not in (None, "")), None)
    avatar = pick((u.get("avatar") or {}).get("image_url"), L.get("profile_image_url_https"))
    loc = u.get("location")
    loc = loc.get("location") if isinstance(loc, dict) else loc
    url = pick((u.get("website") or {}).get("url"),
               (((L.get("entities") or {}).get("url") or {}).get("urls") or [{}])[0].get("expanded_url"))
    pinned = pick(((u.get("pinned_items") or {}).get("tweet_ids_str") or [None])[0], (L.get("pinned_tweet_ids_str") or [None])[0])
    return {
        "id": u["rest_id"],
        "handle": pick(core.get("screen_name"), L.get("screen_name")),
        "name": pick(core.get("name"), L.get("name")),
        "bio": pick((u.get("profile_bio") or {}).get("description"), L.get("description")),
        "location": pick(loc, L.get("location")),
        "url": url,
        "created_at": _when(pick(core.get("created_at"), L.get("created_at"))),
        "followers": _int(pick(rc.get("followers"), L.get("followers_count"))),
        "following": _int(pick(rc.get("following"), L.get("friends_count"))),
        "posts": _int(pick(tc.get("tweets"), L.get("statuses_count"))),
        "media": _int(pick(tc.get("media_tweets"), L.get("media_count"))),
        "likes": _int(pick((u.get("action_counts") or {}).get("favorites_count"), L.get("favourites_count"))),
        "verified": bool(u.get("is_blue_verified") or (u.get("verification") or {}).get("verified") or L.get("verified")),
        "protected": bool((u.get("privacy") or {}).get("protected") or L.get("protected")),
        "avatar": (avatar or "").replace("_normal", "_400x400") or None,
        "banner": pick((u.get("banner") or {}).get("image_url"), L.get("profile_banner_url")),
        "pinned": pinned,
        "follows_me": bool(pick(rp.get("followed_by"), L.get("followed_by"))),
        "i_follow": bool(pick(rp.get("following"), L.get("following"))),
        "i_block": bool(pick(rp.get("blocking"), L.get("blocking"))),
        "i_mute": bool(pick(rp.get("muting"), L.get("muting"))),
    }


def _media(L):
    out = []
    for m in ((L.get("extended_entities") or L.get("entities") or {}).get("media") or []):
        item = {"type": m.get("type"), "url": m.get("media_url_https"), "alt": m.get("ext_alt_text")}
        variants = [v for v in ((m.get("video_info") or {}).get("variants") or []) if v.get("content_type") == "video/mp4"]
        if variants:
            item["video"] = max(variants, key=lambda v: v.get("bitrate") or 0)["url"]
        out.append(item)
    return out


def _text(t, L):
    note = (((t.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {})
    s = note.get("text") or L.get("full_text") or ""
    ents = note.get("entity_set") or L.get("entities") or {}
    for u in ents.get("urls") or []:
        if u.get("url") and u.get("expanded_url"):
            s = s.replace(u["url"], u["expanded_url"])
    for m in ((L.get("extended_entities") or L.get("entities") or {}).get("media") or []):
        if m.get("url"):
            s = s.replace(m["url"], "")
    return s.strip()


def tweet(t, depth=0):
    """A GraphQL Tweet result as a plain dict."""
    t = unwrap(t)
    if not isinstance(t, dict) or t.get("__typename") != "Tweet" or "legacy" not in t:
        return None
    L = t["legacy"]
    u = user(((t.get("core") or {}).get("user_results") or {}).get("result") or {}) or {}
    q = None
    if depth < 1:
        q = tweet(((t.get("quoted_status_result") or {}).get("result")), depth + 1)
    rt = None
    if depth < 1 and L.get("retweeted_status_result"):
        rt = tweet(L["retweeted_status_result"].get("result"), depth + 1)
    handle = u.get("handle")
    return {
        "id": t["rest_id"],
        "url": f"https://x.com/{handle}/status/{t['rest_id']}" if handle else f"https://x.com/i/status/{t['rest_id']}",
        "author": {k: u.get(k) for k in ("id", "handle", "name", "followers", "verified")},
        "text": _text(t, L),
        "created_at": _when(L.get("created_at")),
        "lang": L.get("lang"),
        "views": _int((t.get("views") or {}).get("count")),
        "likes": _int(L.get("favorite_count")),
        "retweets": _int(L.get("retweet_count")),
        "replies": _int(L.get("reply_count")),
        "quotes": _int(L.get("quote_count")),
        "bookmarks": _int(L.get("bookmark_count")),
        "media": _media(L),
        "reply_to": L.get("in_reply_to_status_id_str"),
        "conversation": L.get("conversation_id_str"),
        "quoted": q,
        "retweeted": rt,
        "liked": bool(L.get("favorited")),
        "retweeted_by_me": bool(L.get("retweeted")),
        "bookmarked": bool(L.get("bookmarked")),
    }


def walk(bodies):
    """Every tweet and user in a list of responses, in the order they appear, without duplicates."""
    tweets, users = {}, {}

    def go(o):
        if isinstance(o, dict):
            tn = o.get("__typename")
            if tn in ("Tweet", "TweetWithVisibilityResults") and ("legacy" in o or "tweet" in o):
                t = tweet(o)
                if t and t["id"] not in tweets:
                    tweets[t["id"]] = t
            elif tn == "User" and "rest_id" in o:
                u = user(o)
                if u and u["id"] not in users:
                    users[u["id"]] = u
            for v in o.values():
                go(v)
        elif isinstance(o, list):
            for v in o:
                go(v)

    for b in bodies:
        go(b)
    return list(tweets.values()), list(users.values())


def created_id(body, key):
    """The id X returns from a mutation such as CreateTweet."""
    def go(o):
        if isinstance(o, dict):
            if key in o and isinstance(o[key], dict):
                r = unwrap((o[key].get("tweet_results") or {}).get("result") or o[key])
                if isinstance(r, dict) and r.get("rest_id"):
                    return r["rest_id"]
                if o[key].get("rest_id"):
                    return o[key]["rest_id"]
            for v in o.values():
                x = go(v)
                if x:
                    return x
        elif isinstance(o, list):
            for v in o:
                x = go(v)
                if x:
                    return x
        return None

    def any_id(o):
        if isinstance(o, dict):
            if isinstance(o.get("rest_id"), str):
                return o["rest_id"]
            for v in o.values():
                x = any_id(v)
                if x:
                    return x
        elif isinstance(o, list):
            for v in o:
                x = any_id(v)
                if x:
                    return x
        return None
    return go(body) or any_id(body)
