"""anyx — use X from any browser, as any of your accounts. No API keys.

usage: anyx [--as HANDLE] [--json] [--show] [--browser NAME] <command> ...

read
  tweet <post>                      one post with its counts and media
  thread <post> [-n N]              a post and the replies under it
  quotes <post> [-n N]              posts quoting it
  user <handle>                     profile and counts
  posts <handle> [-n N] [--replies|--media]
  search <query> [--tab latest|top|people|media] [-n N]
  home [--following] [-n N]
  notifications [--mentions] [-n N]
  bookmarks [-n N] | likes [<handle>] [-n N]
  followers <handle> [-n N] | following <handle> [-n N]
  shot <post> [-o file.png]         screenshot of the post as X shows it

write
  post <text> [--quote <post>] [--reply <post>] [--media f ...] [--at "YYYY-MM-DD HH:MM"]
  delete <post> ...
  scheduled [--delete <text> ...]   list (or delete) scheduled posts
  like | unlike | retweet | unretweet | bookmark | unbookmark | pin | unpin  <post> ...
  follow | unfollow | mute | unmute | block | unblock  <handle> ...
  profile [--name] [--bio] [--location] [--website] [--avatar f] [--banner f] [--drop-banner]

accounts
  accounts list
  accounts add <handle>                      log in by hand once; anyx keeps the cookies
  accounts import <handle> <cookies.json>
  accounts harvest --browser adspower --profile <name>   store every account logged into that browser
  accounts set <handle> [--browser NAME] [--profile NAME] [--cdp URL] [--proxy URL]
  accounts check <handle>
  whoami

<post> is a URL or an id; <handle> is a handle, @handle or profile URL.
--as picks the account (default: the only one, or ANYX_ACCOUNT). --json prints JSON. --show opens a
visible window. Exit codes: 0 ok, 2 usage, 3 logged out, 4 wrong account, 5 X changed its page,
6 rate limited, 7 not found, 8 X did not confirm.
"""

import argparse
import json
import os
import sys

from . import accounts, browsers, config, vault
from .errors import AnyxError, UsageError
from .session import Session

POST_ACTIONS = ("like", "unlike", "retweet", "unretweet", "bookmark", "unbookmark", "pin", "unpin", "delete")
USER_ACTIONS = ("follow", "unfollow", "mute", "unmute", "block", "unblock")


def _globals(argv):
    g = {"as": None, "json": False, "show": False, "browser": None}
    rest = []
    it = iter(argv)
    for a in it:
        if a == "--as":
            g["as"] = next(it, None)
        elif a.startswith("--as="):
            g["as"] = a.split("=", 1)[1]
        elif a == "--json":
            g["json"] = True
        elif a == "--show":
            g["show"] = True
        elif a == "--browser" and (not rest or rest[0] != "accounts"):
            g["browser"] = next(it, None)
        else:
            rest.append(a)
    return g, rest


def _account(g):
    h = g["as"] or os.environ.get("ANYX_ACCOUNT")
    if h:
        return h.lstrip("@").lower()
    known = vault.handles()
    if len(known) == 1:
        return known[0]
    if not known:
        raise UsageError("no accounts yet (anyx accounts add <handle>)")
    raise UsageError(f"which account? use --as ({', '.join(known)})")


def _n(v):
    return f"{v:,}" if isinstance(v, int) else "-"


def _show_tweet(t, indent=""):
    a = t["author"]
    head = f"{indent}@{a.get('handle')}  {t['url']}  {(t.get('created_at') or '')[:16].replace('T', ' ')}"
    stats = (f"{indent}views {_n(t['views'])}  likes {_n(t['likes'])}  rt {_n(t['retweets'])}  "
             f"replies {_n(t['replies'])}  quotes {_n(t['quotes'])}  bookmarks {_n(t['bookmarks'])}")
    lines = [head, stats] + [indent + l for l in (t.get("text") or "").splitlines()]
    for m in t.get("media") or []:
        lines.append(f"{indent}  [{m['type']}] {m.get('video') or m['url']}")
    if t.get("quoted"):
        q = t["quoted"]
        lines.append(f"{indent}  quoting @{q['author'].get('handle')}: {(q.get('text') or '')[:100]}")
    return "\n".join(lines)


def _show_user(u):
    return "\n".join(x for x in [
        f"@{u['handle']}  {u['name'] or ''}{'  [verified]' if u['verified'] else ''}{'  [protected]' if u['protected'] else ''}",
        f"followers {_n(u['followers'])}  following {_n(u['following'])}  posts {_n(u['posts'])}  since {(u.get('created_at') or '')[:10]}",
        u.get("bio") or "", u.get("location") or "", u.get("url") or ""] if x)


def _print(out, as_json):
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    if isinstance(out, list):
        for o in out:
            print(_fmt(o))
            print()
        print(f"({len(out)})")
    else:
        print(_fmt(out))


def _fmt(o):
    if isinstance(o, dict) and "text" in o and "author" in o:
        return _show_tweet(o)
    if isinstance(o, dict) and "handle" in o and "followers" in o:
        return _show_user(o)
    if isinstance(o, dict):
        return "  ".join(f"{k}={v}" for k, v in o.items())
    return str(o)


def _parser():
    ap = argparse.ArgumentParser(prog="anyx", add_help=False)
    sub = ap.add_subparsers(dest="cmd")

    def add(name, *args, **kw):
        s = sub.add_parser(name, add_help=False)
        for a in args:
            s.add_argument(a)
        return s

    for name in ("tweet", "user"):
        add(name, "ref")
    for name in ("thread", "quotes"):
        add(name, "ref").add_argument("-n", type=int, default=40)
    s = add("posts", "ref"); s.add_argument("-n", type=int, default=40)
    s.add_argument("--replies", action="store_true"); s.add_argument("--media", action="store_true")
    s = add("search", "query"); s.add_argument("--tab", default="latest"); s.add_argument("-n", type=int, default=40)
    s = add("home"); s.add_argument("--following", action="store_true"); s.add_argument("-n", type=int, default=40)
    s = add("notifications"); s.add_argument("--mentions", action="store_true"); s.add_argument("-n", type=int, default=40)
    s = add("bookmarks"); s.add_argument("-n", type=int, default=40)
    s = add("likes"); s.add_argument("ref", nargs="?"); s.add_argument("-n", type=int, default=40)
    for name in ("followers", "following"):
        add(name, "ref").add_argument("-n", type=int, default=100)
    s = add("shot", "ref"); s.add_argument("-o", "--out")
    s = add("post", "text"); s.add_argument("--quote"); s.add_argument("--reply")
    s.add_argument("--media", nargs="+"); s.add_argument("--at")
    for name in POST_ACTIONS:
        add(name).add_argument("refs", nargs="+")
    for name in USER_ACTIONS:
        add(name).add_argument("refs", nargs="+")
    s = add("scheduled"); s.add_argument("--delete", nargs="+")
    s = add("profile")
    for f in ("--name", "--bio", "--location", "--website", "--avatar", "--banner"):
        s.add_argument(f)
    s.add_argument("--drop-banner", action="store_true")
    add("whoami")
    s = add("accounts", "action")
    s.add_argument("args", nargs="*")
    for f in ("--browser", "--profile", "--cdp", "--proxy"):
        s.add_argument(f)
    return ap


def run(argv):
    g, argv = _globals(argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0
    if argv[0] in ("-V", "--version"):
        from . import __version__
        print(__version__)
        return 0
    try:
        a = _parser().parse_args(argv)
    except SystemExit:
        raise UsageError("bad arguments (anyx --help)")
    if not a.cmd:
        raise UsageError("no command (anyx --help)")

    if a.cmd == "accounts":
        act, rest = a.action, a.args
        if act == "list":
            out = accounts.listing()
        elif act == "add" and rest:
            print("A browser window opens on X's login page. Log in there; anyx waits.", file=sys.stderr)
            out = accounts.add(rest[0])
        elif act == "import" and len(rest) == 2:
            out = accounts.import_file(rest[0], rest[1])
        elif act == "harvest" and a.browser:
            out = accounts.harvest(a.browser, profile=a.profile, cdp=a.cdp)
        elif act == "set" and rest:
            if a.browser and a.browser not in browsers.NAMES:
                raise UsageError(f"browser is one of {', '.join(browsers.NAMES)}")
            out = {"user": rest[0].lstrip("@").lower(),
                   **config.set_account(rest[0], browser=a.browser, profile=a.profile, cdp=a.cdp, proxy=a.proxy)}
        elif act == "check" and rest:
            out = accounts.check(rest[0])
        else:
            raise UsageError("accounts list | add <h> | import <h> <file> | harvest --browser B [--profile P|--cdp URL] | set <h> ... | check <h>")
        _print(out, g["json"])
        return 0

    handle = _account(g)
    with Session(handle, headless=not g["show"], browser=g["browser"]) as x:
        c = a.cmd
        if c == "whoami":
            out = {"user": x.whoami(), "browser": x.account["browser"]}
        elif c == "tweet":
            out = x.tweet(a.ref)
        elif c == "thread":
            out = x.thread(a.ref, limit=a.n)
        elif c == "quotes":
            out = x.quotes(a.ref, limit=a.n)
        elif c == "user":
            out = x.user(a.ref)
        elif c == "posts":
            out = x.timeline(a.ref, limit=a.n, replies=a.replies, media=a.media)
        elif c == "search":
            out = x.search(a.query, tab=a.tab, limit=a.n)
        elif c == "home":
            out = x.home(limit=a.n, following=a.following)
        elif c == "notifications":
            out = x.notifications(limit=a.n, mentions=a.mentions)
        elif c == "bookmarks":
            out = x.bookmarks(limit=a.n)
        elif c == "likes":
            out = x.likes(a.ref, limit=a.n)
        elif c in ("followers", "following"):
            out = getattr(x, c)(a.ref, limit=a.n)
        elif c == "shot":
            from .session import tweet_id
            out = {"file": x.shot(a.ref, a.out or f"{tweet_id(a.ref)}.png")}
        elif c == "post":
            out = x.post(a.text.replace("\\n", "\n"), quote=a.quote, reply=a.reply, media=a.media, at=a.at)
        elif c in POST_ACTIONS or c in USER_ACTIONS:
            out = [getattr(x, c)(r) for r in a.refs]
            out = out[0] if len(out) == 1 else out
        elif c == "scheduled":
            out = x.delete_scheduled(a.delete) if a.delete else x.scheduled()
        elif c == "profile":
            if not any([a.name, a.bio is not None, a.location, a.website, a.avatar, a.banner, a.drop_banner]):
                out = x.user(x.me)
            else:
                out = x.edit_profile(name=a.name, bio=a.bio, location=a.location, website=a.website,
                                     avatar=a.avatar, banner=a.banner, drop_banner=a.drop_banner)
        else:
            raise UsageError(f"unknown command {c}")
    _print(out, g["json"])
    return 0


def main():
    try:
        sys.exit(run(sys.argv[1:]))
    except AnyxError as e:
        print(f"anyx: {e}", file=sys.stderr)
        sys.exit(e.code)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        msg = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        if "Executable doesn't exist" in str(e):
            msg = "Playwright's browser is not installed: run `playwright install chromium`"
        elif "Timeout" in type(e).__name__ or "Timeout" in msg:
            msg = f"timed out waiting for X ({msg})"
        print(f"anyx: {msg}", file=sys.stderr)
        if os.environ.get("ANYX_DEBUG"):
            raise
        sys.exit(1)


if __name__ == "__main__":
    main()
