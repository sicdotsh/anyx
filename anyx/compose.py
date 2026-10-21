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
SEL = {"month": "#SELECTOR_1", "day": "#SELECTOR_2", "year": "#SELECTOR_3", "hour": "#SELECTOR_4", "minute": "#SELECTOR_5"}
TEXT = {"edit": ("編集", "Edit"), "delete": ("削除", "Delete")}

_COMPOSE = '[role="dialog"]:has([data-testid="tweetTextarea_0"])'


def _dialog(p):
    d = p.locator(_COMPOSE).last
    d.wait_for(state="visible", timeout=30000)
    return d


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
            cap.confirm(("CreateDraftTweet",), "the draft", after=n, timeout=20)
            return {"draft": text[:60], "result": "saved"}
        if at:
            opt = dlg.locator('[data-testid="scheduleOption"]').first
            if opt.get_attribute("aria-disabled") == "true" or opt.is_disabled():
                raise ActionFailed(f"X shows the schedule button disabled for @{self.me}; nothing was posted")
            opt.click()
            p.wait_for_selector(SEL["month"], timeout=20000)
            for k, v in (("month", at.month), ("day", at.day), ("year", at.year), ("hour", at.hour), ("minute", at.minute)):
                p.select_option(SEL[k], str(v))
            p.click('[data-testid="scheduledConfirmationPrimaryAction"]')
            p.wait_for_selector(SEL["month"], state="detached", timeout=20000)
            p.wait_for_timeout(1000)
        n = len(cap.items)
        dlg.locator('[data-testid="tweetButton"]').click()
        if at:
            body = cap.wait_for(("CreateScheduledTweet",), timeout=60, after=n)
            if body is None:
                raise ActionFailed("X did not confirm the schedule")
            return {"scheduled": at.strftime("%Y-%m-%d %H:%M"), "id": graphql.created_id(body, "create_scheduled_tweet")}
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


def _unsent(p, url=SCHEDULE_URL):
    p.goto(url, wait_until="domcontentloaded")
    try:
        p.wait_for_selector('[data-testid="unsentTweet"]', timeout=15000)
    except Exception:
        return []
    p.wait_for_timeout(1500)
    return p.evaluate("""() => {
        const txt = el => { const c = el.cloneNode(true);
            c.querySelectorAll('img[alt]').forEach(i => i.replaceWith(i.alt));
            return c.innerText.replace(/\\n+/g, ' ').trim(); };
        return [...document.querySelectorAll('[data-testid="unsentTweet"]')].map(txt);
    }""")


def scheduled(self):
    """This account's scheduled posts, as the text X shows for each (time and body)."""
    p = self.page()
    try:
        return _unsent(p)
    finally:
        p.close()


def _press(p, words):
    for w in words:
        b = p.get_by_text(w, exact=True)
        if b.count():
            b.first.click(timeout=5000)
            return True
    return False


def delete_scheduled(self, words, url=SCHEDULE_URL):
    """Delete scheduled posts. Each word must match exactly one schedule; otherwise nothing is touched."""
    p = self.page()
    try:
        items = _unsent(p, url)
        hits = []
        for w in words:
            n = [i for i, t in enumerate(items) if w in t]
            if len(n) != 1:
                raise UsageError(f"'{w}' matches {len(n)} scheduled posts; it must match exactly one")
            hits.append(n[0])
        if not hits:
            return {"deleted": 0}
        before = len(items)
        self.before_write()
        if not _press(p, TEXT["edit"]):
            raise UIChanged("no Edit button on the scheduled list")
        p.wait_for_timeout(1500)
        boxes = p.locator("input[type=checkbox]")
        if boxes.count() != before:
            raise UIChanged(f"scheduled list and checkboxes disagree ({before} vs {boxes.count()})")
        for i in sorted(set(hits)):
            boxes.nth(i).check()
            p.wait_for_timeout(150)
        if not _press(p, TEXT["delete"]):
            raise UIChanged("no Delete button on the scheduled list")
        p.wait_for_timeout(1200)
        c = p.locator('[data-testid="confirmationSheetConfirm"]')
        if c.count():
            c.first.click(timeout=5000)
        else:
            _press(p, TEXT["delete"])
        time.sleep(2.5)
        after = len(_unsent(p, url))
        return {"deleted": before - after}
    finally:
        p.close()


def drafts(self):
    """This account's drafts, as the text X shows for each."""
    p = self.page()
    try:
        return _unsent(p, DRAFTS_URL)
    finally:
        p.close()


def delete_drafts(self, words):
    return delete_scheduled(self, words, url=DRAFTS_URL)


def reschedule(self, word, at):
    """Move one scheduled post (picked by a word of its text) to another time."""
    if isinstance(at, str):
        at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    if at < datetime.now() + timedelta(minutes=LEAD_MIN):
        raise UsageError(f"schedules must be at least {LEAD_MIN} minutes ahead")
    p = self.page()
    cap = graphql.Capture(p)
    try:
        items = _unsent(p)
        hit = [i for i, t in enumerate(items) if word in t]
        if len(hit) != 1:
            raise UsageError(f"'{word}' matches {len(hit)} scheduled posts; it must match exactly one")
        p.locator('[data-testid="unsentTweet"]').nth(hit[0]).click()
        dlg = _dialog(p)
        close = p.locator('[data-testid="tooltip-close-icon"]')
        if close.count():
            close.first.click()
        dlg.locator('[data-testid="scheduleOption"]').click()
        p.wait_for_selector(SEL["month"], timeout=20000)
        for k, v in (("month", at.month), ("day", at.day), ("year", at.year), ("hour", at.hour), ("minute", at.minute)):
            p.select_option(SEL[k], str(v))
        p.click('[data-testid="scheduledConfirmationPrimaryAction"]')
        p.wait_for_selector(SEL["month"], state="detached", timeout=20000)
        self.before_write()
        n = len(cap.items)
        dlg.locator('[data-testid="tweetButton"]').click()
        cap.confirm(("EditScheduledTweet", "CreateScheduledTweet"), "the new time", after=n, timeout=30)
        return {"scheduled": at.strftime("%Y-%m-%d %H:%M"), "result": "moved"}
    finally:
        cap.detach()
        p.close()
