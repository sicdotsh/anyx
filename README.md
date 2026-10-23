# anyx

Use X from any browser, as any of your accounts. From the command line or from Python. No API keys.

```
pip install anyx && playwright install chromium
anyx accounts add yourhandle          # log in once in the window that opens
anyx tweet https://x.com/jack/status/20
```

anyx drives a real browser the way a person would, and reads what X's own web app reads: while a page loads, x.com fetches its content as JSON from its GraphQL endpoints, and anyx listens to those responses. You get the same counts the app shows (views, likes, reposts, replies, quotes, bookmarks), full text, media and quoted posts, without scraping the page's markup and without an API plan.

## What it does

```
read      tweet  thread  quotes  user  posts  search  home  trends  notifications
          bookmarks  likes  followers  following  analytics  shot
post      post: plain, quote, reply, thread, up to 4 images with alt text or a video,
          poll, who can reply, scheduled, draft
          delete  scheduled (list, move, delete)  drafts (list, delete)
engage    like  unlike  retweet  unretweet  bookmark  unbookmark  pin  unpin
people    follow  unfollow  mute  unmute  block  unblock
lists     lists  list create | add | remove | posts | members | delete
groups    community (info, posts)  join  leave
chat      chat setup  chats  messages  dm
account   profile  settings (read all; change 14, and the @handle)  muted (words)
accounts  add  login  import  harvest  set  check  list
```

```
anyx posts nasa -n 20
anyx search "from:nasa min_faves:1000" --tab top
anyx thread https://x.com/jack/status/20
anyx trends --tab news
anyx --as alice post "hello" --media cat.jpg --alt "a cat on a desk"
anyx --as alice post "agreed" --quote https://x.com/jack/status/20
anyx --as alice post "1/3" --thread "2/3" "3/3"
anyx --as alice post "which?" --poll "this|that" --poll-days 1
anyx --as alice post "later" --at "2026-12-01 09:00"
anyx --as bob like https://x.com/jack/status/20
anyx --as bob dm alice "hi"
anyx --as bob messages alice
anyx --as alice,bob,carol follow nasa          # each account, in parallel
anyx --as all --wait notifications -n 5        # every account; sleep through rate limits
anyx --json user jack | jq .followers
```

`--json` on any command prints JSON instead of text. `--show` opens a visible window so you can watch. `--as a,b,c` (or `all`) runs the command as each account, `-j` at a time; accounts that share one browser profile take turns. `--wait` sleeps until the moment X says a rate limit lifts, then carries on.

## Chat

X's chat is end-to-end capable, so message bodies cross the wire encoded and encrypted; anyx reads them from the page after X's own app has decrypted them. Chat asks each account for a 4-digit passcode once, and again on every new browser. Give it to anyx once and it answers X whenever asked:

```
anyx --as alice chat setup --pin 1234
```

X only delivers messages between accounts that follow each other, or to accounts that open their requests (`anyx settings set dms everyone`).

The chat keys live in the browser, one set per browser profile. When several accounts share one profile and anyx switches between them, it clears x.com's storage on the switch and enters the next account's passcode, once; a wrong passcode is reported and never retried, because X destroys the keys after too many.

## How it behaves

- Each account's browser stays open between commands (`anyx status`, `anyx stop`). The first command starts it; later ones reuse the same tab.
- Pages change inside X's app, the way clicking a link does, instead of reloading it. A reload boots X's whole app (about 30 requests); a change inside it costs 6 to 12.
- A rate limit is reported with the time X says it lifts (exit code 6); `--wait` sleeps until then and carries on.

How hard to drive an account is yours to decide. Two switches in `~/.anyx/config.json`, both off by default:

```json
{"pace": {"page": [2, 5], "write": [6, 14]}, "rest": true}
```

`pace` pauses a random number of seconds before each page change and before each write. `rest` puts an account to rest after X pushes back (a 429 until X's reset time, an unexplained refusal for an hour, a login wall for a day); anyx then refuses to act as it until then.

## Accounts

anyx logs each account in itself, in that account's own browser, and keeps the cookies in `~/.anyx/accounts/<handle>.json` together with the browser they were made in. Using them in another browser works, with a note: to X it looks like one login moving between devices, and X sometimes refuses things from it.

```
anyx accounts login - --file creds.tsv          # log every account in, in its own Chromium
anyx accounts login alice --file creds.tsv      # just one
anyx accounts add alice                         # a visible window, for when X asks for more than a password
anyx accounts harvest --browser adspower --profile work   # adopt accounts already logged into that profile
anyx accounts import alice cookies.json --browser chrome   # cookies exported from that browser
anyx accounts check all                         # ok, expired, locked, suspended, rate_limited, in search?
anyx accounts list
```

`creds.tsv` has one account per line: `handle<TAB>password<TAB>totp-secret`, the last column only for accounts with two-factor on. X asks new devices for an email code now and then; an account that stops there needs `accounts add` once.

Before anything is done, anyx checks that the browser really is the account asked for. If it is not, it stops without touching anything.

## Browsers

| browser | what it is |
|---|---|
| `chromium` | Playwright's Chromium with a profile per account under `~/.anyx/profiles/`. The default. |
| `chrome` | Your installed Google Chrome, same layout. |
| `cdp` | A browser you started with `--remote-debugging-port`. anyx attaches and leaves it running. |
| `adspower` | An AdsPower profile, started through AdsPower's Local API. Its key is stored once with `anyx config set adspower.key KEY` (or read from `ADSPOWER_API_KEY`). |

```
anyx config set adspower.key KEY
anyx accounts set bob --browser adspower --profile x-bob
anyx accounts set carol --browser cdp --cdp http://127.0.0.1:9222
anyx accounts set dave --proxy http://user:pass@host:port
```

A browser is one small class with `open()` and `close()` in `anyx/browsers/`. Adding another anti-detect browser is a matter of starting it and handing back a Playwright context.

## Python

```python
import anyx

with anyx.Session("alice") as x:
    t = x.tweet("https://x.com/jack/status/20")
    print(t["views"], t["likes"], t["text"])
    for q in x.quotes(t["id"], limit=20):
        print(q["author"]["handle"], q["text"])
    x.post("hello", quote=t["url"])
```

Every command is a method of the same name on `Session`, and returns the same dicts `--json` prints.

## When something goes wrong

anyx exits with a code that says what happened, so scripts can react:

| code | meaning |
|---|---|
| 0 | done (or already in that state: liking a liked post returns `already`) |
| 2 | bad arguments |
| 3 | the account is logged out or locked (`anyx accounts add <handle>` again) |
| 4 | the browser showed a different account; nothing was touched |
| 5 | something anyx relies on was not on the page: X changed its layout |
| 6 | rate limited by X (or resting after one, when `rest` is on) |
| 7 | the post or account does not exist or is not visible |
| 8 | X did not confirm the change, or refused it (its own reason is printed, e.g. a duplicate post) |

Writes are only reported as done after X's own request for them (FavoriteTweet, CreateTweet, friendships/create, ...) comes back.

## Things to know

- A quote is made through the quoted post's Repost > Quote menu, never by pasting its URL. Both look like quotes, but only the first appears in the quoted post's quote list.
- Scheduled posts are posted by X at the given time; your machine can be off by then. X needs them at least 5 minutes ahead. X sometimes disables scheduling for an account; anyx says so instead of posting.
- Settings switches are found by their position on X's settings pages; the live test checks those positions.
- `tests/live.py` runs every command against real X and puts everything back. Run it daily: when X changes a page, the step that breaks names what went missing.
- Every page load costs requests against the account's rate limits, like browsing by hand does. Many commands in a row on one account will hit them.
- Automating an account is your responsibility under X's rules.

## License

MIT
