"""Lists: make, fill, read, empty and delete them."""

from . import graphql
from .errors import ActionFailed, NotFound, UIChanged, UsageError


def _is_list(o):
    return isinstance(o, dict) and o.get("id_str") and "member_count" in o and "mode" in o and "name" in o


def _lists_in(bodies):
    out, seen = [], set()

    def go(o):
        if isinstance(o, dict):
            if _is_list(o):
                if o["id_str"] not in seen:
                    seen.add(o["id_str"])
                    owner = graphql.user(((o.get("user_results") or {}).get("result")) or {}) or {}
                    out.append({"id": o["id_str"], "name": o.get("name"), "description": o.get("description"),
                                "members": o.get("member_count"), "followers": o.get("subscriber_count"),
                                "private": (o.get("mode") or "").lower() == "private",
                                "owner": owner.get("handle"), "url": f"https://x.com/i/lists/{o['id_str']}"})
            for v in o.values():
                go(v)
        elif isinstance(o, list):
            for v in o:
                go(v)
    go(bodies)
    return out


def lists(self):
    """Lists this account owns or follows."""
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/{self.me}/lists")
        if cap.wait_for(("ListsManagementPageTimeline",), timeout=30) is None:
            raise NotFound("the lists page did not load")
        p.wait_for_timeout(1500)
        # the page also suggests other people's lists; keep the "your lists" module only
        mine = []

        def go(o):
            if isinstance(o, dict):
                if str(o.get("entryId", "")).startswith("owned-subscribed-list"):
                    mine.append(o)
                    return
                for v in o.values():
                    go(v)
            elif isinstance(o, list):
                for v in o:
                    go(v)
        go(cap.of("ListsManagementPageTimeline"))
        return _lists_in(mine)
    finally:
        cap.detach()
        p.close()


def _list_id(self, ref):
    s = str(ref)
    if s.isdigit():
        return s
    if "/i/lists/" in s:
        return s.split("/i/lists/", 1)[1].split("/", 1)[0]
    hit = [l for l in lists(self) if (l["name"] or "").lower() == s.lower() and (l["owner"] or "").lower() == (self.me or "").lower()]
    if len(hit) != 1:
        raise NotFound(f"{len(hit)} lists of yours are named {s!r}")
    return hit[0]["id"]


def _button(scope, *words):
    b = scope.locator("[role=button], button")
    for w in words:
        hit = b.filter(has_text=w)
        if hit.count():
            return hit.first
    return None


def list_create(self, name, description="", private=True):
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, "https://x.com/i/lists/create")
        layer = p.locator("#layers")
        layer.locator('input[name="name"]').first.wait_for(state="visible", timeout=20000)
        layer.locator('input[name="name"]').fill(name)
        if description:
            layer.locator('textarea[name="description"]').fill(description)
        box = layer.locator('input[type="checkbox"]').first
        if private:
            box.check()
        else:
            box.uncheck()
        nxt = _button(layer, "次へ", "Next")
        if not nxt:
            raise UIChanged("no Next button on the list form")
        self.before_write()
        n = len(cap.items)
        nxt.click()
        b = cap.confirm(("CreateList",), "CreateList", after=n)
        made = _lists_in([b])
        lid = made[0]["id"] if made else graphql.created_id(b, "list")
        return {"id": lid, "name": name, "private": private, "url": f"https://x.com/i/lists/{lid}"}
    finally:
        cap.detach()
        p.close()


def _membership(self, list_ref, who, add):
    from .session import handle_of
    lid = _list_id(self, list_ref)
    h = handle_of(who)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/{h}")
        if cap.wait_for(("UserByScreenName",), timeout=30) is None:
            raise NotFound(f"@{h} not found")
        p.wait_for_timeout(1500)
        p.locator('[data-testid="primaryColumn"] [data-testid="userActions"]').first.click()
        item = p.locator('[role="menu"] a[href="/i/lists/add_member"]').first
        item.wait_for(state="visible", timeout=15000)
        n0 = len(cap.items)
        item.click()
        b = cap.wait_for(("ListOwnerships",), timeout=20, after=n0)
        p.wait_for_timeout(1500)
        owned = _lists_in([b] if b else [])
        idx = next((i for i, l in enumerate(owned) if l["id"] == lid), None)
        if idx is None:
            raise NotFound(f"list {lid} is not one of @{self.me}'s lists")
        cell = p.locator('#layers [data-testid="listCell"]').nth(idx)
        checked = cell.get_attribute("aria-checked") == "true"
        if checked == add:
            p.keyboard.press("Escape")
            return {"list": lid, "user": h, "result": "already"}
        cell.click()
        p.wait_for_timeout(500)
        save = _button(p.locator("#layers"), "保存", "Save")
        if not save:
            raise UIChanged("no Save button in the list picker")
        self.before_write()
        n = len(cap.items)
        save.click()
        op = "ListAddMember" if add else "ListRemoveMember"
        cap.confirm((op,), f"{op}", after=n, timeout=20)
        return {"list": lid, "user": h, "result": "done"}
    finally:
        cap.detach()
        p.close()


def list_add(self, list_ref, who):
    return _membership(self, list_ref, who, True)


def list_remove(self, list_ref, who):
    return _membership(self, list_ref, who, False)


def list_posts(self, list_ref, limit=40):
    from .read import _collect
    lid = _list_id(self, list_ref)
    return _collect(self, f"https://x.com/i/lists/{lid}", ("ListLatestTweetsTimeline", "ListTweetsTimeline"), limit=limit)[0]


def list_members(self, list_ref, limit=200):
    from .read import _collect
    lid = _list_id(self, list_ref)
    return _collect(self, f"https://x.com/i/lists/{lid}/members", ("ListMembers",), limit=limit,
                    pick=lambda tw, us: us)[0]


def list_delete(self, list_ref):
    lid = _list_id(self, list_ref)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        # the editor only opens from the list page (a direct load of /info shows the list)
        self.goto(p, f"https://x.com/i/lists/{lid}")
        edit = p.locator(f'[data-testid="primaryColumn"] a[href="/i/lists/{lid}/info"]').first
        edit.wait_for(state="visible", timeout=20000)
        edit.click()
        p.wait_for_timeout(2500)
        layer = p.locator("#layers")
        btn = _button(layer, "リストを削除", "Delete List", "Delete list")
        if not btn:
            raise UIChanged("no Delete List button on the list editor")
        btn.click()
        c = p.locator('[data-testid="confirmationSheetConfirm"]').first
        c.wait_for(state="visible", timeout=15000)
        self.before_write()
        n = len(cap.items)
        c.click()
        cap.confirm(("DeleteList",), "DeleteList", after=n, timeout=20)
        return {"list": lid, "result": "deleted"}
    finally:
        cap.detach()
        p.close()
