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
    """Enter the 4 digits, one per box (X shows four single-digit inputs)."""
    box = p.locator('[data-testid="pin-code-input-container"]').first
    box.wait_for(state="visible", timeout=20000)
    inputs = box.locator("input")
    if inputs.count() >= len(pin):
        for i, d in enumerate(pin):
            inputs.nth(i).fill(d)
            p.wait_for_timeout(150)
    else:
        box.click()
        p.keyboard.type(pin, delay=150)


WRONG_PIN = ("正しくありません", "間違", "Incorrect", "incorrect", "Wrong", "wrong")


def _await_pin_result(p, timeout=30):
    """After the digits: wait until X leaves the passcode screen, or says the passcode is wrong.
    Never type again on our own: X destroys the chat keys after too many wrong passcodes."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if "/i/chat/pin" not in p.url:
            return True
        try:
            txt = p.locator('[data-testid="xchatEmbedRoute"]').first.inner_text(timeout=1000)
        except Exception:
            txt = ""
        if any(w in txt for w in WRONG_PIN):
            return False
        p.wait_for_timeout(500)
    return None


def _open(self, p, url=CHAT):
    """Open a chat page, getting through the passcode screen if X shows it. Waits until either the
    passcode screen or the chat itself is on screen before deciding (X redirects a moment after load)."""
    import time
    self.goto(p, url)
    pin_box = p.locator('[data-testid="pin-code-input-container"]')
    setup = p.locator('[data-testid="pin-onboarding-setup-now"]')
    ready = p.locator('[data-testid="dm-inbox-panel"], [data-testid="dm-conversation-panel"], [data-testid="dm-settings-root"]')
    tried = 0
    end = time.monotonic() + 45
    while time.monotonic() < end:
        if setup.count() and setup.first.is_visible():
            raise UsageError(f"chat is not set up for @{self.handle} yet (anyx --as {self.handle} chat setup --pin NNNN)")
        if pin_box.count() and pin_box.first.is_visible():
            pin = _pin(self.handle)
            if not pin:
                raise UsageError(f"chat wants the passcode for @{self.handle} (anyx --as {self.handle} chat setup --pin NNNN)")
            if tried:
                raise ActionFailed("chat is still asking for the passcode; anyx does not try again on its own")
            _type_pin(p, pin)
            tried += 1
            ok = _await_pin_result(p)
            if ok is False:
                raise ActionFailed("X says the stored chat passcode is wrong; anyx did not try again "
                                   f"(anyx --as {self.handle} chat setup --pin NNNN to store the right one)")
            if ok is None:
                raise ActionFailed("X did not answer the passcode in 30 s; anyx did not try again")
            if url != CHAT and not p.url.startswith(url):
                self.goto(p, url)
            continue
        if ready.count() and ready.first.is_visible() and "/i/chat/pin" not in p.url:
            # right after the passcode X is still restoring the keys and the conversations
            p.wait_for_timeout(4000 if tried else 800)
            return p
        p.wait_for_timeout(400)
    raise UIChanged(f"chat did not open ({p.url})")


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
            p.wait_for_timeout(1500)
            _type_pin(p, pin)              # confirm
        else:
            _type_pin(p, pin)              # recover on this browser
        if _await_pin_result(p) is not True:
            raise ActionFailed("X did not accept the passcode; anyx did not try again")
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
        empty = p.locator('[data-testid="dm-empty-inbox"]')
        for _ in range(40):                      # the list loads after the chat opens
            if rows.count() or empty.count():
                break
            p.wait_for_timeout(250)
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
        lst = p.locator('[data-testid="dm-message-list"]')
        for _ in range(40):
            if lst.count() and p.locator("[data-dm-message-row]").count():
                break
            p.wait_for_timeout(250)
        if not lst.count():
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
            box.wait_for(state="visible", timeout=30000)
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
