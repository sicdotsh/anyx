"""Communities: read one, its posts, join and leave."""

import re

from . import graphql
from .errors import ActionFailed, NotFound, UIChanged


def _cid(ref):
    m = re.search(r"(\d{10,})", str(ref))
    if not m:
        raise NotFound(f"not a community id or URL: {ref}")
    return m.group(1)


def _community(bodies):
    found = []

    def go(o):
        if isinstance(o, dict):
            if o.get("__typename") == "Community" and (o.get("rest_id") or o.get("id_str")) and "name" in o:
                found.append(o)
                return
            for v in o.values():
                go(v)
        elif isinstance(o, list):
            for v in o:
                go(v)
    go(bodies)
    if not found:
        return None
    c = found[0]
    return {"id": c.get("rest_id") or c.get("id_str"), "name": c.get("name"), "description": c.get("description"),
            "members": c.get("member_count"), "moderators": c.get("moderator_count"),
            "role": c.get("role"), "joined": c.get("role") in ("Member", "Moderator", "Admin"),
            "join_policy": c.get("join_policy"), "rules": [r.get("name") for r in c.get("rules") or []],
            "url": f"https://x.com/i/communities/{c.get('rest_id') or c.get('id_str')}"}


def community(self, ref):
    cid = _cid(ref)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/i/communities/{cid}")
        if cap.wait_for(("CommunityQuery", "CommunitiesFetchOneQuery"), timeout=30) is None:
            raise NotFound(f"community {cid} did not load")
        p.wait_for_timeout(1500)
        c = _community(cap.of("CommunityQuery", "CommunitiesFetchOneQuery"))
        if not c:
            raise NotFound(f"community {cid} not found")
        return c
    finally:
        cap.detach()
        p.close()


def community_posts(self, ref, limit=40):
    from .read import _collect
    return _collect(self, f"https://x.com/i/communities/{_cid(ref)}", ("CommunityTweetsTimeline",), limit=limit)[0]


def _membership(self, ref, join):
    cid = _cid(ref)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/i/communities/{cid}")
        if cap.wait_for(("CommunityQuery", "CommunitiesFetchOneQuery"), timeout=30) is None:
            raise NotFound(f"community {cid} did not load")
        p.wait_for_timeout(2000)
        c = _community(cap.of("CommunityQuery", "CommunitiesFetchOneQuery")) or {}
        if c.get("joined") == join:
            return {"community": cid, "result": "already"}
        self.clear_layers(p)
        col = p.locator('[data-testid="primaryColumn"]')
        words = ("参加する", "Join") if join else ("参加済み", "参加中", "Joined", "Member")
        btn = None
        for w in words:
            hit = col.locator("[role=button], button").filter(has_text=w)
            if hit.count():
                btn = hit.first
                break
        if not btn:
            raise UIChanged(f"no {'Join' if join else 'Joined'} button on the community page")
        n = len(cap.items)
        btn.click()
        p.wait_for_timeout(1500)
        if join:
            # communities with rules ask to agree before joining
            for w in ("同意して参加", "Agree and join", "同意", "Agree"):
                agree = p.locator("#layers [role=button], #layers button").filter(has_text=w)
                if agree.count():
                    agree.first.click()
                    p.wait_for_timeout(1000)
                    break
        if not join:
            leave = p.locator("[role=menuitem], [role=button]").filter(has_text="退出")
            if not leave.count():
                leave = p.locator("[role=menuitem], [role=button]").filter(has_text="Leave")
            if leave.count():
                leave.first.click()
                p.wait_for_timeout(800)
        c2 = p.locator('[data-testid="confirmationSheetConfirm"]').first
        try:
            c2.wait_for(state="visible", timeout=5000)
            c2.click()
        except Exception:
            pass
        op = "JoinCommunity" if join else "LeaveCommunity"
        cap.confirm((op, op + "Mutation"), f"{op} (ops seen: {cap.ops()[n:][:6]})", after=n, timeout=20)
        return {"community": cid, "result": "done"}
    finally:
        cap.detach()
        p.close()


def join(self, ref):
    return _membership(self, ref, True)


def leave(self, ref):
    return _membership(self, ref, False)
