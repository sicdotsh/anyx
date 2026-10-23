"""Posting: plain, quote, reply, with up to 4 images or a video, now or scheduled.

A quote is made from the quoted post's own Repost > Quote menu, never by pasting its URL. Both look
like a quote, but only the first one shows up in the quoted post's quote list.
"""

import os
import time
from datetime import datetime, timedelta

from . import graphql
from .errors import ActionFailed, UIChanged, UsageError

MEDIA_MAX = 4
LEAD_MIN = 5          # X does not take schedules closer than this
UPLOAD_TIMEOUT = 180
SETTLE = 8            # the post button is enabled before uploads finish; wait this long after previews show
SCHEDULE_URL = "https://x.com/compose/post/unsent/scheduled"
TEXT = {"edit": ("編集", "Edit"), "delete": ("削除", "Delete")}

_COMPOSE = '[role="dialog"]:has([data-testid="tweetTextarea_0"])'


def _dialog(p):
    d = p.locator(_COMPOSE).last
    d.wait_for(state="visible", timeout=30000)
    return d


def _set_time(p, at):
    """Fill X's date picker. Its selects are numbered per page lifetime (SELECTOR_1.. on a fresh load,
    higher after in-app navigation), so they are taken by order inside the picker:
    month, day, year, hour, minute (and AM/PM where X shows a 12-hour clock)."""
    box = p.locator('[role="dialog"]:has([data-testid="scheduledConfirmationPrimaryAction"])').last
    box.wait_for(state="visible", timeout=20000)
    sel = box.locator("select")
    sel.nth(4).wait_for(state="attached", timeout=20000)
    hour = at.hour
    if sel.count() > 5:
        sel.nth(5).select_option(label="PM" if hour >= 12 else "AM")
        hour = hour % 12 or 12
    for i, v in enumerate((at.month, at.day, at.year, hour, at.minute)):
        sel.nth(i).select_option(str(v))
    box.locator('[data-testid="scheduledConfirmationPrimaryAction"]').click()
    box.wait_for(state="detached", timeout=20000)


WHO = ("everyone", "following", "verified", "mentioned")     # order of X's "who can reply" choices


def _discard_open_composer(p):
    """A composer left open (by an earlier failure) would be reused by mistake; throw it away."""
    for _ in range(2):
        if not p.locator(_COMPOSE).count():
            return
        close = p.locator(_COMPOSE).last.locator('[data-testid="app-bar-close"]')
        if close.count():
            close.first.click()
        else:
            p.keyboard.press("Escape")
        discard = p.locator('[data-testid="confirmationSheetCancel"]')
        try:
            discard.first.wait_for(state="visible", timeout=3000)
            discard.first.click()
        except Exception:
            pass
        p.wait_for_timeout(800)


def _files(paths):
    out = [os.path.abspath(m) for m in (paths or [])]
    if len(out) > MEDIA_MAX:
        raise UsageError(f"at most {MEDIA_MAX} media per post")
    for m in out:
        if not os.path.exists(m):
            raise UsageError(f"no such file: {m}")
    return out


def _attach(p, dlg, media, alt):
    dlg.locator('[data-testid="fileInput"]').last.set_input_files(media)
    p.wait_for_function(
        """(n) => {
            const ds = [...document.querySelectorAll('[role="dialog"]')]
                .filter(d => d.querySelector('[data-testid="tweetTextarea_0"]'));
            const d = ds[ds.length - 1];
            return d && d.querySelectorAll('[data-testid="attachments"] img, [data-testid="attachments"] video').length >= n;
        }""", arg=1 if any(m.lower().endswith((".mp4", ".mov")) for m in media) else len(media),
        timeout=UPLOAD_TIMEOUT * 1000)
    p.wait_for_timeout(SETTLE * 1000)
    if alt:
        dlg.locator('[data-testid="altTextWrapper"]').first.click()
        layer = p.locator("#layers")

        def edu(wait=1500):
            # X shows an "about alt text" sheet over the editor, sometimes a moment late
            e = layer.locator('[data-testid="altTextEducationSheetPrimaryAction"]')
            try:
                e.first.wait_for(state="visible", timeout=wait)
                e.first.click()
                p.wait_for_timeout(600)
            except Exception:
                pass

        for i, text in enumerate(alt[:len(media)]):
            edu()
            box = layer.locator('[data-testid="altTextInput"]').first
            box.wait_for(state="visible", timeout=15000)
            box.fill(text or "")
            if i < min(len(alt), len(media)) - 1:
                nxt = layer.locator('[role=button][aria-label="次の画像"], [role=button][aria-label="Next image"]')
                if not nxt.count():
                    raise UIChanged("no next-image button in the media editor")
                nxt.first.click()
                p.wait_for_timeout(800)
        edu(800)
        layer.locator('[data-testid="endEditingButton"]').first.click()
        p.wait_for_timeout(1200)


def _who(p, dlg, who):
    if who not in WHO:
        raise UsageError(f"who can reply: {', '.join(WHO)}")
    btn = dlg.locator("[role=button]").filter(has_text="返信できます")
    if not btn.count():
        btn = dlg.locator("[role=button]").filter(has_text="can reply")
    if not btn.count():
        raise UIChanged("no reply-settings button in the composer")
    btn.first.click()
    group = p.locator('[data-testid="HoverCard"] [role="radiogroup"]').first
    group.wait_for(state="visible", timeout=10000)
    group.locator(":scope > *").nth(WHO.index(who)).click()
    p.wait_for_timeout(800)
    card = p.locator('[data-testid="HoverCard"]')
    if card.count() and card.first.is_visible():
        p.keyboard.press("Escape")          # the choice is kept; the card would cover the Post button
        p.wait_for_timeout(500)
    if not p.locator(_COMPOSE).count():
        raise UIChanged("closing the reply-settings card closed the composer")


def _poll(p, dlg, poll):
    choices = poll.get("choices") or []
    if not 2 <= len(choices) <= 4:
        raise UsageError("a poll has 2 to 4 choices")
    dlg.locator('[data-testid="createPollButton"]').first.click()
    p.wait_for_timeout(800)
    for i, c in enumerate(choices, 1):
        if i > 2:
            dlg.locator('[data-testid="addPollChoice"]').first.click()
            p.wait_for_timeout(300)
        dlg.locator(f'input[name="Choice{i}"]').first.fill(c)
    for key, sel in (("days", "selectPollDays"), ("hours", "selectPollHours"), ("minutes", "selectPollMinutes")):
        if poll.get(key) is not None:
            dlg.locator(f'[data-testid="{sel}"]').first.select_option(str(poll[key]))


def post(self, text, quote=None, reply=None, media=None, at=None, alt=None, poll=None, who=None,
         thread=None, draft=False):
    """Post `text`.

    quote / reply   a post URL or id to quote or answer
    media           up to 4 image paths or 1 video;  alt: one description per image
    poll            {"choices": [...2-4], "days": 1, "hours": 0, "minutes": 0}
    who             who can reply: everyone, following, verified, mentioned
    thread          more texts, posted as a thread under this one
    at              a datetime (or "YYYY-MM-DD HH:MM") to schedule instead of posting now
    draft           save to drafts instead of posting
    Returns {"id", "url"} ({"ids"} too for a thread), {"scheduled"} for a schedule, {"draft"} for a draft."""
    from .session import tweet_id
    if quote and reply:
        raise UsageError("a post can quote or reply, not both")
    if poll and media:
        raise UsageError("a post carries a poll or media, not both")
    media = _files(media)
    if isinstance(at, str):
        at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    if at and at < datetime.now() + timedelta(minutes=LEAD_MIN):
        raise UsageError(f"schedules must be at least {LEAD_MIN} minutes ahead")
    if (at or draft) and thread:
        raise UsageError("threads are posted now (X does not schedule or draft them)")

    p = self.page()
    _discard_open_composer(p)
    cap = graphql.Capture(p)
    try:
        if quote or reply:
            tid = tweet_id(quote or reply)
            self.goto(p, f"https://x.com/i/status/{tid}")
            art = self.article(p, tid)
            if quote:
                art.locator('[data-testid="retweet"]').first.click()
                m = p.locator('[role="menu"]').first
                m.wait_for(state="visible", timeout=15000)
                item = m.locator('[role="menuitem"][href="/compose/post"]')
                if not item.count():
                    raise UIChanged("no Quote entry in the repost menu")
                item.first.click()
            else:
                art.locator('[data-testid="reply"]').first.click()
        else:
            self.goto(p, "https://x.com/compose/post")
        dlg = _dialog(p)
        if quote:
            try:
                dlg.locator('[data-testid="attachments"]').first.wait_for(state="visible", timeout=20000)
            except Exception:
                raise ActionFailed("the quoted post did not attach to the composer; nothing was posted")
        if media:
            _attach(p, dlg, media, alt)
        dlg.locator('[data-testid="tweetTextarea_0"]').click()
        p.keyboard.insert_text(text)
        if poll:
            _poll(p, dlg, poll)
        if who and who != "everyone" and not reply:
            _who(p, dlg, who)
        for i, more in enumerate(thread or [], 1):
            dlg.locator('[data-testid="addButton"]').first.click()
            box = dlg.locator(f'[data-testid="tweetTextarea_{i}"]').first
            box.wait_for(state="visible", timeout=10000)
            box.click()
            p.keyboard.insert_text(more)
        p.wait_for_function(
            """() => {
                const ds = [...document.querySelectorAll('[role="dialog"]')]
                    .filter(d => d.querySelector('[data-testid="tweetTextarea_0"]'));
                const b = ds[ds.length - 1]?.querySelector('[data-testid="tweetButton"]');
                return b && b.getAttribute('aria-disabled') !== 'true';
            }""", timeout=UPLOAD_TIMEOUT * 1000)
        self.before_write()
        if draft:
            n = len(cap.items)
            dlg.locator('[data-testid="app-bar-close"]').first.click()
            c = p.locator('[data-testid="confirmationSheetConfirm"]').first
            c.wait_for(state="visible", timeout=15000)
            c.click()
            b = cap.confirm(("CreateDraftTweet",), "the draft", after=n, timeout=20)
            return {"id": graphql.created_id(b, "create_draft_tweet") or graphql.created_id(b, "draft_tweet"),
                    "text": text[:60], "result": "saved"}
        if at:
            opt = dlg.locator('[data-testid="scheduleOption"]').first
            if opt.get_attribute("aria-disabled") == "true" or opt.is_disabled():
                raise ActionFailed(f"X shows the schedule button disabled for @{self.me}; nothing was posted")
            opt.click()
            _set_time(p, at)
            p.wait_for_timeout(1000)
        n = len(cap.items)
        dlg.locator('[data-testid="tweetButton"]').click()
        if at:
            body = cap.confirm(("CreateScheduledTweet",), "the schedule", after=n, timeout=60)
            sid = graphql.created_id(body, "create_scheduled_tweet")
            if not sid:
                # X answers a refused schedule (a daily limit, ...) with errors next to an empty result
                why = "; ".join(e.get("message", "") for e in body.get("errors") or []) or "no id came back"
                raise ActionFailed(f"X did not schedule the post: {why}")
            return {"id": sid, "at": at.astimezone().isoformat(timespec="minutes"), "result": "scheduled"}
        want = 1 + len(thread or [])
        refused = None
        ids = []
        end = time.monotonic() + 60 + 10 * want
        while time.monotonic() < end and len(ids) < want:
            done = [b for op, b in cap.items[n:] if op in ("CreateTweet", "CreateNoteTweet")]
            ids = [graphql.created_id(b, "create_tweet") or graphql.created_id(b, "notetweet_create") for b in done]
            refused = next((e for b in done for e in (b.get("errors") or [])), None)
            if refused:
                break
            p.wait_for_timeout(300)
        ids = [i for i in ids if i]
        if refused and not ids:
            raise ActionFailed(f"X refused the post: {refused.get('message')}")
        if not ids:
            raise ActionFailed("X did not confirm the post")
        out = {"id": ids[0], "url": f"https://x.com/{self.me}/status/{ids[0]}"}
        if thread:
            out["ids"] = ids
            if len(ids) < want:
                raise ActionFailed(f"only {len(ids)} of {want} posts of the thread were confirmed: {ids}")
        return out
    finally:
        cap.detach()
        p.close()


DRAFTS_URL = "https://x.com/compose/post/unsent/drafts"
UNSENT = {"scheduled": (SCHEDULE_URL, "FetchScheduledTweets"), "drafts": (DRAFTS_URL, "FetchDraftTweets")}


def _local(ms):
    return datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="minutes") if ms else None


def _unsent_items(body, kind):
    v = ((body or {}).get("data") or {}).get("viewer") or {}
    rows = v.get("scheduled_tweet_list") if kind == "scheduled" else (v.get("draft_list") or {}).get("response_data")
    out = []
    for r in rows or []:
        req = r.get("tweet_create_request") or {}
        item = {"id": r.get("rest_id"), "text": req.get("status"),
                "quote": req.get("attachment_url"), "reply_to": req.get("in_reply_to_status_id"),
                "media": len(req.get("media_ids") or []), "thread": len(req.get("thread_tweets") or [])}
        if kind == "scheduled":
            item = {"id": item["id"], "at": _local((r.get("scheduling_info") or {}).get("execute_at")),
                    **{k: v for k, v in item.items() if k != "id"}}
        out.append(item)
    return out


def _open_unsent(self, p, cap, kind):
    url, op = UNSENT[kind]
    n = len(cap.items)
    self.goto(p, url)
    b = cap.wait_for((op,), timeout=30, after=n)
    if b is None:
        raise UIChanged(f"the {kind} list did not load")
    return _unsent_items(b, kind)


def scheduled(self):
    """This account's scheduled posts: id, at (local time), text, quote, reply_to, media, thread."""
    p = self.page()
    cap = graphql.Capture(p)
    try:
        return _open_unsent(self, p, cap, "scheduled")
    finally:
        cap.detach()


def drafts(self):
    """This account's drafts: id, text, quote, reply_to, media, thread."""
    p = self.page()
    cap = graphql.Capture(p)
    try:
        return _open_unsent(self, p, cap, "drafts")
    finally:
        cap.detach()


def _press(p, words):
    for w in words:
        b = p.get_by_text(w, exact=True)
        if b.count():
            b.first.click(timeout=5000)
            return True
    return False


def _pick(items, refs):
    """Each ref is an id or a piece of text that matches exactly one item. Returns the indexes."""
    hits = []
    for r in refs:
        r = str(r)
        n = [i for i, it in enumerate(items) if it["id"] == r] or \
            [i for i, it in enumerate(items) if r in (it.get("text") or "")]
        if len(n) != 1:
            seen = "".join(f"\n  {items[i]['id']}  {(items[i].get('text') or '')[:60]}" for i in n)
            raise UsageError(f"'{r}' matches {len(n)} items; give an id or text that matches exactly one{seen}")
        hits.append(n[0])
    return sorted(set(hits))


def _delete_unsent(self, refs, kind):
    p = self.page()
    cap = graphql.Capture(p)
    try:
        items = _open_unsent(self, p, cap, kind)
        hits = _pick(items, refs)
        rows = p.locator('[data-testid="unsentTweet"]')
        rows.first.wait_for(state="visible", timeout=15000)
        if not _press(p, TEXT["edit"]):
            raise UIChanged(f"no Edit button on the {kind} list")
        boxes = p.locator("input[type=checkbox]")
        boxes.first.wait_for(state="attached", timeout=10000)
        if boxes.count() != len(items):
            raise UIChanged(f"{kind} list and checkboxes disagree ({len(items)} vs {boxes.count()})")
        for i in hits:
            boxes.nth(i).check()
        self.before_write()
        if not _press(p, TEXT["delete"]):
            raise UIChanged(f"no Delete button on the {kind} list")
        n = len(cap.items)
        c = p.locator('[data-testid="confirmationSheetConfirm"]').first
        c.wait_for(state="visible", timeout=10000)
        c.click()
        op = "DeleteScheduledTweet" if kind == "scheduled" else "DeleteDraftTweet"
        end = time.monotonic() + 20
        while time.monotonic() < end:
            done = [b for o, b in cap.items[n:] if o.startswith(op)]      # DeleteDraftTweets deletes several
            if done and (len(done) >= len(hits) or op == "DeleteDraftTweet"):
                bad = next((cap.refusal(b) for b in done if cap.refusal(b)), None)
                if bad:
                    raise ActionFailed(f"X refused {op}: {bad}")
                return {"deleted": len(hits), "ids": [items[i]["id"] for i in hits]}
            p.wait_for_timeout(200)
        seen = sorted(set(o for o, _ in cap.items[n:]))
        raise ActionFailed(f"X did not confirm {op} (saw {seen})")
    finally:
        cap.detach()


def delete_scheduled(self, refs):
    """Delete scheduled posts, each picked by id or by text matching exactly one."""
    return _delete_unsent(self, refs, "scheduled")


def delete_drafts(self, refs):
    """Delete drafts, each picked by id or by text matching exactly one."""
    return _delete_unsent(self, refs, "drafts")


def reschedule(self, ref, at):
    """Move one scheduled post (by id or text) to another time (local, "YYYY-MM-DD HH:MM")."""
    if isinstance(at, str):
        at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    if at < datetime.now() + timedelta(minutes=LEAD_MIN):
        raise UsageError(f"schedules must be at least {LEAD_MIN} minutes ahead")
    p = self.page()
    cap = graphql.Capture(p)
    try:
        items = _open_unsent(self, p, cap, "scheduled")
        i = _pick(items, [ref])[0]
        rows = p.locator('[data-testid="unsentTweet"]')
        rows.first.wait_for(state="visible", timeout=15000)
        rows.nth(i).click()
        dlg = _dialog(p)
        close = p.locator('[data-testid="tooltip-close-icon"]')
        if close.count():
            close.first.click()
        dlg.locator('[data-testid="scheduleOption"]').click()
        _set_time(p, at)
        self.before_write()
        n = len(cap.items)
        dlg.locator('[data-testid="tweetButton"]').click()
        cap.confirm(("EditScheduledTweet", "CreateScheduledTweet"), "the new time", after=n, timeout=30)
        return {"id": items[i]["id"], "at": at.astimezone().isoformat(timespec="minutes"), "result": "moved"}
    finally:
        cap.detach()
