"""Current Zen session adapter, informed by noneedofit/arc2zen (MIT).

Only flat Arc sidebars and the default Arc profile are currently supported.
Unexpected folder/container structures stop preparation before any live write.
"""

import copy
import html
import json
import time
import uuid

from .common import require, require_candidate, set_pref
from .jsonlz4 import read, write


def stable_id(kind, arc_id):
    return "{" + str(uuid.uuid5(uuid.NAMESPACE_URL, f"arc2zen:{kind}:{arc_id}")) + "}"


def pairs(values):
    require(
        isinstance(values, list) and len(values) % 2 == 0,
        "Unsupported Arc key/value array",
    )
    result = dict(zip(values[::2], values[1::2]))
    require(len(result) * 2 == len(values), "Duplicate Arc identifiers")
    return result


def parse(path):
    data = json.loads(path.read_text())
    containers = data["sidebar"]["containers"]
    locals_ = [
        container
        for container in containers
        if isinstance(container, dict) and "items" in container and "spaces" in container
    ]
    require(len(locals_) == 1, "Expected one local Arc sidebar container")
    local = locals_[0]
    items, spaces = pairs(local["items"]), pairs(local["spaces"])
    require(spaces, "No Arc spaces found")
    require(
        all(s.get("profile") == {"default": True} for s in spaces.values()),
        "Multiple/custom Arc profiles require an explicit container mapping; not supported",
    )
    sync = data.get("firebaseSyncState", {}).get("syncData", {}).get("spaceModels", [])
    models = pairs(sync) if sync else {}
    return items, spaces, models


def make_tab(tab_id, title, url, workspace, essential=False):
    require(url.startswith(("https://", "http://")), "Unsupported pinned/essential tab URL")
    entry = {"url": url, "title": title}
    return {
        "entries": [entry],
        "index": 1,
        "lastAccessed": int(time.time() * 1000),
        "pinned": True,
        "hidden": False,
        "zenEssential": essential,
        "zenWorkspace": workspace,
        "zenSyncId": stable_id("tab", tab_id),
        "zenStaticLabel": title,
        "userContextId": 0,
        "attributes": {},
        "_zenPinnedInitialState": {"entry": copy.deepcopy(entry), "userContextId": 0},
    }


def root_items(space, kind, items):
    roots = pairs(space["containerIDs"])
    require(
        kind in roots and roots[kind] in items,
        f"Arc {kind} container is missing; unsupported sidebar layout",
    )
    root = items[roots[kind]]
    return root.get("childrenIds", [])


def tab_info(item):
    require("tab" in item.get("data", {}), "Arc sidebar folders are not supported yet")
    tab = item["data"]["tab"]
    url = tab.get("savedURL", "")
    return item.get("title") or tab.get("savedTitle") or url, url


def convert(source, profile, *, spaces=True, tabs=True):
    require_candidate(profile)
    items, arc_spaces, models = parse(source / "Sidebar.json")
    path = profile / "zen-sessions.jsonlz4"
    zen = read(path)
    require(
        all(k in zen for k in ("spaces", "tabs", "folders", "groups", "splitViewData")),
        "Unsupported Zen session format",
    )
    require(
        all(w.get("containerTabId", 0) == 0 for w in zen["spaces"]),
        "Zen containers require an explicit mapping; not supported",
    )
    known_spaces = {w["uuid"]: w for w in zen["spaces"]}
    known_tabs = {t.get("zenSyncId") for t in zen["tabs"]}
    report = {
        "spaces_added": 0,
        "pinned_added": 0,
        "essentials_added": 0,
        "open_tabs_added": 0,
        "unsupported_open_tabs_skipped": 0,
        "unsupported_pinned_tabs_skipped": 0,
        "unsupported_essentials_skipped": 0,
    }
    mapping, portable, open_tabs = {}, [], []

    def add(tab_id, workspace, *, essential=False, pinned=True):
        title, url = tab_info(items[tab_id])
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            counter = (
                "unsupported_essentials_skipped"
                if essential
                else (
                    "unsupported_pinned_tabs_skipped" if pinned else "unsupported_open_tabs_skipped"
                )
            )
            report[counter] += 1
            return
        portable.append((title, url))
        identity = stable_id("tab", tab_id)
        if identity in known_tabs:
            return
        entry = make_tab(tab_id, title, url, workspace, essential)
        if not pinned:
            entry["pinned"] = False
            entry.pop("_zenPinnedInitialState", None)
            open_tabs.append(copy.deepcopy(entry))
        if essential:
            zen["tabs"].insert(report["essentials_added"], entry)
        else:
            zen["tabs"].append(entry)
        known_tabs.add(identity)
        report[
            "essentials_added" if essential else "pinned_added" if pinned else "open_tabs_added"
        ] += 1

    for arc_id, space in arc_spaces.items():
        identity = stable_id("space", arc_id)
        mapping[arc_id] = identity
        if identity not in known_spaces:
            require(spaces, "Import spaces first or include spaces with tabs")
            info = models.get(arc_id, {}).get("value", {})
            custom = info.get("customInfo", {})
            theme = {
                "type": "gradient",
                "gradientColors": [],
                "opacity": 0.5,
                "texture": 0,
            }
            color = custom.get("windowTheme", {}).get("primaryColorPalette", {}).get("midTone", {})
            if all(k in color for k in ("red", "green", "blue")):
                theme["gradientColors"] = [
                    {
                        "c": [
                            round(max(0, min(1, color[k])) * 255) for k in ("red", "green", "blue")
                        ],
                        "isCustom": False,
                        "algorithm": "floating",
                        "isPrimary": True,
                        "lightness": "75",
                        "position": {"x": 228, "y": 253},
                        "type": "explicit-lightness",
                    }
                ]
            workspace = {
                "uuid": identity,
                "name": space.get("title") or info.get("title") or "Arc space",
                "icon": custom.get("iconType", {}).get("emoji_v2"),
                "containerTabId": 0,
                "theme": theme,
            }
            zen["spaces"].append(workspace)
            known_spaces[identity] = workspace
            report["spaces_added"] += 1
        if spaces:
            for tab_id in root_items(space, "pinned", items):
                add(tab_id, identity)
        if tabs:
            for tab_id in root_items(space, "unpinned", items):
                add(tab_id, identity, pinned=False)
    if spaces:
        for item in items.values():
            container = item.get("data", {}).get("itemContainer", {}).get("containerType", {})
            if "topApps" in container:
                require(
                    container["topApps"]["_0"] == {"default": True},
                    "Custom-profile Essentials are unsupported",
                )
                for tab_id in item.get("childrenIds", []):
                    add(tab_id, next(iter(mapping.values())), essential=True)
    zen["lastCollected"] = int(time.time() * 1000)
    write(path, zen)
    if open_tabs:
        session_path = profile / "sessionstore.jsonlz4"
        session = read(session_path)
        require(
            session.get("windows"),
            "Open Zen once and close it normally before importing tabs",
        )
        session["windows"][0]["tabs"].extend(open_tabs)
        write(session_path, session)
        set_pref(profile, "browser.sessionstore.resume_session_once", True)
    lines = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Arc tabs</TITLE>",
        "<H1>Arc tabs</H1>",
        "<DL><p>",
    ]
    lines.extend(
        f'<DT><A HREF="{html.escape(url, quote=True)}">{html.escape(title)}</A>'
        for title, url in portable
    )
    lines.append("</DL><p>")
    (profile.parent / "Arc-tabs.html").write_text("\n".join(lines))
    return report
