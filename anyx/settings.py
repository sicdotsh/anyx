"""Account settings.

Reading: every settings page loads account/settings.json, which carries the current value of nearly
every setting, so `settings` returns it as is.

Changing: each named setting below is a switch, choice or field on one settings page. Switches carry
no names, so they are found by their position among the page's switches (positions are checked by the
live test). After a change the value is read back from settings.json.
"""

from . import graphql
from .errors import ActionFailed, UIChanged, UsageError

# name: (page, kind, where, settings.json key or None)
#   kind "switch": where is the index among the page's checkboxes
#   kind "radio":  where is the radio group name; the value picks the option by its index in CHOICES
#   kind "select": where is the index among the page's selects
SETTINGS = {
    "protected":        ("audience_and_tagging", "switch", 0, "protected"),
    "protect_videos":   ("audience_and_tagging", "switch", 1, None),
    "media_tagging":    ("tagging", "switch", 0, None),
    "media_tagging_from": ("tagging", "radio", "allow_media_tagging", "allow_media_tagging"),
    "sensitive_media":  ("content_you_see", "switch", 0, "display_sensitive_media"),
    "quality_filter":   ("notifications/filters", "switch", 0, "universal_quality_filtering_enabled"),
    "personalize_location": ("location_information", "switch", 0, "allow_location_history_personalization"),
    "personalize_inferred": ("off_twitter_activity", "switch", 0, "allow_logged_out_device_personalization"),
    "personalize_ads":  ("ads_preferences", "switch", 0, "allow_ads_personalization"),
    "grok_training":    ("grok_settings", "switch", 0, None),
    "grok_personalize": ("grok_settings", "switch", 1, None),
    "grok_history":     ("grok_settings", "switch", 2, None),
    "language":         ("language", "select", 0, "language"),
    "dms":              ("chat", "chat", None, "allow_dms_from"),
}
CHOICES = {"media_tagging_from": ("all", "following"),
           # chat > settings > message requests: "allow requests from" No one / Verified users / Everyone
           "dms": ("following", "verified", "everyone")}
ON = ("on", "true", "1", "yes")
OFF = ("off", "false", "0", "no")


def settings(self):
    """The account's settings, as X itself stores them (account/settings.json)."""
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, "https://x.com/settings/account")
        b = cap.wait_rest("account/settings.json", timeout=20)
        if b is None:
            p.reload(wait_until="domcontentloaded")
            b = cap.wait_rest("account/settings.json", timeout=20)
        if b is None:
            if any("settings.json" in n for n in cap.limited):
                raise cap.limit_error("account/settings.json")
            raise UIChanged("settings.json did not load")
        return b
    finally:
        cap.detach()
        p.close()


def _dm_settings(cap):
    for b in reversed(cap.of("xChatDmSettingsQuery", "DmSettingsQuery")):
        d = (b.get("data") or {}).get("dm_settings")
        if d:
            return d
    return None


def _set_dms(self, value):
    from . import chat
    opts = CHOICES["dms"]
    if value not in opts:
        raise UsageError(f"dms is one of {', '.join(opts)}")
    p = self.page()
    cap = graphql.Capture(p)
    try:
        chat._open(self, p, "https://x.com/i/chat/settings/message-requests")
        p.wait_for_timeout(2500)
        cur = (_dm_settings(cap) or {}).get("allow_dms_from")
        if cur == value:
            return {"setting": "dms", "value": value, "result": "already"}
        row = p.locator('[data-testid="dm-container"] button').filter(has=p.locator('[data-slot="xds-list-label"]')).last
        self.before_write()
        row.click()
        radios = p.locator("[role=radio]")
        radios.first.wait_for(state="visible", timeout=10000)
        radios.nth(opts.index(value)).click()
        dlg = radios.first.locator("xpath=ancestor::*[@role='dialog'][1]")
        ok = dlg.locator("button").filter(has_text="Confirm")
        if not ok.count():
            ok = dlg.locator("button").filter(has_text="確認")
        if not ok.count():
            raise UIChanged("no Confirm button in the message-request choice")
        ok.first.click()
        p.wait_for_timeout(2500)
        n = len(cap.items)
        p.reload(wait_until="domcontentloaded")
        chat._open(self, p, "https://x.com/i/chat/settings/message-requests")
        cap.wait_for(("xChatDmSettingsQuery", "DmSettingsQuery"), timeout=20, after=n)
        now = (_dm_settings(cap) or {}).get("allow_dms_from")
        if now != value:
            raise ActionFailed(f"dms is still {now}")
        return {"setting": "dms", "value": value, "result": "done"}
    finally:
        cap.detach()
        p.close()


def set_setting(self, name, value):
    if name not in SETTINGS:
        raise UsageError(f"unknown setting {name} (known: {', '.join(SETTINGS)})")
    page, kind, where, key = SETTINGS[name]
    if kind == "chat":
        return _set_dms(self, value)
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, f"https://x.com/settings/{page}")
        col = p.locator("main").first
        col.wait_for(state="visible", timeout=20000)
        need = {"switch": 'input[type="checkbox"]', "radio": 'input[type="radio"]', "select": "select"}[kind]
        idx = where if isinstance(where, int) else 0
        for _ in range(60):                                   # the switches render a moment after the page
            if col.locator(need).count() > idx:
                break
            if any("settings.json" in n for n in cap.limited):
                raise cap.limit_error("account/settings.json")
            p.wait_for_timeout(250)
        p.wait_for_timeout(800)
        if kind == "switch":
            v = str(value).lower()
            if v not in ON + OFF:
                raise UsageError(f"{name} is on or off")
            want = v in ON
            boxes = col.locator('input[type="checkbox"]')
            if boxes.count() <= where:
                raise UIChanged(f"{page} has {boxes.count()} switches, expected more than {where}")
            box = boxes.nth(where)
            if box.is_checked() == want:
                return {"setting": name, "value": want, "result": "already"}
            self.before_write()
            n = len(cap.rest) + len(cap.items)
            box.click(force=True)
            c = p.locator('[data-testid="confirmationSheetConfirm"]').first
            try:
                c.wait_for(state="visible", timeout=3000)
                c.click()
            except Exception:
                pass
            p.wait_for_timeout(2500)
            if box.is_checked() != want:
                raise ActionFailed(f"{name} did not change")
            if len(cap.rest) + len(cap.items) == n:
                raise ActionFailed(f"X sent nothing for {name}")
            return {"setting": name, "value": want, "result": "done"}
        if kind == "radio":
            opts = CHOICES[name]
            if value not in opts:
                raise UsageError(f"{name} is one of {', '.join(opts)}")
            radios = col.locator(f'input[type="radio"][name="{where}"]')
            r = radios.nth(opts.index(value))
            if r.is_checked():
                return {"setting": name, "value": value, "result": "already"}
            self.before_write()
            r.click(force=True)
            p.wait_for_timeout(2500)
            if not r.is_checked():
                raise ActionFailed(f"{name} did not change")
            return {"setting": name, "value": value, "result": "done"}
        if kind == "select":
            sel = col.locator("select").nth(where)
            if sel.input_value() == value:
                return {"setting": name, "value": value, "result": "already"}
            self.before_write()
            sel.select_option(value)
            save = col.locator('[data-testid="settingsDetailSave"]')
            if save.count():
                save.first.click()
            p.wait_for_timeout(2500)
            return {"setting": name, "value": value, "result": "done"}
        raise UsageError(f"cannot change {name}")
    finally:
        cap.detach()
        p.close()


def muted_words(self):
    """Muted words, from mutes/keywords/list.json (what X's own settings page reads)."""
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, "https://x.com/settings/muted_keywords")
        b = cap.wait_rest("mutes/keywords/list", timeout=20)
        if b is None:
            raise UIChanged("the muted words list did not load")
        return [{"id": k["id"], "word": k["keyword"], "where": k.get("mute_surfaces"),
                 "until": k.get("valid_until"), "options": k.get("mute_options")} for k in b.get("muted_keywords") or []]
    finally:
        cap.detach()
        p.close()


def mute_word(self, word):
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, "https://x.com/settings/add_muted_keyword")
        col = p.locator("main").first
        box = col.locator('input[name="keyword"], input[type="text"]:not([placeholder])').last
        box.wait_for(state="visible", timeout=20000)
        box.fill(word)
        save = col.locator('[data-testid="settingsDetailSave"]')
        if not save.count():
            raise UIChanged("no Save button for muted words")
        self.before_write()
        n = len(cap.rest)
        save.first.click()
        b = cap.wait_rest("mutes/keywords/create", timeout=20, after=n)
        if b is None or cap.refusal(b):
            raise ActionFailed(f"X did not accept mutes/keywords/create: {cap.refusal(b) if b else 'no answer'}")
        return {"word": word, "result": "done"}
    finally:
        cap.detach()
        p.close()


def unmute_word(self, word):
    """Remove a muted word, with the Unmute button on its row of the list."""
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, "https://x.com/settings/muted_keywords")
        b = cap.wait_rest("mutes/keywords/list", timeout=20)
        words = [k["keyword"] for k in (b or {}).get("muted_keywords") or []]
        if word not in words:
            return {"word": word, "result": "already"}
        main = p.locator("main").first
        label = main.locator(f'[data-testid="{word}-LTRtext"]').first
        label.wait_for(state="visible", timeout=15000)
        row = label.locator("xpath=ancestor::*[.//button or .//*[@role='button']][1]")
        btn = row.locator('[role=button][aria-label="ミュートを解除"], [role=button][aria-label="Unmute"], '
                          'button[aria-label="ミュートを解除"], button[aria-label="Unmute"]')
        if not btn.count():
            btn = main.locator('[aria-label="ミュートを解除"], [aria-label="Unmute"]').nth(words.index(word))
        self.before_write()
        n = len(cap.rest)
        btn.first.click()
        c = p.locator('[data-testid="confirmationSheetConfirm"]').first
        try:
            c.wait_for(state="visible", timeout=5000)
            c.click()
        except Exception:
            pass
        b = cap.wait_rest("mutes/keywords/destroy", timeout=20, after=n)
        if b is None or cap.refusal(b):
            raise ActionFailed(f"X did not accept mutes/keywords/destroy: {cap.refusal(b) if b else 'no answer'}")
        return {"word": word, "result": "done"}
    finally:
        cap.detach()
        p.close()


def set_handle(self, new):
    """Change this account's @handle. The vault and config follow the new name."""
    import os
    import re
    from . import config, vault
    new = new.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{4,15}", new):
        raise UsageError("a handle is 4 to 15 letters, digits or _")
    if new.lower() == (self.me or "").lower():
        return {"user": new, "result": "already"}
    p = self.page()
    cap = graphql.Capture(p)
    try:
        self.goto(p, "https://x.com/settings/screen_name")
        box = p.locator('main input[name="typedScreenName"]').first
        box.wait_for(state="visible", timeout=20000)
        box.fill(new)
        p.wait_for_timeout(2500)                    # X checks availability as you type
        save = p.locator('main [data-testid="settingsDetailSave"]').first
        if save.get_attribute("aria-disabled") == "true" or save.is_disabled():
            raise ActionFailed(f"X does not accept @{new} (taken or not allowed)")
        self.before_write()
        n = len(cap.rest)
        save.click()
        b = cap.wait_rest("account/settings", timeout=20, after=n)
        if b is None:
            raise ActionFailed("X did not confirm the new handle")
        if cap.refusal(b):
            raise ActionFailed(f"X refused the new handle: {cap.refusal(b)}")
        p.wait_for_timeout(2000)
    finally:
        cap.detach()
        p.close()
    old = self.handle
    cookies = vault.load(old)
    vault.save(new, cookies)
    os.remove(vault.path(old))
    for ext in (".pin", ".meta.json"):
        f = vault.path(old)[:-5] + ext
        if os.path.exists(f):
            os.replace(f, vault.path(new)[:-5] + ext)
    cfg = config.load()
    if old in cfg["accounts"]:
        cfg["accounts"][new.lower()] = cfg["accounts"].pop(old)
        config.save(cfg)
    self.handle = self.me = new.lower()
    return {"user": new, "was": old, "result": "done"}
