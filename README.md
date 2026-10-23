# anyx

Use X from any browser, as any of your accounts. From the command line or from Python. No API keys.

```
pip install anyx && playwright install chromium
anyx accounts add yourhandle          # log in once in the window that opens
anyx tweet https://x.com/jack/status/20
```

anyx drives a real browser and reads the same data X's web app loads, so you get what the app shows: views, likes, reposts, replies, quotes, bookmarks, full text, media and quoted posts.

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
anyx --as alice scheduled
anyx --as bob like https://x.com/jack/status/20
anyx --as bob dm alice "hi"
anyx --as alice,bob,carol follow nasa          # each account, in parallel
anyx --as all --wait notifications -n 5        # every account; sleep through rate limits
anyx --json user jack | jq .followers
```

`anyx <command> --help` shows how to call one command. `--json` prints JSON instead of text. `--show` opens a visible window. `--as a,b,c` (or `all`) runs the command as each account, `-j` at a time. `--wait` sleeps until X lifts a rate limit, then carries on. Times are local, in `--at` and in what anyx prints.

Each account's browser stays open between commands; `anyx status` lists them and `anyx stop` closes them.

Scheduled posts are sent by X itself, so your machine can be off by then. X takes them at least 5 minutes ahead.

## Accounts

```
anyx accounts login - --file creds.tsv          # log every account in
anyx accounts login alice --file creds.tsv      # just one
anyx accounts add alice                         # log in by hand, when X asks for more than a password
anyx accounts harvest --browser adspower --profile work   # adopt accounts already logged into that profile
anyx accounts import alice cookies.json --browser chrome   # cookies exported from that browser
anyx accounts check all                         # ok, expired, locked, suspended, rate_limited, in search?
anyx accounts list
```

`creds.tsv` has one account per line: `handle<TAB>password<TAB>totp-secret`, the last column only for accounts with two-factor on. Cookies used in a browser other than the one they were made in work, but X sometimes refuses things from them.

## Browsers

| browser | what it is |
|---|---|
| `chromium` | Playwright's Chromium, one profile per account. The default. |
| `chrome` | Your installed Google Chrome. |
| `cdp` | A browser you started with `--remote-debugging-port`. |
| `adspower` | An AdsPower profile. |

```
anyx config set adspower.key KEY
anyx accounts set bob --browser adspower --profile x-bob
anyx accounts set carol --browser cdp --cdp http://127.0.0.1:9222
anyx accounts set dave --proxy http://user:pass@host:port
```

A browser can be one you use yourself, with accounts already logged in. anyx works in a tab of its own and leaves yours alone. When it switches the browser to another account it does it the way X's account switcher does, so every account stays logged in and you can still switch by hand. While you are using the browser (it shows an account anyx did not put there, or you have x.com open in another tab), anyx stops instead of switching; `--force` switches anyway. AdsPower profiles are started with their window; `anyx config set adspower.headless true` starts them without.

## Chat

```
anyx --as alice chat setup --pin 1234
```

X asks each account for its 4-digit chat passcode on every new browser; anyx enters the one you gave it. If X says it is wrong, anyx stops instead of trying again, since X destroys the chat keys after too many wrong tries. Messages only go between accounts that follow each other, or to accounts that accept requests (`anyx settings set dms everyone`).

## Pace

Off by default:

```
anyx config set pace.page [2,5]     # pause 2-5 s before each page change
anyx config set pace.write [6,14]   # and 6-14 s before each write
anyx config set rest true           # stop using an account for a while after X pushes back
```

## Python

```python
import anyx

with anyx.Session("alice") as x:
    t = x.tweet("https://x.com/jack/status/20")
    print(t["views"], t["likes"], t["text"])
    x.post("hello", quote=t["url"])
```

Every command is a method of the same name on `Session`, and returns what `--json` prints.

## Exit codes

| code | meaning |
|---|---|
| 0 | done (or already so: liking a liked post returns `already`) |
| 2 | bad arguments |
| 3 | the account is logged out or locked |
| 4 | the browser showed a different account; nothing was done |
| 5 | X changed its page |
| 6 | rate limited |
| 7 | the post or account does not exist or is not visible |
| 8 | X did not confirm the change, or refused it (its reason is printed) |

Tested on macOS, with X shown in Japanese and in English.

## License

MIT
