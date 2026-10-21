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


def post(self, text, quote=None, reply=None, media=None, at=None):
    """Post `text`. quote/reply take a post URL or id. media: up to 4 image paths or 1 video.
    at: a datetime (or "YYYY-MM-DD HH:MM") to schedule instead of posting now.
    Returns {"id", "url"} for a post, {"scheduled": "..."} for a schedule."""
    from .session import tweet_id
    if quote and reply:
        raise UsageError("a post can quote or reply, not both")
    media = [os.path.abspath(m) for m in (media or [])]
    if len(media) > MEDIA_MAX:
        raise UsageError(f"at most {MEDIA_MAX} media")
    for m in media:
        if not os.path.exists(m):
            raise UsageError(f"no such file: {m}")
    if isinstance(at, str):
        at = datetime.strptime(at, "%Y-%m-%d %H:%M")
    if at and at < datetime.now() + timedelta(minutes=LEAD_MIN):
        raise UsageError(f"schedules must be at least {LEAD_MIN} minutes ahead")

    p = self.page()
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
            dlg.locator('[data-testid="fileInput"]').set_input_files(media)
            p.wait_for_function(
                """(n) => {
                    const ds = [...document.querySelectorAll('[role="dialog"]')]
                        .filter(d => d.querySelector('[data-testid="tweetTextarea_0"]'));
                    const d = ds[ds.length - 1];
                    return d && d.querySelectorAll('[data-testid="attachments"] img, [data-testid="attachments"] video').length >= n;
                }""", arg=1 if any(m.lower().endswith((".mp4", ".mov")) for m in media) else len(media),
                timeout=UPLOAD_TIMEOUT * 1000)
            p.wait_for_timeout(SETTLE * 1000)
        dlg.locator('[data-testid="tweetTextarea_0"]').click()
        p.keyboard.insert_text(text)
        p.wait_for_function(
            """() => {
                const ds = [...document.querySelectorAll('[role="dialog"]')]
                    .filter(d => d.querySelector('[data-testid="tweetTextarea_0"]'));
                const b = ds[ds.length - 1]?.querySelector('[data-testid="tweetButton"]');
                return b && b.getAttribute('aria-disabled') !== 'true';
            }""", timeout=UPLOAD_TIMEOUT * 1000)
        if at:
            dlg.locator('[data-testid="scheduleOption"]').click()
            p.wait_for_selector(SEL["month"], timeout=20000)
            for k, v in (("month", at.month), ("day", at.day), ("year", at.year), ("hour", at.hour), ("minute", at.minute)):
                p.select_option(SEL[k], str(v))
            p.click('[data-testid="scheduledConfirmationPrimaryAction"]')
            p.wait_for_selector(SEL["month"], state="detached", timeout=20000)
            p.wait_for_timeout(1000)
        n = len(cap.items)
        dlg.locator('[data-testid="tweetButton"]').click()
        ops = ("CreateScheduledTweet",) if at else ("CreateTweet", "CreateNoteTweet")
        body = cap.wait_for(ops, timeout=60, after=n)
        if body is None:
            raise ActionFailed("X did not confirm the post")
        if at:
            return {"scheduled": at.strftime("%Y-%m-%d %H:%M"),
                    "id": graphql.created_id(body, "create_scheduled_tweet") or None}
        tid = graphql.created_id(body, "create_tweet") or graphql.created_id(body, "notetweet_create")
        if not tid:
            raise ActionFailed(f"posted, but X's answer had no id: {str(body)[:200]}")
        return {"id": tid, "url": f"https://x.com/{self.me}/status/{tid}"}
    finally:
        cap.detach()
        p.close()


def _unsent(p):
    p.goto(SCHEDULE_URL, wait_until="domcontentloaded")
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


def delete_scheduled(self, words):
    """Delete scheduled posts. Each word must match exactly one schedule; otherwise nothing is touched."""
    p = self.page()
    try:
        items = _unsent(p)
        hits = []
        for w in words:
            n = [i for i, t in enumerate(items) if w in t]
            if len(n) != 1:
                raise UsageError(f"'{w}' matches {len(n)} scheduled posts; it must match exactly one")
            hits.append(n[0])
        if not hits:
            return {"deleted": 0}
        before = len(items)
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
        after = len(_unsent(p))
        return {"deleted": before - after}
    finally:
        p.close()
