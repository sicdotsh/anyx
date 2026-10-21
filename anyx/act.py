"""Acting on posts and accounts. Each action clicks what a person would click, then waits for X's
own request (FavoriteTweet, friendships/create, ...) to come back before it reports success. If the
state is already what was asked (liking a post that is liked), it returns "already" and clicks nothing.
"""

from . import graphql
from .errors import ActionFailed, UIChanged

# Menu entries X gives no data-testid. Matched by their text in the languages anyx knows.
TEXT = {
    "delete": ("削除", "Delete", "Eliminar", "Supprimer", "Löschen", "삭제", "删除"),
}


def _post_action(self, ref, button, confirm=None, wait=None, done_button=None):
    from .session import tweet_id
    tid = tweet_id(ref)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/i/status/{tid}")
        art = self.article(p, tid)
        if done_button and art.locator(f'[data-testid="{done_button}"]').count():
            return {"id": tid, "result": "already"}
        b = art.locator(f'[data-testid="{button}"]').first
        if not b.count():
            raise UIChanged(f'no [data-testid="{button}"] on post {tid}')
        self.before_write()
        n = len(cap.items)
        b.click()
        if confirm:
            c = p.locator(f'[data-testid="{confirm}"]').first
            c.wait_for(state="visible", timeout=15000)
            c.click()
        if wait:
            b = cap.wait_for((wait,), timeout=20, after=n)
            if b is None:
                raise ActionFailed(f"X did not confirm {wait} for {tid}")
            if cap.refusal(b):
                raise ActionFailed(f"X refused {wait} for {tid}: {cap.refusal(b)}")
        return {"id": tid, "result": "done"}
    finally:
        cap.detach()
        p.close()


def like(self, ref):
    return _post_action(self, ref, "like", wait="FavoriteTweet", done_button="unlike")


def unlike(self, ref):
    return _post_action(self, ref, "unlike", wait="UnfavoriteTweet", done_button="like")


def retweet(self, ref):
    return _post_action(self, ref, "retweet", confirm="retweetConfirm", wait="CreateRetweet", done_button="unretweet")


def unretweet(self, ref):
    return _post_action(self, ref, "unretweet", confirm="unretweetConfirm", wait="DeleteRetweet", done_button="retweet")


def bookmark(self, ref):
    return _post_action(self, ref, "bookmark", wait="CreateBookmark", done_button="removeBookmark")


def unbookmark(self, ref):
    return _post_action(self, ref, "removeBookmark", wait="DeleteBookmark", done_button="bookmark")


def _caret_menu(self, p, art):
    for _ in range(3):
        if p.locator('#layers [data-testid="mask"]').count() == 0:
            break
        p.keyboard.press("Escape")
        p.wait_for_timeout(600)
    art.locator('[data-testid="caret"]').first.click()
    menu = p.locator('[role="menu"]').first
    menu.wait_for(state="visible", timeout=15000)
    return menu


def delete(self, ref):
    """Delete one of this account's own posts."""
    from .session import tweet_id
    tid = tweet_id(ref)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/i/status/{tid}")
        art = self.article(p, tid)
        menu = _caret_menu(self, p, art)
        item = menu.locator('[role="menuitem"]').filter(has_text=TEXT["delete"][0])
        for word in TEXT["delete"][1:]:
            if item.count():
                break
            item = menu.locator('[role="menuitem"]').filter(has_text=word)
        if not item.count():
            p.keyboard.press("Escape")
            raise UIChanged(f"no delete entry in the menu of {tid} (is it this account's post?)")
        self.before_write()
        n = len(cap.items)
        item.first.click()
        c = p.locator('[data-testid="confirmationSheetConfirm"]').first
        c.wait_for(state="visible", timeout=15000)
        c.click()
        b = cap.wait_for(("DeleteTweet",), timeout=20, after=n)
        if b is None:
            raise ActionFailed(f"X did not confirm DeleteTweet for {tid}")
        if cap.refusal(b):
            raise ActionFailed(f"X refused to delete {tid}: {cap.refusal(b)}")
        return {"id": tid, "result": "deleted"}
    finally:
        cap.detach()
        p.close()


def _pin(self, ref, testid, op):
    from .session import tweet_id
    tid = tweet_id(ref)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/i/status/{tid}")
        art = self.article(p, tid)
        menu = _caret_menu(self, p, art)
        item = menu.locator(f'[data-testid="{testid}"]')
        if not item.count():
            p.keyboard.press("Escape")
            other = "unpin" if testid == "pin" else "pin"
            if menu.locator(f'[data-testid="{other}"]').count():
                return {"id": tid, "result": "already"}
            raise UIChanged(f'no [data-testid="{testid}"] in the menu of {tid}')
        self.before_write()
        n = len(cap.items)
        item.first.click()
        c = p.locator('[data-testid="confirmationSheetConfirm"]').first
        try:
            c.wait_for(state="visible", timeout=8000)
            c.click()
        except Exception:
            pass
        cap.confirm((op,), f"{op} for {tid}", after=n, timeout=20)
        return {"id": tid, "result": "done"}
    finally:
        cap.detach()
        p.close()


def pin(self, ref):
    return _pin(self, ref, "pin", "PinTweet")


def unpin(self, ref):
    return _pin(self, ref, "unpin", "UnpinTweet")


def _profile(self, ref):
    """Open a profile page and return (page, capture, user id)."""
    from .session import handle_of
    h = handle_of(ref)
    p = self.page()
    cap = graphql.Capture(p)
    self.goto(p, f"https://x.com/{h}")
    b = cap.wait_for(("UserByScreenName",), timeout=30)
    _, us = graphql.walk([b] if b else [])
    u = next((x for x in us if (x["handle"] or "").lower() == h.lower()), None)
    if not u:
        cap.detach()
        p.close()
        from .errors import NotFound
        raise NotFound(f"@{h} not found")
    p.locator('[data-testid="primaryColumn"]').wait_for(state="visible", timeout=20000)
    p.wait_for_timeout(1500)
    return p, cap, u


def _account_action(self, ref, button, rest, confirm=False, state=None, want=True, menu=False, alt=None):
    """Click an account-level button. `state` names the user flag (i_follow, i_mute, i_block) that tells
    whether the change is already in place; X's own JSON says so, so no button text is read."""
    p, cap, u = _profile(self, ref)
    try:
        if state and bool(u.get(state)) == want:
            return {"user": u["handle"], "result": "already"}
        col = p.locator('[data-testid="primaryColumn"]')
        bt = button.format(id=u["id"])
        target = None
        if alt and col.locator(f'[data-testid="{alt}"]').count():
            target = col.locator(f'[data-testid="{alt}"]')
        elif menu:
            col.locator('[data-testid="userActions"]').first.click()
            m = p.locator('[role="menu"]').first
            m.wait_for(state="visible", timeout=15000)
            target = m.locator(f'[data-testid="{bt}"]')
            if not target.count():
                p.keyboard.press("Escape")
                raise UIChanged(f'no [data-testid="{bt}"] in the profile menu of @{u["handle"]}')
        else:
            target = col.locator(f'[data-testid="{bt}"]')
            if not target.count():
                raise UIChanged(f'no [data-testid="{bt}"] on the profile of @{u["handle"]}')
        self.before_write()
        n = len(cap.rest)
        target.first.click()
        # Some of these ask "are you sure" and some do not, and X changes which. Confirm when asked.
        c = p.locator('[data-testid="confirmationSheetConfirm"]').first
        try:
            c.wait_for(state="visible", timeout=15000 if confirm else 4000)
            c.click()
        except Exception:
            if confirm:
                raise UIChanged(f"no confirmation sheet after {bt} on @{u['handle']}")
        b = cap.wait_rest(rest, timeout=20, after=n)
        if b is None:
            raise ActionFailed(f"X did not confirm {rest} for @{u['handle']}")
        if cap.refusal(b):
            raise ActionFailed(f"X refused {rest} for @{u['handle']}: {cap.refusal(b)}")
        return {"user": u["handle"], "result": "done"}
    finally:
        cap.detach()
        p.close()


def follow(self, ref):
    return _account_action(self, ref, "{id}-follow", "friendships/create", state="i_follow", want=True)


def unfollow(self, ref):
    return _account_action(self, ref, "{id}-unfollow", "friendships/destroy", confirm=True, state="i_follow", want=False)


def mute(self, ref):
    return _account_action(self, ref, "mute", "mutes/users/create", menu=True, state="i_mute", want=True)


def unmute(self, ref):
    # A muted profile shows an Unmute link in its header; the menu entry keeps the testid "mute".
    return _account_action(self, ref, "mute", "mutes/users/destroy", menu=True, alt="unmuteLink",
                           state="i_mute", want=False)


def block(self, ref):
    return _account_action(self, ref, "block", "blocks/create", confirm=True, menu=True, state="i_block", want=True)


def unblock(self, ref):
    return _account_action(self, ref, "{id}-unblock", "blocks/destroy", confirm=True, state="i_block", want=False)


def edit_profile(self, name=None, bio=None, location=None, website=None, avatar=None, banner=None, drop_banner=False):
    """Change this account's profile. Anything left as None is not touched. The handle is never changed."""
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, "https://x.com/settings/profile")
        nm = p.locator('input[name="displayName"]').first
        nm.wait_for(state="visible", timeout=30000)
        p.wait_for_timeout(1500)
        for sel, val in (('input[name="displayName"]', name), ('textarea[name="description"]', bio),
                         ('input[name="location"]', location), ('input[name="url"]', website)):
            if val is not None:
                f = p.locator(sel).first
                if not f.count():
                    raise UIChanged(f"no {sel} on the profile editor")
                f.fill(val)
        files = p.locator('input[type="file"]')
        for idx, img in ((1, avatar), (0, banner)):
            if img is None:
                continue
            if files.count() < 2:
                raise UIChanged("the profile editor has no image inputs")
            files.nth(idx).set_input_files(img)
            p.locator('[data-testid="applyButton"]').first.click(timeout=20000)
            p.wait_for_timeout(2000)
        if drop_banner:
            # The banner row shows a remove button only when a banner exists. It has no testid; its
            # position is the second button in the banner area.
            rm = p.locator('[aria-label="画像を削除"], [aria-label="Remove photo"]')
            if rm.count():
                rm.first.click()
                p.wait_for_timeout(800)
        self.before_write()
        n = len(cap.rest)
        p.locator('[data-testid="Profile_Save_Button"]').first.click(timeout=20000)
        p.wait_for_timeout(1500)
        ok = p.locator('[data-testid="confirmationSheetConfirm"]')
        if ok.count():
            ok.first.click(timeout=10000)
        b = cap.wait_rest("account/update_profile", timeout=25, after=n)
        if b is None:
            raise ActionFailed("X did not confirm the profile update")
        if cap.refusal(b):
            raise ActionFailed(f"X refused the profile update: {cap.refusal(b)}")
        p.wait_for_timeout(2000)
        return {"user": self.me, "result": "done"}
    finally:
        cap.detach()
        p.close()
