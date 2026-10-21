"""anyx — use X from any browser, as any of your accounts. No API keys.

usage: anyx [--as HANDLE|a,b,c|all] [--json] [--show] [--wait] [-j N] [--browser NAME] <command> ...

read
  tweet <post>                      one post with its counts and media
  thread <post> [-n N]              a post and the replies under it
  quotes <post> [-n N]              posts quoting it
  user <handle>                     profile and counts
  posts <handle> [-n N] [--replies|--media]
  search <query> [--tab latest|top|people|media] [-n N]
  home [--following] [-n N]
  trends [--tab for_you|trending|news|sports|entertainment] [-n N]
  notifications [--mentions] [-n N]
  bookmarks [-n N] | likes [<handle>] [-n N]
  followers <handle> [-n N] | following <handle> [-n N]
  shot <post> [-o file.png]         screenshot of the post as X shows it
  analytics <post>                  impressions, engagements, profile visits, video plays (own posts)

write
  post <text> [--quote <post>] [--reply <post>] [--media f ...] [--alt t ...] [--at "YYYY-MM-DD HH:MM"]
       [--poll "a|b|c" [--poll-days D] [--poll-hours H] [--poll-minutes M]]
       [--who everyone|following|verified|mentioned] [--thread t2 t3 ...] [--draft]
  delete <post> ...
  scheduled [--delete <text> ...] [--move <text> --at "YYYY-MM-DD HH:MM"]
  drafts [--delete <text> ...]
  like | unlike | retweet | unretweet | bookmark | unbookmark | pin | unpin  <post> ...
  follow | unfollow | mute | unmute | block | unblock  <handle> ...
  profile [--name] [--bio] [--location] [--website] [--avatar f] [--banner f] [--drop-banner]

settings
  settings                          every setting, as X stores it
  settings set <name> <value>       protected, protect_videos, media_tagging, media_tagging_from,
                                    sensitive_media, quality_filter, personalize_location,
                                    personalize_inferred, personalize_ads, grok_training,
                                    grok_personalize, grok_history, language,
                                    dms (following|verified|everyone), handle (renames the vault too)
  muted [--add word] [--remove word]  muted words

lists
  lists                             lists this account owns or follows
  list create <name> [--description t] [--public]
  list add|remove <list> <handle>   <list> is an id, URL, or the name of one of your lists
  list posts|members <list> [-n N]
  list delete <list>

communities
  community <id|url> [--posts] [-n N]
  join | leave <id|url>

chat
  chat setup --pin NNNN             set (or enter on this browser) the chat passcode
  chats [--box all|unread|direct|groups|requests] [-n N]
  messages <handle|conversation> [-n N]
  dm <handle|conversation> <text> [--media f ...]

accounts
  accounts list
  accounts login <handle>|- --file creds.tsv [--browser NAME]   log in with password (+ TOTP); the usual way
  accounts add <handle>                      log in by hand in a window anyx opens (when X asks for more than a password)
  accounts harvest --browser adspower --profile <name>   adopt the accounts already logged into that browser
  accounts import <handle> <cookies.json> --browser NAME [--profile P]   cookies exported from that browser
  accounts set <handle> [--browser NAME] [--profile NAME] [--cdp URL] [--proxy URL]
  status [<handle>...]                       which accounts have a browser open, which are resting
  stop [<handle>...|all]                     close the browsers anyx left open
  accounts check <handle>|all                ok, expired, locked, suspended, rate_limited (and whether its posts show in search)
  whoami

<post> is a URL or an id; <handle> is a handle, @handle or profile URL.
Each account's browser stays open between commands and pages change inside X's app instead of
reloading. Pausing ("pace") and resting accounts after X pushes back ("rest") are off unless
~/.anyx/config.json turns them on. --as picks the account (default: the only one, or ANYX_ACCOUNT); a,b,c or all runs the command as each,
N at a time (-j, default 4). --json prints JSON. --show opens a visible window. --wait sleeps through
rate limits until X says they lift, then carries on. Exit codes: 0 ok, 2 usage, 3 logged out, 4 wrong account, 5 X changed its page,
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
    g = {"as": None, "json": False, "show": False, "browser": None, "wait": False, "jobs": 4, "force": False}
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
        elif a == "--wait":
            g["wait"] = True
        elif a == "--force":
            g["force"] = True
        elif a in ("-j", "--jobs"):
            g["jobs"] = int(next(it, "4"))
        elif a == "--browser" and (not rest or rest[0] != "accounts"):
            g["browser"] = next(it, None)
        else:
            rest.append(a)
    return g, rest


def _accounts(g):
    """--as a,b,c or --as all. None when only one account is meant."""
    h = g["as"]
    if not h or ("," not in h and h != "all"):
        return None
    if h == "all":
        return vault.handles()
    return [x.strip().lstrip("@").lower() for x in h.split(",") if x.strip()]


def _fan_out(handles, argv, g):
    """Run the same command as each account, in parallel. Accounts that share one browser profile
    run one after another, since a profile can only be open once."""
    import subprocess
    import threading
    groups = {}
    for h in handles:
        a = config.account(h)
        key = (a["browser"], a.get("profile") or a.get("cdp") or h)
        groups.setdefault(key, []).append(h)
    results = {}
    sem = threading.Semaphore(max(1, g["jobs"]))
    extra = (["--show"] if g["show"] else []) + (["--wait"] if g["wait"] else []) + \
            (["--browser", g["browser"]] if g["browser"] else [])

    def run_group(hs):
        with sem:
            for h in hs:
                r = subprocess.run([sys.executable, "-m", "anyx", "--as", h, "--json", *extra, *argv],
                                   capture_output=True, text=True)
                try:
                    out = json.loads(r.stdout) if r.stdout.strip() else None
                except json.JSONDecodeError:
                    out = r.stdout.strip()
                results[h] = {"account": h, "ok": r.returncode == 0, "code": r.returncode,
                              "result": out, "error": r.stderr.strip().removeprefix("anyx: ") or None}
                if not g["json"]:
                    mark = "ok  " if r.returncode == 0 else f"E{r.returncode:<3}"
                    print(f"{mark} @{h}  {(results[h]['error'] or _brief(out))}", flush=True)

    ts = [threading.Thread(target=run_group, args=(hs,)) for hs in groups.values()]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    rows = [results[h] for h in handles if h in results]
    if g["json"]:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    return 0 if all(r["ok"] for r in rows) else 1


def _brief(o):
    if isinstance(o, list):
        return f"{len(o)} items"
    if isinstance(o, dict):
        return "  ".join(f"{k}={o[k]}" for k in list(o)[:4])
    return str(o)[:100]


def _account(g):
    h = g["as"] or os.environ.get("ANYX_ACCOUNT")
    if h:
        return h.lstrip("@").lower()
    known = vault.handles()
    if len(known) == 1:
        return known[0]
    if not known:
        raise UsageError("no accounts yet (anyx accounts login <handle> --file <creds>)")
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
    if isinstance(o, dict) and "kind" in o and "users" in o:
        return f"[{(o.get('time') or '')[:16]}] {o['kind']}: {o.get('text') or ''}"
    if isinstance(o, dict) and "context" in o and "ai" in o:
        return f"{o['name']}   ({o['context']})"
    if isinstance(o, dict) and "status" in o and "mine" in o:
        who = "me" if o["mine"] else (o.get("sender") or "?")
        return f"[{o.get('time') or ''}] {who}: {o.get('text') or ''}" + "".join(f"\n  [media] {m}" for m in o.get("media") or [])
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
    s = add("trends"); s.add_argument("--tab", default="trending"); s.add_argument("-n", type=int, default=30)
    s = add("likes"); s.add_argument("ref", nargs="?"); s.add_argument("-n", type=int, default=40)
    for name in ("followers", "following"):
        add(name, "ref").add_argument("-n", type=int, default=100)
    s = add("shot", "ref"); s.add_argument("-o", "--out")
    add("analytics", "ref")
    s = add("post", "text"); s.add_argument("--quote"); s.add_argument("--reply")
    s.add_argument("--media", nargs="+"); s.add_argument("--at"); s.add_argument("--alt", nargs="+")
    s.add_argument("--poll"); s.add_argument("--poll-days", type=int); s.add_argument("--poll-hours", type=int)
    s.add_argument("--poll-minutes", type=int); s.add_argument("--who"); s.add_argument("--thread", nargs="+")
    s.add_argument("--draft", action="store_true")
    for name in POST_ACTIONS:
        add(name).add_argument("refs", nargs="+")
    for name in USER_ACTIONS:
        add(name).add_argument("refs", nargs="+")
    s = add("scheduled"); s.add_argument("--delete", nargs="+"); s.add_argument("--move"); s.add_argument("--at")
    s = add("drafts"); s.add_argument("--delete", nargs="+")
    s = add("chat", "action"); s.add_argument("--pin")
    add("lists")
    s = add("community", "ref"); s.add_argument("--posts", action="store_true"); s.add_argument("-n", type=int, default=40)
    add("join", "ref"); add("leave", "ref")
    s = add("settings"); s.add_argument("args", nargs="*")
    s = add("muted"); s.add_argument("--add"); s.add_argument("--remove")
    s = add("list", "action"); s.add_argument("args", nargs="*"); s.add_argument("--description", default="")
    s.add_argument("--public", action="store_true"); s.add_argument("-n", type=int, default=40)
    s = add("chats"); s.add_argument("--box", default="all"); s.add_argument("-n", type=int, default=30)
    s = add("messages", "ref"); s.add_argument("-n", type=int, default=50)
    s = add("dm", "to", "text"); s.add_argument("--media", nargs="+")
    s = add("profile")
    for f in ("--name", "--bio", "--location", "--website", "--avatar", "--banner"):
        s.add_argument(f)
    s.add_argument("--drop-banner", action="store_true")
    add("whoami")
    add("stop").add_argument("refs", nargs="*")
    add("status").add_argument("refs", nargs="*")
    s = add("accounts", "action")
    s.add_argument("args", nargs="*")
    for f in ("--browser", "--profile", "--cdp", "--proxy", "--file"):
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

    if a.cmd in ("stop", "status"):
        from . import run as _run
        hs = vault.handles() if (not a.refs or a.refs == ["all"]) else [h.lstrip("@").lower() for h in a.refs]
        rows = []
        for h in hs:
            st = _run.load(h)
            if a.cmd == "stop":
                kind = (st.get("browser") or {}).get("kind")
                done = browsers.get(kind if kind in browsers.NAMES else "chromium").stop(h) if kind else False
                if done:
                    rows.append({"user": h, "result": "stopped"})
            else:
                import time as _t
                c = _run.cooling(h)
                rows.append({"user": h, "browser": (st.get("browser") or {}).get("kind") or "-",
                             "resting": f"{int((c[0] - _t.time()) // 60)}m: {c[1]}" if c else "-"})
        _print(rows, g["json"])
        return 0
    if a.cmd == "accounts":
        act, rest = a.action, a.args
        if act == "list":
            out = accounts.listing()
        elif act == "add" and rest:
            print("A browser window opens on X's login page. Log in there; anyx waits.", file=sys.stderr)
            out = accounts.add(rest[0])
        elif act == "import" and len(rest) == 2:
            out = accounts.import_file(rest[0], rest[1], browser=a.browser, profile=a.profile)
        elif act == "harvest" and a.browser:
            out = accounts.harvest(a.browser, profile=a.profile, cdp=a.cdp)
        elif act == "set" and rest:
            if a.browser and a.browser not in browsers.NAMES:
                raise UsageError(f"browser is one of {', '.join(browsers.NAMES)}")
            out = {"user": rest[0].lstrip("@").lower(),
                   **config.set_account(rest[0], browser=a.browser, profile=a.profile, cdp=a.cdp, proxy=a.proxy)}
        elif act == "check" and rest:
            hs = vault.handles() if rest[0] == "all" else rest
            out = [accounts.check(h) for h in hs]
            out = out[0] if len(out) == 1 else out
        elif act == "login" and a.file:
            from . import login
            creds = login.read_file(a.file)
            if rest and rest[0] != "-":
                creds = [c for c in creds if c[0].lower() == rest[0].lstrip("@").lower()]
                if not creds:
                    raise UsageError(f"{rest[0]} is not in {a.file}")
            out = []
            for h, pw, sec in creds:
                try:
                    out.append(login.login(h, pw, sec, browser=a.browser or "chromium", headless=not g["show"]))
                except AnyxError as e:
                    out.append({"user": h, "result": "failed", "detail": str(e)})
            out = out[0] if len(out) == 1 else out
        else:
            raise UsageError("accounts list | login <h>|- --file F | add <h> | harvest --browser B [--profile P] | "
                             "import <h> <file> --browser B | set <h> ... | check <h>|all")
        _print(out, g["json"])
        return 0

    many = _accounts(g)
    if many is not None:
        return _fan_out(many, argv, g)
    handle = _account(g)
    if g["wait"]:
        return _with_wait(lambda: _one(a, g, handle), g)
    return _one(a, g, handle)


def _with_wait(fn, g, tries=4):
    """On a rate limit, sleep until X says it lifts (or 15 minutes), then try again."""
    import time
    from .errors import RateLimited
    for i in range(tries):
        try:
            return fn()
        except RateLimited as e:
            if i == tries - 1:
                raise
            wait = (e.reset - time.time() + 10) if e.reset else 15 * 60
            wait = min(max(wait, 30), 30 * 60)
            print(f"anyx: rate limited; waiting {int(wait)}s", file=sys.stderr, flush=True)
            time.sleep(wait)


def _one(a, g, handle):
    with Session(handle, headless=not g["show"], browser=g["browser"], force=g["force"]) as x:
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
        elif c == "trends":
            out = x.trends(tab=a.tab, limit=a.n)
        elif c == "bookmarks":
            out = x.bookmarks(limit=a.n)
        elif c == "likes":
            out = x.likes(a.ref, limit=a.n)
        elif c in ("followers", "following"):
            out = getattr(x, c)(a.ref, limit=a.n)
        elif c == "analytics":
            out = x.analytics(a.ref)
        elif c == "shot":
            from .session import tweet_id
            out = {"file": x.shot(a.ref, a.out or f"{tweet_id(a.ref)}.png")}
        elif c == "post":
            poll = None
            if a.poll:
                poll = {"choices": [c.strip() for c in a.poll.split("|")], "days": a.poll_days,
                        "hours": a.poll_hours, "minutes": a.poll_minutes}
            out = x.post(a.text.replace("\\n", "\n"), quote=a.quote, reply=a.reply, media=a.media, at=a.at,
                         alt=a.alt, poll=poll, who=a.who, thread=a.thread, draft=a.draft)
        elif c in POST_ACTIONS or c in USER_ACTIONS:
            out = [getattr(x, c)(r) for r in a.refs]
            out = out[0] if len(out) == 1 else out
        elif c == "settings":
            if not a.args:
                out = x.settings()
            elif a.args[0] == "set" and len(a.args) == 3 and a.args[1] == "handle":
                out = x.set_handle(a.args[2])
            elif a.args[0] == "set" and len(a.args) == 3:
                out = x.set_setting(a.args[1], a.args[2])
            else:
                raise UsageError("settings | settings set <name> <value>")
        elif c == "muted":
            out = x.mute_word(a.add) if a.add else x.unmute_word(a.remove) if a.remove else x.muted_words()
        elif c == "community":
            out = x.community_posts(a.ref, limit=a.n) if a.posts else x.community(a.ref)
        elif c in ("join", "leave"):
            out = getattr(x, c)(a.ref)
        elif c == "lists":
            out = x.lists()
        elif c == "list":
            act, r = a.action, a.args
            if act == "create" and r:
                out = x.list_create(r[0], a.description, private=not a.public)
            elif act in ("add", "remove") and len(r) == 2:
                out = getattr(x, "list_" + act)(r[0], r[1])
            elif act == "posts" and r:
                out = x.list_posts(r[0], limit=a.n)
            elif act == "members" and r:
                out = x.list_members(r[0], limit=a.n)
            elif act == "delete" and r:
                out = x.list_delete(r[0])
            else:
                raise UsageError("list create <name> | add|remove <list> <handle> | posts|members <list> | delete <list>")
        elif c == "chat":
            if a.action != "setup" or not a.pin:
                raise UsageError("chat setup --pin NNNN")
            out = x.chat_setup(a.pin)
        elif c == "chats":
            out = x.chats(limit=a.n, box=a.box)
        elif c == "messages":
            out = x.messages(a.ref, limit=a.n)
        elif c == "dm":
            out = x.dm(a.to, a.text.replace("\\n", "\n"), media=a.media)
        elif c == "scheduled":
            if a.move:
                if not a.at:
                    raise UsageError("scheduled --move <text> --at \"YYYY-MM-DD HH:MM\"")
                out = x.reschedule(a.move, a.at)
            else:
                out = x.delete_scheduled(a.delete) if a.delete else x.scheduled()
        elif c == "drafts":
            out = x.delete_drafts(a.delete) if a.delete else x.drafts()
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
