"""Entirely invented browser records. Never load a developer's browser profile."""

import json
import sqlite3
from pathlib import Path

from arc2zen.jsonlz4 import write
from arc2zen.places import EPOCH

UNIX_US = 1700000000000000


def database(path, schema):
    with sqlite3.connect(path) as connection:
        connection.executescript(schema)
    connection.close()


def sidebar(path):
    items = {
        "pinned-root": {"childrenIds": ["pinned-tab"]},
        "unpinned-root": {"childrenIds": ["open-tab", "internal-tab"]},
        "essentials-root": {
            "childrenIds": ["essential-tab"],
            "data": {"itemContainer": {"containerType": {"topApps": {"_0": {"default": True}}}}},
        },
        "pinned-tab": {
            "title": "Pinned example",
            "data": {"tab": {"savedURL": "https://pinned.example.test/"}},
        },
        "open-tab": {"data": {"tab": {"savedURL": "https://open.example.test/"}}},
        "internal-tab": {"data": {"tab": {"savedURL": "chrome://settings/"}}},
        "essential-tab": {"data": {"tab": {"savedURL": "https://essential.example.test/"}}},
    }
    space = {
        "title": "Example workspace",
        "profile": {"default": True},
        "containerIDs": ["pinned", "pinned-root", "unpinned", "unpinned-root"],
    }
    data = {
        "sidebar": {
            "containers": [
                "local",
                {
                    "items": [v for pair in items.items() for v in pair],
                    "spaces": ["space-one", space],
                },
            ]
        },
        "firebaseSyncState": {
            "syncData": {
                "spaceModels": [
                    "space-one",
                    {
                        "value": {
                            "title": "Example workspace",
                            "customInfo": {"iconType": {"emoji_v2": "🌱"}},
                        }
                    },
                ]
            }
        },
    }
    path.write_text(json.dumps(data))


def make_profiles(root):
    root = Path(root)
    arc, zen = root / "Arc/User Data/Default", root / "Zen/Profile"
    arc.mkdir(parents=True)
    zen.mkdir(parents=True)
    side = root / "Arc/StorableSidebar.json"
    sidebar(side)
    database(
        arc / "History",
        f"""
        create table urls(id integer primary key,url text,title text,hidden integer,typed_count integer);
        create table visits(id integer primary key,url integer,visit_time integer,from_visit integer,transition integer);
        insert into urls values(1,'https://history.example.test/','History example',0,1),(2,'chrome://settings/','Internal',0,0);
        insert into visits values(1,1,{EPOCH + UNIX_US},0,1),(2,1,{EPOCH + UNIX_US},0,1),
            (3,1,{EPOCH + UNIX_US + 1000},1,0),(4,2,{EPOCH + UNIX_US},0,0);
    """,
    )
    bookmarks = {
        "roots": {
            "bookmark_bar": {
                "type": "folder",
                "guid": "source-root",
                "name": "Examples",
                "children": [
                    {
                        "type": "folder",
                        "guid": "folder",
                        "name": "Nested",
                        "children": [
                            {
                                "type": "url",
                                "guid": "bookmark",
                                "name": "Bookmark example",
                                "url": "https://bookmark.example.test/",
                            }
                        ],
                    }
                ],
            }
        }
    }
    (arc / "Bookmarks").write_text(json.dumps(bookmarks))
    database(
        arc / "Web Data",
        """
        create table keywords(short_name text,keyword text,url text,search_url_post_params text,
                              prepopulate_id integer,starter_pack_id integer,is_active integer);
        insert into keywords values('Example Search','ex','https://search.example.test/?q={searchTerms}','',0,0,1),
            ('POST search','post','https://search.example.test/','q={searchTerms}',0,0,1);
        create table addresses(guid text,use_count integer,use_date integer,date_modified integer);
        insert into addresses values('invented-address',4,1700000000,1700000000);
        create table address_type_tokens(guid text,type integer,value text);
        insert into address_type_tokens values('invented-address',7,'Example Person'),('invented-address',36,'US'),
            ('invented-address',77,'123 Example Street');
        create table autofill(name text,value text,count integer,date_created integer,date_last_used integer);
        insert into autofill values('example-field','invented form value',5,1699999999,1700000000),
            ('','empty field is skipped',1,1700000000,1700000000);
    """,
    )
    database(
        zen / "places.sqlite",
        """
        create table moz_origins(id integer primary key,prefix text,host text,frecency integer,
            recalc_frecency integer,recalc_alt_frecency integer,unique(prefix,host));
        create table moz_places(id integer primary key,url text unique,title text,rev_host text,hidden integer,
            typed integer,frecency integer,guid text unique,url_hash integer,origin_id integer references moz_origins(id),
            recalc_frecency integer,recalc_alt_frecency integer,visit_count integer default 0,
            last_visit_date integer,foreign_count integer default 0);
        create table moz_historyvisits(id integer primary key,from_visit integer,place_id integer references moz_places(id),
            visit_date integer,visit_type integer,session integer,source integer);
        create table moz_bookmarks(id integer primary key,type integer,fk integer references moz_places(id),parent integer,
            position integer,title text,dateAdded integer,lastModified integer,guid text unique,syncStatus integer,syncChangeCounter integer);
        insert into moz_bookmarks(id,type,position,title,guid) values(1,2,0,'Other Bookmarks','unfiled_____');
        create table moz_keywords(id integer primary key,place_id integer references moz_places(id));
    """,
    )
    database(
        zen / "formhistory.sqlite",
        """create table moz_formhistory(id integer primary key,fieldname text,
        value text,timesUsed integer,firstUsed integer,lastUsed integer,guid text unique);""",
    )
    database(
        zen / "cookies.sqlite",
        """
        pragma user_version=17;
        create table moz_cookies(id integer primary key,originAttributes text,name text,value text,host text,path text,
            expiry integer,lastAccessed integer,creationTime integer,isSecure integer,isHttpOnly integer,inBrowserElement integer,
            sameSite integer,schemeMap integer,isPartitionedAttributeSet integer,updateTime integer);
        insert into moz_cookies values(1,'','existing','existing-value','existing.example.test','/',9999999999999,
            1700000000000000,1700000000000000,1,1,0,1,2,0,1700000000000000);
    """,
    )
    database(
        arc / "Cookies",
        """
        create table meta(key text,value text); insert into meta values('version','24');
        create table cookies(name text,host_key text,path text,encrypted_value blob,is_persistent integer,expires_utc integer,
            top_frame_site_key text,has_cross_site_ancestor integer,source_scheme integer,samesite integer,is_secure integer,
            is_httponly integer,last_update_utc integer,creation_utc integer,last_access_utc integer);
    """,
    )
    write(
        zen / "zen-sessions.jsonlz4",
        {
            "spaces": [
                {"uuid": "existing-space", "name": "Existing workspace", "containerTabId": 0}
            ],
            "tabs": [
                {
                    "zenSyncId": "existing-tab",
                    "entries": [{"url": "https://existing.example.test/"}],
                }
            ],
            "folders": [],
            "groups": [],
            "splitViewData": {},
        },
    )
    write(
        zen / "sessionstore.jsonlz4",
        {
            "windows": [{"tabs": []}],
            "cookies": [
                {
                    "host": "existing.example.test",
                    "path": "/",
                    "name": "session-existing",
                    "value": "invented-session",
                }
            ],
        },
    )
    write(
        zen / "search.json.mozlz4",
        {"version": 1, "engines": [{"_name": "Existing", "_metaData": {"alias": "old"}}]},
    )
    (zen / "prefs.js").write_text('user_pref("existing.preference", true);\n')
    (zen / "logins.json").write_text('{"invented_password_store":true}')
    (zen / "key4.db").write_bytes(b"invented key store sentinel; not a real NSS database")
    (zen / "zen-keyboard-shortcuts.json").write_text(
        json.dumps(
            {
                "shortcuts": [
                    {"id": name, "key": "", "keycode": "", "modifiers": {}}
                    for name in ("zen-toggle-pin-tab", "zen-toggle-sidebar", "zen-duplicate-tab")
                ]
                + [{"id": "bookmark", "key": "d", "keycode": "", "modifiers": {"accel": True}}]
            }
        )
    )
    return arc, side, zen
