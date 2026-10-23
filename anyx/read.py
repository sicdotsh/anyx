"""Reading. Every function opens the page a person would open, listens to the JSON the page
fetches, and returns plain dicts (see graphql.tweet and graphql.user). No API key is involved."""

import os
import urllib.parse

from . import graphql
from .errors import NotFound, UsageError

TIMELINE_OPS = ("UserTweets", "UserOriginalsTimeline", "UserTweetsAndReplies", "UserRepliesTimeline", "UserMedia",
                "UserVideoTimeline", "UserMediaTimeline", "UserHighlightsTweets")


def _collect(self, url, ops, limit=40, pick=None, scrolls=40, first_wait=30):
    """Open `url`, wait for one of `ops`, scroll until `limit` items (as counted by `pick`) or no more."""
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, url)
        pick = pick or (lambda tw, us: tw)

        def loaded():
            got = cap.of(*ops)
            hit = [n for n in cap.limited if n in ops]
            if not got and hit:
                raise cap.limit_error(hit[0])
            if not got:
                return None
            # on a page that was already open X only polls for newer items (cursors, no content): that is
            # not the list. A real load's answer is, even when empty.
            if pick(*graphql.walk(got)) or getattr(p, "spa_url", None) is None:
                return True
            return None

        if cap._wait(loaded, first_wait) is None:
            if "Viewer" in cap.limited:
                raise cap.limit_error("Viewer")
            raise NotFound(f"nothing loaded at {url} (no {', '.join(ops)})")
        p.wait_for_timeout(1200)
        stale = 0
        last = -1
        for _ in range(scrolls):
            tw, us = graphql.walk(cap.of(*ops))
            items = pick(tw, us)
            if len(items) >= limit:
                break
            if len(items) == last:
                stale += 1
                if stale >= 3:
                    break
            else:
                stale = 0
            last = len(items)
            p.mouse.wheel(0, 6000)
            p.wait_for_timeout(1500)
        tw, us = graphql.walk(cap.of(*ops))
        return pick(tw, us)[:limit], cap
    finally:
        cap.detach()
        p.close()


def tweet(self, ref):
    """One post, with its counts, media and the post it quotes."""
    from .session import tweet_id
    tid = tweet_id(ref)
    items, _ = _collect(self, f"https://x.com/i/status/{tid}", ("TweetDetail", "TweetResultByRestId"),
                        limit=1, pick=lambda tw, us: [t for t in tw if t["id"] == tid], scrolls=0)
    if not items:
        raise NotFound(f"post {tid} not found")
    return items[0]


def thread(self, ref, limit=50):
    """A post and the replies shown under it, in page order (the post first)."""
    from .session import tweet_id
    tid = tweet_id(ref)

    def pick(tw, us):
        focal = next((t for t in tw if t["id"] == tid), None)
        if not focal:
            return []
        conv = focal["conversation"] or tid
        return [focal] + [t for t in tw if t["id"] != tid and t["conversation"] == conv]

    items, _ = _collect(self, f"https://x.com/i/status/{tid}", ("TweetDetail",), limit=limit, pick=pick)
    if not items:
        raise NotFound(f"post {tid} not found")
    return items


def quotes(self, ref, limit=40):
    """Posts that quote the post."""
    from .session import tweet_id
    tid = tweet_id(ref)
    return _collect(self, f"https://x.com/i/status/{tid}/quotes", ("SearchTimeline",), limit=limit,
                    pick=lambda tw, us: [t for t in tw if (t.get("quoted") or {}).get("id") == tid])[0]


def user(self, ref):
    """An account's profile and counts."""
    from .session import handle_of
    h = handle_of(ref)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/{h}")
        b = cap.wait_for(("UserByScreenName",), timeout=30)
        res = (((b or {}).get("data") or {}).get("user") or {}).get("result") or {}
        if res.get("__typename") == "UserUnavailable":
            raise NotFound(f"@{h} is unavailable ({res.get('reason') or res.get('message') or 'unknown reason'})")
        _, us = graphql.walk([b] if b else [])
        u = next((x for x in us if (x["handle"] or "").lower() == h.lower()), None)
        if not u:
            raise NotFound(f"@{h} not found")
        return u
    finally:
        cap.detach()
        p.close()


def timeline(self, ref, limit=40, replies=False, media=False):
    """An account's posts, newest first. Includes its reposts; the pinned post is not moved to the top."""
    from .session import handle_of
    h = handle_of(ref)
    tab = "/with_replies" if replies else "/media" if media else ""
    items, _ = _collect(self, f"https://x.com/{h}{tab}", TIMELINE_OPS, limit=limit,
                        pick=lambda tw, us: sorted([t for t in tw if (t["author"]["handle"] or "").lower() == h.lower()],
                                                   key=lambda t: int(t["id"]), reverse=True))
    return items


def search(self, query, tab="latest", limit=40):
    """X search. tab: latest, top, people, media. Advanced operators work (from:, min_faves:, since:, ...)."""
    f = {"latest": "live", "top": "top", "people": "user", "media": "media"}.get(tab)
    if not f:
        raise UsageError("tab is latest, top, people or media")
    url = "https://x.com/search?" + urllib.parse.urlencode({"q": query, "src": "typed_query", "f": f})
    pick = (lambda tw, us: us) if tab == "people" else None
    return _collect(self, url, ("SearchTimeline",), limit=limit, pick=pick)[0]


ICONS = {"heart_icon": "like", "person_icon": "follow", "retweet_icon": "repost", "bell_icon": "post",
         "bird_icon": "x", "list_icon": "list", "milestone_icon": "milestone", "spaces_icon": "space",
         "recommendation_icon": "recommendation", "security_alert_icon": "security", "lightning_bolt_icon": "trend"}


def _notification_items(bodies):
    """Notifications in page order. Likes, follows, reposts... come as TimelineNotification; replies,
    mentions and quotes come as posts."""
    out, seen = [], set()

    def go(o):
        if isinstance(o, dict):
            if "entryId" in o and "content" in o and o["entryId"] not in seen:
                ic = (o["content"] or {}).get("itemContent") or {}
                if ic.get("__typename") == "TimelineNotification":
                    seen.add(o["entryId"])
                    t = ic.get("template") or {}
                    users = [graphql.user((u.get("user_results") or {}).get("result") or {}) for u in t.get("from_users") or []]
                    posts = [graphql.tweet((x.get("tweet_results") or {}).get("result")) for x in t.get("target_objects") or []]
                    out.append({"kind": ICONS.get(ic.get("notification_icon"), ic.get("notification_icon")),
                                "text": (ic.get("rich_message") or {}).get("text"),
                                "time": graphql.when_ms(ic.get("timestamp_ms")),
                                "users": [u["handle"] for u in users if u],
                                "posts": [x["id"] for x in posts if x],
                                "id": ic.get("id")})
                    return
                if ic.get("__typename") == "TimelineTweet":
                    seen.add(o["entryId"])
                    t = graphql.tweet(((ic.get("tweet_results") or {}).get("result")))
                    if t:
                        kind = "reply" if t.get("reply_to") else "quote" if t.get("quoted") else "mention"
                        out.append({"kind": kind, "text": t["text"], "time": t["created_at"],
                                    "users": [t["author"]["handle"]], "posts": [t["id"]], "post": t})
                    return
            for v in o.values():
                go(v)
        elif isinstance(o, list):
            for v in o:
                go(v)
    go(bodies)
    return out


def notifications(self, limit=40, mentions=False):
    """The notifications tab: likes, follows, reposts, replies, mentions, quotes... newest first.
    Each item has kind, text, time, users (handles) and posts (ids); replies and mentions carry the post."""
    url = "https://x.com/notifications/mentions" if mentions else "https://x.com/notifications"
    return _notifications_from(self, url, limit)


def _notifications_from(self, url, limit):
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, url)
        if cap.wait_for(("NotificationsTimeline",), timeout=30) is None:
            raise NotFound("notifications did not load")
        p.wait_for_timeout(1200)
        last = -1
        for _ in range(20):
            items = _notification_items(cap.of("NotificationsTimeline"))
            if len(items) >= limit or len(items) == last:
                break
            last = len(items)
            p.mouse.wheel(0, 6000)
            p.wait_for_timeout(1500)
        return _notification_items(cap.of("NotificationsTimeline"))[:limit]
    finally:
        cap.detach()
        p.close()


def trends(self, tab="trending", limit=50):
    """Explore: trending topics. tab: for_you, trending, news, sports, entertainment."""
    if tab not in ("for_you", "trending", "news", "sports", "entertainment"):
        raise UsageError("tab is for_you, trending, news, sports or entertainment")
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/explore/tabs/{tab}")
        if cap.wait_for(("GenericTimelineById", "ExplorePage"), timeout=30) is None:
            raise NotFound("explore did not load")
        p.wait_for_timeout(2000)
        out, seen = [], set()

        def go(o):
            if isinstance(o, dict):
                if o.get("itemType") == "TimelineTrend" and o.get("name") and o["name"] not in seen:
                    seen.add(o["name"])
                    ctx = (o.get("social_context") or {}).get("text") or ""
                    out.append({"name": o["name"], "context": ctx,
                                "ai": bool(o.get("is_ai_trend")),
                                "url": "https://x.com/search?" + urllib.parse.urlencode({"q": o["name"]})})
                    return
                for v in o.values():
                    go(v)
            elif isinstance(o, list):
                for v in o:
                    go(v)
        go(cap.of("GenericTimelineById", "ExplorePage"))
        return out[:limit]
    finally:
        cap.detach()
        p.close()


def home(self, limit=40, following=False):
    """The home timeline: For you, or Following with following=True."""
    p_ops = ("HomeLatestTimeline",) if following else ("HomeTimeline",)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, "https://x.com/home")
        # X opens home on the tab chosen last time: pick the asked one (For you, Following)
        tab = p.locator('[data-testid="primaryColumn"] [role="tab"]').nth(1 if following else 0)
        tab.wait_for(state="visible", timeout=20000)
        if tab.get_attribute("aria-selected") != "true":
            tab.click()
        if cap.wait_for(p_ops, timeout=30) is None:
            raise NotFound("home timeline did not load")
        for _ in range(20):
            tw, _ = graphql.walk(cap.of(*p_ops))
            if len(tw) >= limit:
                break
            p.mouse.wheel(0, 6000)
            p.wait_for_timeout(1500)
        return graphql.walk(cap.of(*p_ops))[0][:limit]
    finally:
        cap.detach()
        p.close()


def bookmarks(self, limit=40):
    return _collect(self, "https://x.com/i/bookmarks", ("Bookmarks", "BookmarkSearchTimeline"), limit=limit)[0]


def likes(self, ref=None, limit=40):
    """Posts an account liked. X only shows this for the logged-in account itself."""
    from .session import handle_of
    h = handle_of(ref) if ref else self.me
    return _collect(self, f"https://x.com/{h}/likes", ("Likes",), limit=limit)[0]


def _people(self, ref, kind, limit):
    from .session import handle_of
    h = handle_of(ref)
    ops = {"followers": ("Followers", "BlueVerifiedFollowers"), "following": ("Following",)}[kind]
    return _collect(self, f"https://x.com/{h}/{kind}", ops, limit=limit,
                    pick=lambda tw, us: [u for u in us if (u["handle"] or "").lower() != h.lower()])[0]


def followers(self, ref, limit=100):
    return _people(self, ref, "followers", limit)


def following(self, ref, limit=100):
    return _people(self, ref, "following", limit)


def shot(self, ref, out):
    """A screenshot of the post exactly as X shows it to this account. Returns the file path."""
    from .session import tweet_id
    tid = tweet_id(ref)
    p = self.page()
    try:
        self.goto(p, f"https://x.com/i/status/{tid}")
        art = self.article(p, tid)
        p.wait_for_timeout(2500)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        art.screenshot(path=out)
        return out
    finally:
        p.close()


def analytics(self, ref):
    """A post's analytics, as its author sees them: impressions, engagements, detail expands, profile
    visits, link clicks, follows, and video plays and completions. Only for this account's own posts."""
    from .session import tweet_id
    tid = tweet_id(ref)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/{self.me}/status/{tid}/analytics")
        b = cap.wait_for(("TweetActivityQuery",), timeout=30)
        if b is None:
            raise NotFound(f"no analytics for {tid} (is it @{self.me}'s own post?)")
        r = ((b.get("data") or {}).get("tweet_result_by_rest_id") or {}).get("result") or {}
        snake = lambda s: "".join("_" + c.lower() if c.isupper() else c for c in s).lstrip("_")
        out = {"id": tid}
        for group in ("datapoints_grid", "video"):
            for m in r.get(group) or []:
                out[snake(m["metric_type"])] = m.get("metric_value", 0)
        return out
    finally:
        cap.detach()
        p.close()
