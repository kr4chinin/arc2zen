"""Prepare isolated candidates, validate drift, apply with backup, and restore."""

import configparser
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

from .common import digest, require, write_json

PARTS = {
    "spaces",
    "tabs",
    "history",
    "bookmarks",
    "addresses",
    "forms",
    "search",
    "cookies",
    "keyboard",
}
DEFAULT_PARTS = PARTS - {"cookies", "keyboard"}
FILES = {
    "spaces": {"zen-sessions.jsonlz4"},
    "tabs": {"zen-sessions.jsonlz4", "sessionstore.jsonlz4", "prefs.js"},
    "history": {"places.sqlite"},
    "bookmarks": {"places.sqlite"},
    "addresses": {"autofill-profiles.json", "prefs.js"},
    "forms": {"formhistory.sqlite"},
    "search": {"search.json.mozlz4"},
    "keyboard": {"zen-keyboard-shortcuts.json"},
    "cookies": {"cookies.sqlite", "sessionstore.jsonlz4", "prefs.js"},
}
ALLOWED_FILES = set.union(*FILES.values())
LOCKS = {"lock", ".parentlock", "parent.lock"}


def ensure_closed():
    require(sys.platform == "darwin", "Live migration currently supports macOS only")
    result = subprocess.run(
        ["/bin/ps", "-axo", "comm="], capture_output=True, text=True, check=True
    )
    running = any(
        Path(line.strip()).name in {"Arc", "zen", "zen-bin"}
        or "/Arc.app/Contents/" in line
        or "/Zen.app/Contents/" in line
        for line in result.stdout.splitlines()
    )
    require(
        not running,
        "Quit Arc and Zen completely with Cmd+Q, then retry. No browser is closed automatically.",
    )


def discover(home=None):
    support = (Path(home) if home else Path.home()) / "Library/Application Support"
    root = support / "zen"
    config = configparser.ConfigParser(interpolation=None)
    config.read(root / "profiles.ini")
    profiles = []
    for section in config.sections():
        if section.startswith("Profile") and config.has_option(section, "Path"):
            path = Path(config.get(section, "Path"))
            if config.get(section, "IsRelative", fallback="1") == "1":
                path = root / path
            profiles.append(
                {
                    "name": config.get(section, "Name", fallback=section),
                    "path": str(path.resolve()),
                    "exists": path.is_dir(),
                }
            )
    return {
        "arc_profile": str(support / "Arc/User Data/Default"),
        "arc_sidebar": str(support / "Arc/StorableSidebar.json"),
        "zen_profiles": profiles,
    }


def regular(path):
    require(not path.is_symlink(), "Symlinked browser files are unsupported")
    hints = {
        "formhistory.sqlite": "Use form autofill in Zen first, or omit forms with --parts.",
        "search.json.mozlz4": "Use search in Zen first, or omit search with --parts.",
    }
    require(
        path.is_file(),
        f"Missing required file: {path.name}. "
        + hints.get(path.name, "Open and close the browser normally first."),
    )


def snapshot_db(source, destination):
    regular(source)
    # Even mode=ro may create WAL/SHM beside the source. SQLite only opens our
    # private copy; raw copies retain WAL/journal records without touching profiles.
    with tempfile.TemporaryDirectory(prefix="arc2zen-snapshot-") as directory:
        copied = Path(directory) / source.name
        shutil.copyfile(source, copied)
        for suffix in ("-wal", "-journal"):
            sidecar = source.with_name(source.name + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                regular(sidecar)
                shutil.copyfile(sidecar, copied.with_name(copied.name + suffix))
        with (
            closing(sqlite3.connect(copied)) as source_db,
            closing(sqlite3.connect(destination)) as target_db,
        ):
            source_db.backup(target_db)
            require(
                target_db.execute("pragma integrity_check").fetchone()[0] == "ok",
                "SQLite snapshot failed integrity check",
            )
            target_db.execute("pragma wal_checkpoint(truncate)")
    os.chmod(destination, 0o600)


def fingerprint(profile, names):
    result = {}
    for name in sorted(names):
        result[name] = digest(profile / name)
        if name.endswith(".sqlite"):
            result[name + "-wal"] = digest(profile / (name + "-wal"))
    return result


def private_location(run, *profiles):
    run = run.expanduser().resolve()
    require(
        not any((parent / ".git").exists() for parent in (run, *run.parents)),
        "Migration data must be stored outside Git repositories",
    )
    for profile in profiles:
        require(
            not run.is_relative_to(profile) and not profile.is_relative_to(run),
            "Run directories and browser profiles must not contain one another",
        )
    return run


def prepare(arc, sidebar, profile, run, parts):
    ensure_closed()
    require(parts, "Select at least one migration part with --parts")
    require(parts <= PARTS, "Unknown migration part; supported parts: " + ",".join(sorted(PARTS)))
    arc, sidebar, profile = (
        arc.expanduser().resolve(),
        sidebar.expanduser().resolve(),
        profile.expanduser().resolve(),
    )
    require(arc.name == "Default", "Only Arc's Default profile is supported in this release")
    require(
        arc.is_dir() and profile.is_dir() and arc != profile,
        "Select existing, different Arc and Zen profile directories",
    )
    require(
        not arc.is_relative_to(profile) and not profile.is_relative_to(arc),
        "Profiles must not contain one another",
    )
    run = private_location(run, arc, profile)
    require(not run.exists(), "Run directory already exists; choose a new output directory")
    run.mkdir(mode=0o700, parents=True)
    candidate, source = run / "candidate", run / "source"
    candidate.mkdir(mode=0o700)
    source.mkdir(mode=0o700)
    write_json(candidate / ".arc2zen-candidate.json", {"format": 1, "profile": str(candidate)})
    selected = set.union(*(FILES[p] for p in parts))
    needed = selected | ({"zen-sessions.jsonlz4"} if "cookies" in parts else set())
    protected = {"logins.json", "key4.db"} | (
        {"cookies.sqlite"} if "cookies" not in parts else set()
    )
    baseline = fingerprint(profile, needed | protected)
    write_json(
        run / "manifest.json",
        {"format": 1, "status": "preparing", "profile": str(profile)},
    )
    for name in sorted(needed):
        path = profile / name
        if name == "autofill-profiles.json" and not path.exists():
            continue
        regular(path)
        if name.endswith(".sqlite"):
            snapshot_db(path, candidate / name)
        else:
            shutil.copy2(path, candidate / name)
            os.chmod(candidate / name, 0o600)
    source_files = {}
    if parts & {"spaces", "tabs"}:
        source_files["Sidebar.json"] = sidebar
    if "history" in parts:
        source_files["History.sqlite"] = arc / "History"
    if "bookmarks" in parts:
        source_files["Bookmarks.json"] = arc / "Bookmarks"
    if parts & {"addresses", "forms", "search"}:
        source_files["Web Data.sqlite"] = arc / "Web Data"
    if "cookies" in parts:
        source_files["Cookies.sqlite"] = arc / "Cookies"
    source_names = set(source_files.values())
    source_before = {str(p): digest(p) for p in source_names}
    source_wal_before = {
        str(p): digest(Path(str(p) + "-wal"))
        for p in source_names
        if p.name in {"History", "Web Data", "Cookies"}
    }
    for name, path in source_files.items():
        regular(path)
        if name.endswith(".sqlite"):
            snapshot_db(path, source / name)
        else:
            shutil.copy2(path, source / name)
            os.chmod(source / name, 0o600)
    require(
        source_before == {str(p): digest(p) for p in source_names},
        "Arc changed during snapshot; prepare a new run",
    )
    require(
        all(digest(Path(path + "-wal")) == value for path, value in source_wal_before.items()),
        "Arc WAL changed during snapshot; prepare a new run",
    )
    report = {}
    if parts & {"spaces", "tabs"}:
        from .sidebar import convert

        report["sidebar"] = convert(
            source, candidate, spaces="spaces" in parts, tabs="tabs" in parts
        )
    if parts & {"history", "bookmarks"}:
        from .places import convert

        report["places"] = convert(
            source,
            candidate,
            history="history" in parts,
            bookmarks="bookmarks" in parts,
        )
    if parts & {"addresses", "forms", "search", "keyboard"}:
        from .extras import convert

        report["extras"] = convert(source, candidate, parts)
    if "cookies" in parts:
        from .cookies import convert

        print(
            "Reading Arc Safe Storage. Approve the macOS Keychain dialog if it appears.",
            flush=True,
        )
        report["cookies"] = convert(source / "Cookies.sqlite", candidate)
    ensure_closed()
    require(
        fingerprint(profile, needed | protected) == baseline,
        "Zen changed during preparation; prepare a new run",
    )
    changes = {
        name: {"before": baseline[name], "candidate": digest(candidate / name)}
        for name in sorted(selected)
    }
    require(
        all(value["candidate"] is not None for value in changes.values()),
        "Candidate is incomplete",
    )
    manifest = {
        "format": 1,
        "status": "prepared",
        "profile": str(profile),
        "parts": sorted(parts),
        "baseline": baseline,
        "changes": changes,
        "report": report,
        "created": datetime.now(UTC).isoformat(),
    }
    write_json(run / "manifest.json", manifest)
    write_json(run / "report.json", report)
    return run, report


def load_run(run, status):
    run = run.expanduser().resolve()
    manifest = json.loads((run / "manifest.json").read_text())
    require(
        manifest.get("format") == 1 and manifest.get("status") in status,
        "Run is not in the required state",
    )
    require(
        manifest.get("changes") and set(manifest["changes"]) <= ALLOWED_FILES,
        "Unsupported manifest file list",
    )
    require(
        all(
            name in ALLOWED_FILES
            or (name.endswith("-wal") and name[:-4] in ALLOWED_FILES)
            or name in {"logins.json", "key4.db"}
            for name in manifest["baseline"]
        ),
        "Invalid baseline file list",
    )
    profile = Path(manifest["profile"])
    require(profile.is_absolute() and profile.is_dir(), "Target profile no longer exists")
    private_location(run, profile)
    return run, manifest, profile


def backup_profile(profile, destination):
    # Retain the complete profile, including extensions/passwords, excluding only process locks.
    def ignore(_directory, names):
        return LOCKS & set(names)

    shutil.copytree(profile, destination, ignore=ignore, symlinks=True)
    os.chmod(destination, 0o700)


def install_file(source, target):
    require(not target.is_symlink(), "Refusing to replace a symlinked profile file")
    if target.name.endswith(".sqlite"):
        snapshot_db(source, target)
    else:
        descriptor, name = tempfile.mkstemp(
            prefix="." + target.name + ".arc2zen-", dir=target.parent
        )
        os.close(descriptor)
        temporary = Path(name)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def restore_files(backup, profile, changes):
    for name, item in changes.items():
        if item["before"] is None:
            (profile / name).unlink(missing_ok=True)
        else:
            regular(backup / name)
            install_file(backup / name, profile / name)


def apply(run):
    ensure_closed()
    run, manifest, profile = load_run(run, {"prepared"})
    with profile_lock(profile):
        return apply_locked(run, manifest, profile)


def apply_locked(run, manifest, profile):
    run, manifest, profile = load_run(run, {"prepared"})
    require(
        fingerprint(
            profile,
            {name for name in manifest["baseline"] if not name.endswith("-wal")},
        )
        == manifest["baseline"],
        "Profile changed since prepare; prepare a new run before applying",
    )
    for name, item in manifest["changes"].items():
        require(
            digest(run / "candidate" / name) == item["candidate"],
            "Candidate changed since prepare",
        )
    backup = run / "backup"
    require(not backup.exists(), "Backup already exists; this run cannot be applied again")
    backup_profile(profile, backup)
    ensure_closed()
    require(
        fingerprint(
            profile,
            {name for name in manifest["baseline"] if not name.endswith("-wal")},
        )
        == manifest["baseline"],
        "Profile changed during backup; live profile was not modified",
    )
    manifest["status"] = "applying"
    write_json(run / "manifest.json", manifest)
    try:
        for name in manifest["changes"]:
            install_file(run / "candidate" / name, profile / name)
        for name in ("logins.json", "key4.db"):
            require(
                digest(profile / name) == manifest["baseline"][name],
                "Password files changed during apply",
            )
        if "cookies" not in manifest["parts"]:
            require(
                fingerprint(profile, {"cookies.sqlite"})
                == {
                    k: v for k, v in manifest["baseline"].items() if k.startswith("cookies.sqlite")
                },
                "Cookies changed during apply",
            )
        manifest["applied"] = fingerprint(profile, set(manifest["changes"]))
        manifest["status"] = "applied"
        write_json(run / "manifest.json", manifest)
    except BaseException:
        restore_files(backup, profile, manifest["changes"])
        manifest["status"] = "rolled_back"
        write_json(run / "manifest.json", manifest)
        raise
    return {
        "status": "applied",
        "changed_files": sorted(manifest["changes"]),
        "backup": str(backup),
    }


def restore(run, *, discard_new_data=False):
    ensure_closed()
    run, manifest, profile = load_run(run, {"applied", "applying", "restoring"})
    with profile_lock(profile):
        return restore_locked(run, manifest, profile, discard_new_data=discard_new_data)


def restore_locked(run, manifest, profile, *, discard_new_data):
    run, manifest, profile = load_run(run, {"applied", "applying", "restoring"})
    require(
        discard_new_data or manifest["status"] == "applied",
        "This run was interrupted during apply/restore. Keep both browsers closed, review backup/, "
        "then recover with restore --run <run> --discard-new-data.",
    )
    current = fingerprint(profile, set(manifest["changes"]))
    require(
        discard_new_data or current == manifest.get("applied"),
        "Profile files differ from the recorded import state, possibly after another restore. "
        "Restore would discard newer data; review the backup and use --discard-new-data explicitly.",
    )
    for name, item in manifest["changes"].items():
        if item["before"] is not None:
            require(
                digest(run / "backup" / name) == item["before"],
                "Backup file changed; use manual recovery",
            )
            if name.endswith(".sqlite"):
                require(
                    digest(run / "backup" / (name + "-wal"))
                    == manifest["baseline"].get(name + "-wal"),
                    "Backup WAL changed; use manual recovery",
                )
    rescue = run / ("before-restore-" + uuid.uuid4().hex[:8])
    backup_profile(profile, rescue)
    ensure_closed()
    require(
        fingerprint(profile, set(manifest["changes"])) == current,
        "Profile changed during restore backup",
    )
    manifest["status"] = "restoring"
    write_json(run / "manifest.json", manifest)
    restore_files(run / "backup", profile, manifest["changes"])
    manifest["status"] = "restored"
    write_json(run / "manifest.json", manifest)
    return {"status": "restored", "rescue_backup": str(rescue)}


def default_run():
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return (
        Path.home()
        / "Library/Application Support/arc2zen/runs"
        / (stamp + "-" + uuid.uuid4().hex[:8])
    )


@contextmanager
def profile_lock(profile):
    # Cooperating CLI processes share a lock outside both the checkout and profile.
    directory = Path(tempfile.gettempdir()) / f"arc2zen-locks-{os.getuid()}"
    directory.mkdir(mode=0o700, exist_ok=True)
    require(
        not directory.is_symlink()
        and directory.stat().st_uid == os.getuid()
        and directory.stat().st_mode & 0o077 == 0,
        "Unsafe migration lock directory",
    )
    identity = hashlib.sha256(str(profile.resolve()).encode()).hexdigest()
    descriptor = os.open(directory / identity, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            require(False, "Another Arc to Zen apply/restore is using this profile")
        yield
    finally:
        os.close(descriptor)
