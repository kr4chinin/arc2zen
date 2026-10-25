"""Selected Arc autofill/search imports and an optional Arc-like key preset."""

import json
import sqlite3
import time
import uuid
from contextlib import closing

from .common import require, require_candidate, set_pref
from .jsonlz4 import read, write
from .places import guid


def search_shortcuts(web, profile):
    path = profile / "search.json.mozlz4"
    search = read(path)
    aliases = {e.get("_metaData", {}).get("alias") for e in search["engines"]}
    report = {"search_added": 0, "search_skipped": 0}
    for (
        name,
        keyword,
        url,
        post,
    ) in web.execute("""select short_name,keyword,url,search_url_post_params
            from keywords where prepopulate_id=0 and starter_pack_id=0 and is_active=1"""):
        if (
            not keyword
            or keyword in aliases
            or "{searchTerms}" not in url
            or not url.startswith(("https://", "http://"))
            or post
        ):
            report["search_skipped"] += 1
            continue
        search["engines"].append(
            {
                "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "arc2zen-search:" + keyword)),
                "_name": name + " (Arc)",
                "_loadPath": "[user]",
                "_metaData": {"alias": keyword},
                "_urls": [{"template": url, "params": [], "rels": []}],
                "_definedAliases": [],
            }
        )
        aliases.add(keyword)
        report["search_added"] += 1
    write(path, search)
    return report


def keyboard_preset(profile):
    path = profile / "zen-keyboard-shortcuts.json"
    keyboard = json.loads(path.read_text())
    mappings = {
        "zen-toggle-pin-tab": ("d", "", False),
        "zen-toggle-sidebar": ("s", "", False),
        "zen-duplicate-tab": ("", "VK_RETURN", True),
    }
    for identity, (key, keycode, alt) in mappings.items():
        shortcut = next((s for s in keyboard["shortcuts"] if s["id"] == identity), None)
        require(shortcut is not None, "Required Zen keyboard command is missing")
        for other in keyboard["shortcuts"]:
            mods = other.get("modifiers", {})
            same_command = bool(mods.get("accel") or mods.get("meta")) and not mods.get("control")
            if (
                other is not shortcut
                and not other.get("disabled")
                and other.get("key", "") == key
                and other.get("keycode", "") in (keycode, None)
                and same_command
                and bool(mods.get("alt")) == alt
                and not mods.get("shift")
            ):
                other["disabled"] = True
        shortcut.update(
            {
                "key": key,
                "keycode": keycode,
                "disabled": False,
                "modifiers": {
                    "control": False,
                    "alt": alt,
                    "shift": False,
                    "meta": False,
                    "accel": True,
                },
            }
        )
    path.write_text(json.dumps(keyboard, ensure_ascii=False))
    return {"keyboard_bindings": len(mappings)}


def addresses(web, profile):
    path = profile / "autofill-profiles.json"
    data = (
        json.loads(path.read_text())
        if path.exists()
        else {"version": 1, "addresses": [], "creditCards": []}
    )
    require(data.get("version") == 1, "Unsupported address store version")
    existing = {a.get("guid") for a in data["addresses"]}
    fields = {
        7: "name",
        9: "email",
        14: "tel",
        33: "address-level2",
        34: "address-level1",
        35: "postal-code",
        36: "country",
        60: "organization",
        77: "street-address",
        81: "address-level3",
    }
    added, now = 0, int(time.time() * 1000)
    for arc_guid, used, used_at, modified in web.execute(
        "select guid,use_count,use_date,date_modified from addresses"
    ):
        identity = guid("arc2zen-address:" + arc_guid)
        if identity in existing:
            continue
        tokens = {
            t: v
            for t, v in web.execute(
                "select type,value from address_type_tokens where guid=?", (arc_guid,)
            )
            if v
        }
        address = {field: tokens[t] for t, field in fields.items() if t in tokens}
        if not address.get("name"):
            name = " ".join(tokens[t] for t in (3, 4, 5) if t in tokens)
            if name:
                address["name"] = name
        if not address.get("street-address"):
            street = "\n".join(tokens[t] for t in (30, 31, 76) if t in tokens)
            if street:
                address["street-address"] = street
        require(
            not address.get("country") or len(address["country"]) == 2,
            "Unsupported address country format",
        )
        address.update(
            {
                "guid": identity,
                "version": 1,
                "timeCreated": int(modified * 1000) or now,
                "timeLastUsed": int(used_at * 1000),
                "timeLastModified": int(modified * 1000) or now,
                "timesUsed": used,
            }
        )
        data["addresses"].append(address)
        existing.add(identity)
        added += 1
    path.write_text(json.dumps(data, ensure_ascii=False))
    set_pref(profile, "extensions.formautofill.addresses.supported", "on")
    set_pref(profile, "extensions.formautofill.addresses.enabled", True)
    return {"addresses_added": added}


def forms(web, profile):
    report = {"forms_added": 0, "forms_merged": 0, "forms_empty_skipped": 0}
    with closing(sqlite3.connect(profile / "formhistory.sqlite")) as target, target:
        known = {
            (name, value): fid
            for fid, name, value in target.execute("select id,fieldname,value from moz_formhistory")
        }
        for name, value, count, created, used in web.execute(
            "select name,value,count,date_created,date_last_used from autofill"
        ):
            if not name or not value:
                report["forms_empty_skipped"] += 1
                continue
            first, last = int(created) * 1000000, int(used) * 1000000
            require(
                0 < first <= last <= time.time_ns() // 1000 + 86400000000,
                "Unsupported form timestamp; expected Unix seconds in Arc",
            )
            identity = (name, value)
            if identity in known:
                target.execute(
                    """update moz_formhistory set timesUsed=max(timesUsed,?),
                    firstUsed=min(firstUsed,?),lastUsed=max(lastUsed,?) where id=?""",
                    (count, first, last, known[identity]),
                )
                report["forms_merged"] += 1
            else:
                result = target.execute(
                    """insert into moz_formhistory(fieldname,value,timesUsed,firstUsed,lastUsed,guid)
                    values(?,?,?,?,?,?)""",
                    (
                        name,
                        value,
                        count,
                        first,
                        last,
                        guid("arc2zen-form:" + name + "\0" + value),
                    ),
                )
                known[identity] = result.lastrowid
                report["forms_added"] += 1
        require(
            target.execute("pragma integrity_check").fetchone()[0] == "ok",
            "Form database failed integrity check",
        )
    return report


def convert(source, profile, parts):
    require_candidate(profile)
    report = {}
    web_parts = parts & {"search", "addresses", "forms"}
    if web_parts:
        web = sqlite3.connect(
            (source / "Web Data.sqlite").resolve().as_uri() + "?mode=ro", uri=True
        )
        try:
            for part, function in (
                ("search", search_shortcuts),
                ("addresses", addresses),
                ("forms", forms),
            ):
                if part in web_parts:
                    report.update(function(web, profile))
        finally:
            web.close()
    if "keyboard" in parts:
        report.update(keyboard_preset(profile))
    return report
