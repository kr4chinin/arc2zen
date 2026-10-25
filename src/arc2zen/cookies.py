"""Private local Arc -> Zen cookie conversion, schema 24 -> 17.

Reads only an encrypted Arc snapshot; writes only an explicitly supplied copy
of Zen. Keychain secret and decrypted values are never printed or exported.
Session cookies remain session cookies in Firefox's session store.
"""

import ctypes
import hashlib
import sqlite3
import subprocess
import time
from contextlib import ExitStack, closing
from urllib.parse import quote_plus, urlsplit

from .common import require, require_candidate, set_pref
from .jsonlz4 import decode, encode

EPOCH_US = 11644473600 * 1000000
SAMESITE = {-1: 256, 0: 0, 1: 1, 2: 2}
CC = None


def crypto():
    global CC
    if CC is None:
        CC = ctypes.CDLL("/usr/lib/system/libcommonCrypto.dylib")
        CC.CCCrypt.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        CC.CCCrypt.restype = ctypes.c_int
    return CC


KEY_CACHE = None


def read_key():
    global KEY_CACHE
    if KEY_CACHE is not None:
        return KEY_CACHE
    r = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-w", "-s", "Arc Safe Storage"],
        capture_output=True,
        check=False,
        timeout=180,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Keychain access unavailable (exit {r.returncode}); no values logged")
    password = r.stdout.rstrip(b"\r\n")
    require(password, "Empty Keychain secret")
    KEY_CACHE = hashlib.pbkdf2_hmac("sha1", password, b"saltysalt", 1003, 16)
    return KEY_CACHE


def decrypt(row, key):
    encrypted = row["encrypted_value"]
    require(encrypted[:3] == b"v10", "Unexpected encryption format")
    ciphertext = encrypted[3:]
    require(
        ciphertext and len(ciphertext) % 16 == 0,
        "Cookie schema, partition, or integrity validation failed",
    )
    output = ctypes.create_string_buffer(len(ciphertext) + 16)
    written = ctypes.c_size_t()
    result = crypto().CCCrypt(
        1,
        0,
        1,
        key,
        len(key),
        b" " * 16,
        ciphertext,
        len(ciphertext),
        output,
        len(output),
        ctypes.byref(written),
    )
    require(result == 0, "AES decrypt failed")
    plaintext = output.raw[: written.value]
    expected_hash = hashlib.sha256(row["host_key"].encode()).digest()
    require(plaintext[:32] == expected_hash, "Cookie host integrity check failed")
    return plaintext[32:].decode("utf-8", errors="strict")


def partition(row):
    if not row["top_frame_site_key"]:
        return ("", {})
    site = urlsplit(row["top_frame_site_key"])
    require(
        site.scheme in ("http", "https") and site.hostname,
        "Cookie schema, partition, or integrity validation failed",
    )
    require(
        site.path in ("", "/") and (not site.query) and (not site.fragment),
        "Cookie schema, partition, or integrity validation failed",
    )
    require(
        not site.username and (not site.password) and (site.port is None),
        "Cookie schema, partition, or integrity validation failed",
    )
    host = site.hostname
    if ":" in host:
        host = "[" + host + "]"
    key = f"({site.scheme},{host}"
    if row["has_cross_site_ancestor"]:
        key += ",f"
    key += ")"
    attrs = {"partitionKey": key}
    return origin_suffix(attrs), attrs


def origin_suffix(attrs):
    if isinstance(attrs, str):
        require(not attrs or attrs.startswith("^"), "Unsupported session origin suffix")
        return attrs
    require(isinstance(attrs, dict), "Unsupported session origin attributes")
    supported = {"userContextId", "privateBrowsingId", "firstPartyDomain", "partitionKey"}
    require(
        all(name in supported or not value for name, value in attrs.items()),
        "Unsupported nonempty session origin attribute; no cookies were applied",
    )
    require(
        not attrs.get("userContextId") and not attrs.get("privateBrowsingId"),
        "Container/private cookie mapping is not supported",
    )
    # Match OriginAttributes::CreateSuffix ordering and string sanitization.
    values = []
    for name in ("firstPartyDomain", "partitionKey"):
        value = attrs.get(name, "")
        if value:
            require(isinstance(value, str), "Unsupported session origin attribute type")
            encoded = quote_plus(value.replace(":", "+"), safe="").replace("*", "%2A")
            values.append(name + "=" + encoded)
    return "^" + "&".join(values) if values else ""


def cookie_key(cookie):
    return (
        cookie.get("name", ""),
        cookie.get("host", ""),
        cookie.get("path", ""),
        origin_suffix(cookie.get("originAttributes", {})),
    )


def convert(source, target):
    require_candidate(target)
    with ExitStack() as stack:
        db = target / "cookies.sqlite"
        session_file = target / "sessionstore.jsonlz4"
        session = decode(session_file.read_bytes())
        zen = decode((target / "zen-sessions.jsonlz4").read_bytes())
        require(
            all(w.get("containerTabId", 0) == 0 for w in zen["spaces"]),
            "Container mapping required",
        )
        c = stack.enter_context(closing(sqlite3.connect(db)))
        require(
            c.execute("pragma user_version").fetchone()[0] == 17,
            "Cookie schema, partition, or integrity validation failed",
        )
        source_db = stack.enter_context(
            closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True))
        )
        source_db.row_factory = sqlite3.Row
        require(
            source_db.execute("select value from meta where key='version'").fetchone()[0] == "24",
            "Cookie schema, partition, or integrity validation failed",
        )
        rows = source_db.execute("select * from cookies").fetchall()
        source_db.close()
        key = read_key()
        report = {
            "source_total": len(rows),
            "expired_skipped": 0,
            "unsupported_extension_partition_skipped": 0,
            "decrypted_and_host_verified": 0,
            "format_collisions": 0,
            "persistent_imported": 0,
            "session_imported": 0,
            "partitioned_imported": 0,
            "existing_persistent_before": c.execute("select count(*) from moz_cookies").fetchone()[
                0
            ],
        }
        selected = {}
        for row in rows:
            if row["is_persistent"] and row["expires_utc"] <= EPOCH_US + time.time_ns() // 1000:
                report["expired_skipped"] += 1
                continue
            if (
                row["top_frame_site_key"]
                and urlsplit(row["top_frame_site_key"]).scheme == "chrome-extension"
            ):
                report["unsupported_extension_partition_skipped"] += 1
                continue
            value = decrypt(row, key)
            report["decrypted_and_host_verified"] += 1
            suffix, attrs = partition(row)
            identity = (row["name"], row["host_key"], row["path"], suffix)
            require(
                row["path"].startswith("/") and row["source_scheme"] in (0, 1, 2),
                "Cookie schema, partition, or integrity validation failed",
            )
            same_site = SAMESITE[row["samesite"]]
            converted = {
                "name": row["name"],
                "host": row["host_key"],
                "path": row["path"],
                "value": value,
                "secure": bool(row["is_secure"]),
                "httponly": bool(row["is_httponly"]),
                "originAttributes": attrs,
                "sameSite": same_site,
                "schemeMap": row["source_scheme"],
                "isPartitioned": bool(attrs),
            }
            if row["is_persistent"]:
                converted["expiry"] = (row["expires_utc"] - EPOCH_US) // 1000
            item = (row, converted)
            if identity in selected:
                report["format_collisions"] += 1
                previous = selected[identity][0]
                if (row["last_update_utc"], row["creation_utc"], row["source_scheme"]) <= (
                    previous["last_update_utc"],
                    previous["creation_utc"],
                    previous["source_scheme"],
                ):
                    continue
            selected[identity] = item
        del key
        sessions = [
            cookie for cookie in session.get("cookies", []) if cookie_key(cookie) not in selected
        ]
        for identity, (row, converted) in selected.items():
            name, host, path, suffix = identity
            c.execute(
                "delete from moz_cookies where name=? and host=? and path=? and originAttributes=?",
                identity,
            )
            if not row["is_persistent"]:
                sessions.append(converted)
                report["session_imported"] += 1
            else:
                c.execute(
                    "insert into moz_cookies\n                (originAttributes,name,value,host,path,expiry,lastAccessed,creationTime,isSecure,isHttpOnly,\n                 inBrowserElement,sameSite,schemeMap,isPartitionedAttributeSet,updateTime)\n                values (?,?,?,?,?,?,?,?,?,?,0,?,?,?,?)",
                    (
                        suffix,
                        name,
                        converted["value"],
                        host,
                        path,
                        converted["expiry"],
                        row["last_access_utc"] - EPOCH_US,
                        row["creation_utc"] - EPOCH_US,
                        row["is_secure"],
                        row["is_httponly"],
                        converted["sameSite"],
                        row["source_scheme"],
                        bool(suffix),
                        max(
                            row["last_update_utc"] - EPOCH_US,
                            row["creation_utc"] - EPOCH_US,
                        ),
                    ),
                )
                report["persistent_imported"] += 1
            report["partitioned_imported"] += bool(suffix)
        c.commit()
        require(
            c.execute("pragma integrity_check").fetchone()[0] == "ok",
            "Cookie schema, partition, or integrity validation failed",
        )
        c.execute("pragma wal_checkpoint(truncate)")
        report["persistent_after"] = c.execute("select count(*) from moz_cookies").fetchone()[0]
        for identity, (row, cookie) in selected.items():
            if not row["is_persistent"]:
                require(
                    cookie in sessions,
                    "Cookie schema, partition, or integrity validation failed",
                )
                continue
            got = c.execute(
                "select value,expiry,isSecure,isHttpOnly,sameSite,schemeMap,isPartitionedAttributeSet from moz_cookies where name=? and host=? and path=? and originAttributes=?",
                identity,
            ).fetchone()
            require(
                got
                == (
                    cookie["value"],
                    cookie["expiry"],
                    row["is_secure"],
                    row["is_httponly"],
                    cookie["sameSite"],
                    row["source_scheme"],
                    bool(cookie["originAttributes"]),
                ),
                "Cookie schema, partition, or integrity validation failed",
            )
        c.close()
        session["cookies"] = sessions
        output = encode(session)
        require(
            decode(output)["cookies"] == sessions,
            "Cookie schema, partition, or integrity validation failed",
        )
        session_file.write_bytes(output)
        report["session_after"] = len(sessions)
        report["exact_value_and_flags_check"] = "PASS"
        report["key_written_to_disk"] = False
        set_pref(target, "network.cookie.maxNumber", max(5000, report["persistent_after"] + 1000))
        set_pref(target, "browser.sessionstore.resume_session_once", True)
        return report
