"""Live test against real X. Runs every command once as one account against another, then puts
everything back the way it was (follow state, pinned post, profile bio; test posts are deleted).

    python tests/live.py --as <your handle> --target <someone else's handle> [--peer <your second handle>]

--peer is a second account of yours with chat set up (anyx --as <peer> chat setup --pin NNNN). It is
used for messages, which X only allows between accounts that follow each other or have DMs open.

Prints one line per step and exits non-zero if any step failed. Meant to be run daily: when X
changes a page, the step that breaks says which marker or request went missing.
"""

import argparse
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import anyx  # noqa: E402

PAUSE = 3.0
results = []


def step(name, fn, expect=None, soft=False):
    """soft: a miss is printed as "warn" and not counted as a failure (X-side delays, e.g. search indexing)."""
    t = time.monotonic()
    try:
        out = fn()
        ok = True
        if expect is not None:
            ok = expect(out)
        note = _brief(out)
    except Exception as e:  # noqa: BLE001
        ok, out, note = False, None, f"{type(e).__name__}: {e}"
        if os.environ.get("ANYX_DEBUG"):
            traceback.print_exc()
    dt = time.monotonic() - t
    results.append((name, ok or soft, dt, note))
    print(f"{'ok  ' if ok else 'warn' if soft else 'FAIL'} {name:28s} {dt:5.1f}s  {note}", flush=True)
    time.sleep(PAUSE)
    return out


def _brief(o):
    if isinstance(o, list):
        return f"{len(o)} items"
    if isinstance(o, dict):
        keys = [k for k in ("result", "id", "handle", "user", "scheduled", "deleted", "file", "followers") if k in o]
        return "  ".join(f"{k}={o[k]}" for k in keys)
    return str(o)[:80]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="me", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--peer")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()
    tag = datetime.now().strftime("%m%d%H%M%S")
    tmp = tempfile.mkdtemp(prefix="anyx-live-")

    with anyx.Session(a.me, headless=not a.show) as x:
        me = step("whoami", x.whoami, lambda h: h.lower() == a.me.lower())
        mine = step("user (me)", lambda: x.user(a.me), lambda u: u["handle"].lower() == a.me.lower())
        target = step("user (target)", lambda: x.user(a.target), lambda u: bool(u["id"]))
        if not target:
            return finish(tmp)
        posts = step("posts (target)", lambda: x.timeline(a.target, limit=5), lambda l: len(l) > 0)
        own = [t for t in posts or [] if not t.get("retweeted") and (t["author"]["handle"] or "").lower() == a.target.lower()]
        if not own:
            return finish(tmp)
        P = own[0]["id"]

        # reading
        step("tweet", lambda: x.tweet(P), lambda t: t["id"] == P and t["views"] is not None)
        step("thread", lambda: x.thread(P, limit=5), lambda l: l and l[0]["id"] == P)
        step("quotes", lambda: x.quotes(P, limit=5), lambda l: isinstance(l, list))
        step("search latest", lambda: x.search(f"from:{a.target}", limit=5), lambda l: len(l) > 0)
        step("search people", lambda: x.search(a.target, tab="people", limit=3), lambda l: len(l) > 0)
        step("home", lambda: x.home(limit=5), lambda l: len(l) > 0)
        step("home following", lambda: x.home(limit=5, following=True), lambda l: isinstance(l, list))
        step("notifications", lambda: x.notifications(limit=5), lambda l: isinstance(l, list))
        step("following (target)", lambda: x.following(a.target, limit=5), lambda l: isinstance(l, list))
        step("followers (target)", lambda: x.followers(a.target, limit=5), lambda l: len(l) > 0)
        step("shot", lambda: {"file": x.shot(P, os.path.join(tmp, "shot.png"))},
             lambda o: os.path.getsize(o["file"]) > 1000)

        # engaging with the target's post
        done = lambda o: o["result"] in ("done", "already")
        step("like", lambda: x.like(P), done)
        step("like (again)", lambda: x.like(P), lambda o: o["result"] == "already")
        step("likes (me)", lambda: x.likes(limit=5), lambda l: any(t["id"] == P for t in l))
        step("unlike", lambda: x.unlike(P), done)
        step("retweet", lambda: x.retweet(P), done)
        step("unretweet", lambda: x.unretweet(P), done)
        step("bookmark", lambda: x.bookmark(P), done)
        step("bookmarks", lambda: x.bookmarks(limit=5), lambda l: any(t["id"] == P for t in l))
        step("unbookmark", lambda: x.unbookmark(P), done)

        # relationships, restored to how they were
        was_following = bool(target.get("i_follow"))
        if was_following:
            step("unfollow", lambda: x.unfollow(a.target), done)
            step("follow", lambda: x.follow(a.target), done)
        else:
            step("follow", lambda: x.follow(a.target), done)
            step("follow (again)", lambda: x.follow(a.target), lambda o: o["result"] == "already")
            step("unfollow", lambda: x.unfollow(a.target), done)
        step("mute", lambda: x.mute(a.target), done)
        step("unmute", lambda: x.unmute(a.target), done)
        step("block", lambda: x.block(a.target), done)
        step("unblock", lambda: x.unblock(a.target), done)
        if was_following:
            step("follow (restore)", lambda: x.follow(a.target), done)
        step("relationship restored", lambda: x.user(a.target),
             lambda u: bool(u["i_follow"]) == was_following and not u["i_mute"] and not u["i_block"])

        # posting, then cleaning up
        img = os.path.join(tmp, "card.png")
        pg = x.page()
        pg.set_content(f"<body style='margin:0;background:#111;color:#fff;font:64px sans-serif;"
                       f"display:flex;align-items:center;justify-content:center;width:800px;height:800px'>anyx {tag}</body>")
        pg.set_viewport_size({"width": 800, "height": 800})
        pg.screenshot(path=img)
        pg.close()

        mine_pinned = (mine or {}).get("pinned")
        p1 = step("post", lambda: x.post(f"anyx live test {tag}"), lambda o: bool(o.get("id")))
        if p1:
            step("pin", lambda: x.pin(p1["id"]), done)
            step("unpin", lambda: x.unpin(p1["id"]), done)
            if mine_pinned:
                step("pin (restore old)", lambda: x.pin(mine_pinned), done)
        r1 = step("reply", lambda: x.post(f"anyx live test reply {tag}", reply=P), lambda o: bool(o.get("id")))
        q1 = step("quote", lambda: x.post(f"anyx live test quote {tag}", quote=P), lambda o: bool(o.get("id")))
        if q1:
            step("quote is listed", lambda: x.quotes(P, limit=40),
                 lambda l: any(t["id"] == q1["id"] for t in l), soft=True)
        m1 = step("post with image", lambda: x.post(f"anyx live test image {tag}", media=[img]),
                  lambda o: bool(o.get("id")))
        if m1:
            step("image is attached", lambda: x.tweet(m1["id"]), lambda t: len(t["media"]) == 1)
        at = (datetime.now() + timedelta(days=2)).replace(hour=3, minute=17, second=0, microsecond=0)
        step("schedule", lambda: x.post(f"anyx live test scheduled {tag}", at=at), lambda o: bool(o.get("scheduled")))
        step("scheduled", x.scheduled, lambda l: any(tag in s for s in l))
        step("delete scheduled", lambda: x.delete_scheduled([f"scheduled {tag}"]), lambda o: o["deleted"] == 1)
        for name, o in (("delete post", p1), ("delete reply", r1), ("delete quote", q1), ("delete image post", m1)):
            if o and o.get("id"):
                step(name, lambda i=o["id"]: x.delete(i), lambda r: r["result"] == "deleted")

        # more ways to post
        t1 = step("post poll", lambda: x.post("test", poll={"choices": ["a", "b"], "days": 1}), lambda o: bool(o.get("id")))
        t2 = step("post alt", lambda: x.post("test " + tag, media=[img], alt=["test"]), lambda o: bool(o.get("id")))
        if t2:
            step("alt is set", lambda: x.tweet(t2["id"]), lambda t: (t["media"] or [{}])[0].get("alt") == "test")
        t3 = step("post who=following", lambda: x.post("test  " + tag, who="following"), lambda o: bool(o.get("id")))
        t4 = step("post thread", lambda: x.post("test   " + tag, thread=["test", "test"]), lambda o: len(o.get("ids", [])) == 3)
        for o in (t1, t2, t3):
            if o:
                step("delete", lambda i=o["id"]: x.delete(i), lambda r: r["result"] == "deleted")
        for i in (t4 or {}).get("ids", []):
            step("delete thread post", lambda i=i: x.delete(i), lambda r: r["result"] == "deleted")
        step("draft", lambda: x.post("test draft " + tag, draft=True), lambda o: o["result"] == "saved")
        step("drafts", x.drafts, lambda l: any(tag in d for d in l))
        step("delete draft", lambda: x.delete_drafts([tag]), lambda o: o["deleted"] == 1)
        mine_posts = step("posts (me)", lambda: x.timeline(a.me, limit=3), lambda l: isinstance(l, list))
        if mine_posts:
            step("analytics", lambda: x.analytics(mine_posts[0]["id"]), lambda d: "impressions" in d)

        # notifications, trends, lists
        step("notifications (all kinds)", lambda: x.notifications(limit=10), lambda l: isinstance(l, list))
        step("trends", lambda: x.trends(limit=5), lambda l: len(l) > 0)
        L = step("list create", lambda: x.list_create("test", "test"), lambda o: bool(o.get("id")))
        if L:
            step("list add", lambda: x.list_add(L["id"], a.target), done)
            step("list members", lambda: x.list_members(L["id"]), lambda l: any((u["handle"] or "").lower() == a.target.lower() for u in l))
            step("list posts", lambda: x.list_posts(L["id"], limit=3), lambda l: isinstance(l, list))
            step("list remove", lambda: x.list_remove(L["id"], a.target), done)
            step("list delete", lambda: x.list_delete(L["id"]), lambda o: o["result"] == "deleted")

        # settings, each flipped and put back
        before = step("settings", x.settings, lambda d: "protected" in d) or {}
        for name, key in (("sensitive_media", "display_sensitive_media"), ("personalize_ads", "allow_ads_personalization")):
            was = "on" if before.get(key) else "off"
            flip = "off" if was == "on" else "on"
            step(f"setting {name} {flip}", lambda n=name, f=flip: x.set_setting(n, f), done)
            step(f"setting {name} {was}", lambda n=name, w=was: x.set_setting(n, w), done)
        step("muted add", lambda: x.mute_word("test" + tag), done)
        step("muted list", x.muted_words, lambda l: any(w["word"] == "test" + tag for w in l))
        step("muted remove", lambda: x.unmute_word("test" + tag), done)

        # profile, restored
        if mine:
            old_bio = mine.get("bio") or ""
            step("profile bio", lambda: x.edit_profile(bio=(old_bio + f" ·{tag}")[:160]), done)
            step("profile bio shows", lambda: x.user(a.me), lambda u: tag in (u.get("bio") or ""))
            step("profile bio restore", lambda: x.edit_profile(bio=old_bio), done)

    if a.peer:
        with anyx.Session(a.peer, headless=not a.show) as y:
            step("dm peer -> me", lambda: y.dm(a.me, "test"), lambda o: o["result"] == "sent")
        with anyx.Session(a.me, headless=not a.show) as x:
            step("chats", lambda: x.chats(limit=5), lambda l: len(l) > 0)
            step("messages", lambda: x.messages(a.peer, limit=5), lambda l: any(m["text"] == "test" for m in l))
            step("dm me -> peer", lambda: x.dm(a.peer, "test"), lambda o: o["result"] == "sent")

    return finish(tmp)


def finish(tmp):
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    bad = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(bad)}/{len(results)} passed")
    for name, _, _, note in bad:
        print(f"  FAIL {name}: {note}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
