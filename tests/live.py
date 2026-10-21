"""Live test against real X. Runs every command once as one account against another, then puts
everything back the way it was (follow state, pinned post, profile bio; test posts are deleted).

    python tests/live.py --as <your handle> --target <someone else's handle>

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


def step(name, fn, expect=None):
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
    results.append((name, ok, dt, note))
    print(f"{'ok  ' if ok else 'FAIL'} {name:28s} {dt:5.1f}s  {note}", flush=True)
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
        step("following (target)", lambda: x.following(a.target, limit=5), lambda l: len(l) > 0)
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
                 lambda l: any(t["id"] == q1["id"] for t in l))
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

        # profile, restored
        if mine:
            old_bio = mine.get("bio") or ""
            step("profile bio", lambda: x.edit_profile(bio=(old_bio + f" ·{tag}")[:160]), done)
            step("profile bio shows", lambda: x.user(a.me), lambda u: tag in (u.get("bio") or ""))
            step("profile bio restore", lambda: x.edit_profile(bio=old_bio), done)

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
