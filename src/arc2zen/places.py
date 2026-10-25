"""Import Arc history and bookmarks into a task-owned, closed Zen profile copy."""

import base64
import hashlib
import json
import sqlite3
import time
from contextlib import ExitStack, closing
from urllib.parse import urlsplit

from .common import require, require_candidate

EPOCH = 11644473600 * 1000000
MASK = 4294967295


def string_hash(data):
    value = 0
    for byte in data:
        value = 2654435769 * ((value << 5 | value >> 27) & MASK ^ byte) & MASK
    return value


def url_hash(url):
    data = url.encode("utf-8")
    low = string_hash(data[:1500])
    colon = data[:50].find(b":")
    return ((string_hash(data[:colon]) & 65535) << 32) + low if colon >= 0 else low


def guid(key):
    return base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()[:9]).decode()


def supported(url):
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ("http", "https", "ftp", "file") and len(url.encode()) <= 65536
    except ValueError:
        return False


def convert(source_dir, profile, *, history=True, bookmarks=True):
    require_candidate(profile)
    with ExitStack() as stack:
        c = stack.enter_context(closing(sqlite3.connect(profile / "places.sqlite")))
        require(
            not c.execute("select 1 from sqlite_master where type='trigger'").fetchone(),
            "Places compatibility or integrity validation failed",
        )
        for url, stored in c.execute(
            "select url,url_hash from moz_places where url_hash<>0 limit 2000"
        ):
            require(url_hash(url) == stored, "Places URL hash mismatch")
        c.execute("pragma foreign_keys=on")
        source = None
        if history:
            source = stack.enter_context(
                closing(
                    sqlite3.connect(
                        (source_dir / "History.sqlite").resolve().as_uri() + "?mode=ro", uri=True
                    )
                )
            )
            source.row_factory = sqlite3.Row
        report = {
            "source_urls": source.execute("select count(*) from urls").fetchone()[0]
            if source
            else 0,
            "source_visits": source.execute("select count(*) from visits").fetchone()[0]
            if source
            else 0,
            "urls_added": 0,
            "visits_added": 0,
            "duplicate_visits_skipped": 0,
            "unsupported_visits_skipped": 0,
            "bookmarks_added": 0,
            "bookmark_folders_added": 0,
            "unsupported_bookmarks_skipped": 0,
        }
        places = dict(c.execute("select url,id from moz_places"))
        origins = {
            (prefix, host): oid
            for oid, prefix, host in c.execute("select id,prefix,host from moz_origins")
        }
        imported_places = set()

        def ensure_place(url, title="", hidden=0, typed=0):
            if url in places:
                pid = places[url]
                c.execute(
                    "update moz_places set typed=max(typed,?),hidden=min(hidden,?) where id=?",
                    (typed, hidden, pid),
                )
                return pid
            parsed = urlsplit(url)
            host = parsed.hostname or ""
            origin_host = parsed.netloc.rsplit("@", 1)[-1]
            prefix = parsed.scheme + ("://" if url.startswith(parsed.scheme + "://") else ":")
            origin = (prefix, origin_host)
            if origin not in origins:
                cur = c.execute(
                    "insert into moz_origins(prefix,host,frecency,recalc_frecency,recalc_alt_frecency) values(?,?,0,1,1)",
                    origin,
                )
                origins[origin] = cur.lastrowid
            cur = c.execute(
                "insert into moz_places\n            (url,title,rev_host,hidden,typed,frecency,guid,url_hash,origin_id,recalc_frecency,recalc_alt_frecency)\n            values(?,?,?,?,?,-1,?,?,?,1,1)",
                (
                    url,
                    title[:4096],
                    host[::-1] + "." if host else None,
                    hidden,
                    typed,
                    guid("arc2zen-place:" + url),
                    url_hash(url),
                    origins[origin],
                ),
            )
            places[url] = cur.lastrowid
            report["urls_added"] += 1
            return cur.lastrowid

        if history:
            arc_places = {}
            for row in source.execute("select * from urls"):
                if not supported(row["url"]):
                    continue
                pid = ensure_place(
                    row["url"],
                    row["title"] or "",
                    row["hidden"],
                    int(row["typed_count"] > 0),
                )
                arc_places[row["id"]] = pid
                imported_places.add(pid)
            existing = {
                (pid, date, typ): vid
                for vid, pid, date, typ in c.execute(
                    "select id,place_id,visit_date,visit_type from moz_historyvisits"
                )
            }
            visit_ids = {}
            deferred = []
            transitions = {
                0: 1,
                1: 2,
                2: 3,
                3: 4,
                4: 8,
                5: 1,
                6: 1,
                7: 1,
                8: 9,
                9: 2,
                10: 1,
            }
            for row in source.execute(
                "select id,url,visit_time,from_visit,transition from visits order by id"
            ):
                pid = arc_places.get(row["url"])
                date = row["visit_time"] - EPOCH
                if pid is None or date <= 0:
                    report["unsupported_visits_skipped"] += 1
                    continue
                transition = row["transition"] & MASK
                if transition & 255 not in transitions:
                    report["unsupported_visits_skipped"] += 1
                    continue
                typ = 6 if transition & 3221225472 else transitions[transition & 255]
                identity = (pid, date, typ)
                if identity in existing:
                    visit_ids[row["id"]] = existing[identity]
                    report["duplicate_visits_skipped"] += 1
                    continue
                referrer = visit_ids.get(row["from_visit"], 0)
                cur = c.execute(
                    "insert into moz_historyvisits(from_visit,place_id,visit_date,visit_type,session,source) values(?,?,?,?,0,0)",
                    (referrer, pid, date, typ),
                )
                vid = cur.lastrowid
                visit_ids[row["id"]] = vid
                existing[identity] = vid
                if row["from_visit"] and (not referrer):
                    deferred.append((vid, row["from_visit"]))
                report["visits_added"] += 1
            for vid, old_ref in deferred:
                if old_ref in visit_ids:
                    c.execute(
                        "update moz_historyvisits set from_visit=? where id=?",
                        (visit_ids[old_ref], vid),
                    )
        now = time.time_ns() // 1000
        if bookmarks:
            bookmark_data = json.loads((source_dir / "Bookmarks.json").read_text())
            bookmark_guids = dict(c.execute("select guid,id from moz_bookmarks"))
            unfiled = c.execute(
                "select id from moz_bookmarks where guid='unfiled_____'"
            ).fetchone()[0]

            def bookmark(node, parent):
                is_url = node.get("type") == "url"
                if is_url and (not supported(node.get("url", ""))):
                    report["unsupported_bookmarks_skipped"] += 1
                    return
                identity = guid("arc2zen-bookmark:" + str(node["guid"]))
                if identity in bookmark_guids:
                    bid = bookmark_guids[identity]
                else:
                    position = c.execute(
                        "select coalesce(max(position)+1,0) from moz_bookmarks where parent=?",
                        (parent,),
                    ).fetchone()[0]
                    pid = ensure_place(node["url"], node.get("name", "")) if is_url else None
                    try:
                        added = max(0, int(node.get("date_added", 0)) - EPOCH) or now
                        modified = max(0, int(node.get("date_modified", 0)) - EPOCH) or added
                    except ValueError:
                        added = modified = now
                    cur = c.execute(
                        "insert into moz_bookmarks(type,fk,parent,position,title,dateAdded,lastModified,guid,syncStatus,syncChangeCounter)\n                    values(?,?,?,?,?,?,?,?,1,1)",
                        (
                            1 if is_url else 2,
                            pid,
                            parent,
                            position,
                            node.get("name", ""),
                            added,
                            modified,
                            identity,
                        ),
                    )
                    bid = cur.lastrowid
                    bookmark_guids[identity] = bid
                    report["bookmarks_added" if is_url else "bookmark_folders_added"] += 1
                    if pid:
                        imported_places.add(pid)
                for child in node.get("children", []):
                    bookmark(child, bid)

            root = {
                "guid": "arc2zen-import-root",
                "type": "folder",
                "name": "Arc Bookmarks",
                "children": [
                    node
                    for node in bookmark_data.get("roots", {}).values()
                    if isinstance(node, dict) and node.get("type") == "folder"
                ],
            }
            bookmark(root, unfiled)
        c.execute("create temp table arc_places(id integer primary key)")
        c.executemany("insert into arc_places values(?)", ((pid,) for pid in imported_places))
        c.execute(
            "create temp table arc_visit_stats(id integer primary key, visits integer, last_visit integer)"
        )
        c.execute(
            "insert into arc_visit_stats select place_id,\n        sum(case when visit_type not in (0,4,7,8,9) then 1 else 0 end),max(visit_date)\n        from moz_historyvisits group by place_id"
        )
        c.execute(
            "update moz_places set\n        visit_count=coalesce((select visits from arc_visit_stats v where v.id=moz_places.id),0),\n        last_visit_date=(select last_visit from arc_visit_stats v where v.id=moz_places.id),\n        foreign_count=(select count(*) from moz_bookmarks b where b.fk=moz_places.id)+(select count(*) from moz_keywords k where k.place_id=moz_places.id),\n        recalc_frecency=1,recalc_alt_frecency=1 where id in (select id from arc_places)"
        )
        require(
            not c.execute("pragma foreign_key_check").fetchall(),
            "Foreign key validation failed",
        )
        require(
            c.execute("pragma integrity_check").fetchone()[0] == "ok",
            "Places compatibility or integrity validation failed",
        )
        report["total_places_after"] = c.execute("select count(*) from moz_places").fetchone()[0]
        report["total_visits_after"] = c.execute(
            "select count(*) from moz_historyvisits"
        ).fetchone()[0]
        report["all_visit_dates_verified"] = (
            c.execute("select count(*) from moz_historyvisits where visit_date<=0").fetchone()[0]
            == 0
        )
        c.commit()
        c.execute("pragma wal_checkpoint(truncate)")
        c.close()
        if source:
            source.close()
        return report
