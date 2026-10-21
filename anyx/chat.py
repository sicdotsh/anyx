"""Chat (direct messages).

X's chat is end-to-end capable: message bodies travel as encoded, encrypted events, so anyx reads
messages from the page after X's own app has decrypted them, not from the network.

Chat asks each account for a 4-digit passcode once (to set up its keys), and again on every new
browser (to recover them). anyx keeps the passcode next to the account's cookies and enters it
whenever X asks. Set it with `anyx chat setup --pin 1234`.
"""

import os
import re

from . import graphql, vault
from .errors import ActionFailed, NotFound, UIChanged, UsageError

CHAT = "https://x.com/i/chat"


def _pin_path(handle):
    return vault.path(handle)[:-5] + ".pin"


def _pin(handle):
    p = _pin_path(handle)
    return open(p).read().strip() if os.path.exists(p) else None


def _save_pin(handle, pin):
    p = _pin_path(handle)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(pin)
    os.chmod(p, 0o600)


def _type_pin(p, pin):
    p.locator('[data-testid="pin-code-input-container"]').first.wait_for(state="visible", timeout=20000)
    p.locator('[data-testid="pin-code-input-container"] input, [data-testid="pin-code-input-container"]').first.click()
    p.keyboard.type(pin, delay=120)
    p.wait_for_timeout(1500)


def _open(self, p, url=CHAT):
    """Open a chat page, getting through the passcode screens if X shows them."""
    self.goto(p, url)
    for _ in range(3):
        p.wait_for_timeout(2500)
        if "/i/chat/pin" not in p.url:
            break
        pin = _pin(self.handle)
        if not pin:
            raise UsageError(f"chat wants a passcode for @{self.handle} (anyx --as {self.handle} chat setup --pin NNNN)")
        if p.locator('[data-testid="pin-onboarding-setup-now"]').count():
            raise UsageError(f"chat is not set up for @{self.handle} yet (anyx --as {self.handle} chat setup --pin NNNN)")
        _type_pin(p, pin)
        p.wait_for_timeout(2500)
        if "/i/chat/pin" in p.url and url != CHAT:
            self.goto(p, url)
    if "/i/chat/pin" in p.url:
        raise ActionFailed("chat did not accept the passcode")
    p.locator('[data-testid="dm-container"]').first.wait_for(state="attached", timeout=30000)
    return p


def chat_setup(self, pin):
    """Set this account's chat passcode (first time), or enter it on this browser (later times)."""
    if not re.fullmatch(r"\d{4}", pin or ""):
        raise UsageError("the passcode is 4 digits")
    p = self.page()
    try:
        self.goto(p, CHAT)
        p.wait_for_timeout(3000)
        if "/i/chat/pin" not in p.url:
            _save_pin(self.handle, pin)
            return {"user": self.handle, "result": "already"}
        if p.locator('[data-testid="pin-onboarding-setup-now"]').count():
            p.locator('[data-testid="pin-onboarding-setup-now"]').click()
            _type_pin(p, pin)              # choose
            _type_pin(p, pin)              # confirm
        else:
            _type_pin(p, pin)              # recover on this browser
        p.wait_for_timeout(3000)
        if "/i/chat/pin" in p.url:
            raise ActionFailed("X did not accept the passcode")
        _save_pin(self.handle, pin)
        return {"user": self.handle, "result": "done"}
    finally:
        p.close()


def chats(self, limit=30, box="all"):
    """The chat inbox: one row per conversation, newest first. box: all, unread, direct, groups, requests."""
    p = self.page()
    try:
        _open(self, p)
        if box != "all":
            ids = {"unread": "unread", "direct": "oneonone", "groups": "groups", "requests": "requests"}
            if box not in ids:
                raise UsageError("box is all, unread, direct, groups or requests")
            p.locator('[data-testid="dm-inbox-dropdown-trigger"]').first.click()
            p.locator(f'[data-testid="dm-inbox-dropdown-{ids[box]}"]').first.click()
            p.wait_for_timeout(2500)
        rows = p.locator('[data-testid^="dm-conversation-item-"]')
        out = []
        for r in rows.all()[:limit]:
            tid = r.get_attribute("data-testid")
            cid = tid.split("dm-conversation-item-", 1)[1]
            lines = [x.strip() for x in r.inner_text().split("\n") if x.strip()]
            href = r.locator('a[href^="/i/chat/"]').first
            link = href.get_attribute("href") if href.count() else "/i/chat/" + cid.replace(":", "-")
            out.append({"id": link.rsplit("/", 1)[1], "name": lines[0] if lines else None,
                        "when": lines[1] if len(lines) > 1 else None,
                        "last": " ".join(lines[2:]) if len(lines) > 2 else None,
                        "unread": bool(r.locator('[data-testid*="unread"]').count())})
        return out
    finally:
        p.close()


def _conversation_url(self, ref):
    s = str(ref)
    if re.fullmatch(r"[\dg]+[-:][\d]+|g\d+|\d+", s) and ("-" in s or ":" in s or s.startswith("g")):
        return f"{CHAT}/{s.replace(':', '-')}"
    if "/i/chat/" in s:
        return s
    # a handle: the one-to-one conversation id is the two user ids in ascending order
    from .session import handle_of
    other = self.user(handle_of(s))["id"]
    mine = vault.user_id(vault.load(self.handle) or []) or self.user(self.me)["id"]
    a, b = sorted([mine, other], key=int)
    return f"{CHAT}/{a}-{b}"


READ_JS = """rows => rows.map(r => {
    const id = r.getAttribute('data-dm-message-row');
    const body = r.querySelector('[data-testid^="message-text-"] span[dir="auto"]');
    const sender = r.querySelector('[id$="-sender"]');
    const time = r.querySelector('[id$="-time"]');
    const imgs = [...r.querySelectorAll('img')].map(i => i.src).filter(s => s.includes('ton.x.com') || s.includes('pbs.twimg.com/dm'));
    return {id, text: body ? body.innerText : null, sender: sender ? sender.textContent : null,
            time: time ? time.textContent : null, mine: r.className.includes('justify-end'),
            status: r.getAttribute('data-send-status'), media: imgs};
})"""


def messages(self, ref, limit=50):
    """Messages of one conversation, oldest first. ref: a conversation id or the other person's handle."""
    url = _conversation_url(self, ref)
    p = self.page()
    try:
        _open(self, p, url)
        p.wait_for_timeout(2500)
        if not p.locator('[data-testid="dm-message-list"]').count():
            raise NotFound(f"no conversation at {url}")
        seen = -1
        for _ in range(15):
            n = p.locator("[data-dm-message-row]").count()
            if n >= limit or n == seen:
                break
            seen = n
            p.locator('[data-testid="dm-message-scroller"]').first.evaluate("e => e.scrollTop = 0")
            p.wait_for_timeout(1500)
        rows = p.locator("[data-dm-message-row]").evaluate_all(READ_JS)
        return rows[-limit:]
    finally:
        p.close()


def dm(self, to, text=None, media=None):
    """Send a message to one person (handle) or into a conversation (id). Waits for X to accept it."""
    if not text and not media:
        raise UsageError("nothing to send")
    url = _conversation_url(self, to)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        _open(self, p, url)
        box = p.locator('[data-testid="dm-composer-textarea"]').first
        try:
            box.wait_for(state="visible", timeout=15000)
        except Exception:
            if p.get_by_text("受信トレイは閉じられて").count() or p.get_by_text("inbox is closed", exact=False).count():
                raise ActionFailed(f"@{to}'s inbox is closed to this account (they need to follow it, or open DMs)")
            raise UIChanged("no message box in the conversation")
        if media:
            files = [os.path.abspath(m) for m in media]
            for f in files:
                if not os.path.exists(f):
                    raise UsageError(f"no such file: {f}")
            p.locator('[data-testid="dm-composer-file-input"]').first.set_input_files(files)
            p.wait_for_timeout(3000)
        if text:
            box.fill(text)
        self.before_write()
        n = len(cap.items)
        p.locator('[data-testid="dm-composer-send-button"]').first.click()
        cap.confirm(("SendMessageCreateMutation",), "the message", after=n, timeout=30)
        return {"to": str(to), "conversation": p.url.rsplit("/", 1)[1], "result": "sent"}
    finally:
        cap.detach()
        p.close()
