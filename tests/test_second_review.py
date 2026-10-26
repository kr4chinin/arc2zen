"""Synthetic regressions from the Muse review; no home/browser/Keychain access."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fixtures import make_profiles

from arc2zen import cli, cookies, workflow
from arc2zen.common import MigrationError, write_json


class SecondReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.arc, self.side, self.zen = make_profiles(self.root)
        guard = patch("arc2zen.workflow.ensure_closed")
        guard.start()
        self.addCleanup(guard.stop)

    def prepare(self, parts=None):
        return workflow.prepare(
            self.arc, self.side, self.zen, self.root / "run", parts or {"addresses"}
        )[0]

    def test_interrupted_json_replacement_leaves_previous_manifest_readable(self):
        path = self.root / "state.json"
        before = {"status": "applying", "invented": True}
        write_json(path, before)
        with patch("arc2zen.common.os.replace", side_effect=OSError("invented disk failure")):
            with self.assertRaises(OSError):
                write_json(path, {"status": "applied"})
        self.assertEqual(json.loads(path.read_text()), before)
        self.assertEqual(list(self.root.glob(".state.json*")), [])

    def test_partial_backup_can_be_retried_without_repreparing(self):
        run = self.prepare()
        original = workflow.backup_profile
        baseline = workflow.fingerprint(self.zen, workflow.ALLOWED_FILES)

        def interrupted(profile, destination):
            destination.mkdir()
            (destination / "partial.txt").write_text("invented partial backup")
            raise KeyboardInterrupt()

        with patch("arc2zen.workflow.backup_profile", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                workflow.apply(run)
        self.assertEqual(workflow.fingerprint(self.zen, workflow.ALLOWED_FILES), baseline)
        self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "prepared")
        with patch("arc2zen.workflow.backup_profile", wraps=original):
            self.assertEqual(workflow.apply(run)["status"], "applied")
        self.assertTrue((run / "backup/logins.json").is_file())
        workflow.restore(run)

    def test_existing_unused_backup_is_preserved_when_retrying_prepared_run(self):
        run = self.prepare()
        (run / "backup").mkdir()
        (run / "backup/partial.txt").write_text("invented previous attempt")
        self.assertEqual(workflow.apply(run)["status"], "applied")
        self.assertTrue(
            any(
                p.read_text() == "invented previous attempt"
                for p in run.glob("backup-unused-*/partial.txt")
            )
        )

    def test_load_run_reports_corrupt_missing_and_interrupted_states(self):
        with self.assertRaisesRegex(MigrationError, "manifest"):
            workflow.load_run(self.root / "missing", {"prepared"})
        run = self.prepare()
        manifest_path = run / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest_path.write_text('{"status":')
        with self.assertRaisesRegex(MigrationError, "manifest.*manual"):
            workflow.load_run(run, {"applied"})
        manifest["status"] = "applying"
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(MigrationError, "interrupted.*restore"):
            workflow.apply(run)

    def test_prepare_cannot_snapshot_a_profile_held_by_another_operation(self):
        with workflow.profile_lock(self.zen):
            with self.assertRaisesRegex(MigrationError, "Another Arc to Zen"):
                self.prepare()
        self.assertFalse((self.root / "run").exists())

    def test_backup_excludes_only_top_level_process_locks(self):
        (self.zen / "lock").write_text("invented process lock")
        nested = self.zen / "storage/example.test"
        nested.mkdir(parents=True)
        (nested / "lock").write_text("invented application data")
        backup = self.root / "backup"
        workflow.backup_profile(self.zen, backup)
        self.assertFalse((backup / "lock").exists())
        self.assertEqual(
            (backup / "storage/example.test/lock").read_text(), "invented application data"
        )

    def test_discover_marks_install_default_and_cli_does_not_pick_a_spare(self):
        home = self.root / "invented-home"
        support = home / "Library/Application Support/zen"
        support.mkdir(parents=True)
        missing_default = support / "Profiles/missing-default"
        (support / "profiles.ini").write_text(
            "[Profile0]\nName=Main\nIsRelative=1\nPath=Profiles/missing-default\nDefault=1\n"
            f"[Profile1]\nName=Spare\nIsRelative=0\nPath={self.zen}\n"
        )
        (support / "installs.ini").write_text(
            "[INVENTED_INSTALL]\nDefault=Profiles/missing-default\n"
        )
        found = workflow.discover(home)
        self.assertEqual(
            [p["path"] for p in found["zen_profiles"]], [str(missing_default), str(self.zen)]
        )
        self.assertEqual([p["default"] for p in found["zen_profiles"]], [True, False])
        self.assertEqual([p["exists"] for p in found["zen_profiles"]], [False, True])
        with (
            patch("arc2zen.workflow.discover", return_value=found),
            patch("sys.stderr", io.StringIO()),
        ):
            result = cli.main(
                [
                    "prepare",
                    "--arc-profile",
                    str(self.arc),
                    "--arc-sidebar",
                    str(self.side),
                    "--output",
                    str(self.root / "run"),
                    "--parts",
                    "addresses",
                ]
            )
        self.assertEqual(result, 1)
        self.assertFalse((self.root / "run").exists())

    def test_non_web_pinned_and_essential_tabs_are_counted_and_skipped(self):
        data = json.loads(self.side.read_text())
        items = data["sidebar"]["containers"][1]["items"]
        items[items.index("pinned-tab") + 1]["data"]["tab"]["savedURL"] = (
            "file:///tmp/invented.html"
        )
        items[items.index("essential-tab") + 1]["data"]["tab"]["savedURL"] = "about:blank"
        self.side.write_text(json.dumps(data))
        run = self.prepare({"spaces", "tabs", "bookmarks"})
        report = json.loads((run / "report.json").read_text())
        self.assertEqual(report["sidebar"]["unsupported_pinned_tabs_skipped"], 1)
        self.assertEqual(report["sidebar"]["unsupported_essentials_skipped"], 1)
        self.assertEqual(report["sidebar"]["open_tabs_added"], 1)
        self.assertEqual(report["places"]["bookmarks_added"], 1)

    def test_bookmark_null_dates_use_the_existing_fallback(self):
        path = self.arc / "Bookmarks"
        data = json.loads(path.read_text())
        bookmark = data["roots"]["bookmark_bar"]["children"][0]["children"][0]
        bookmark.update({"date_added": None, "date_modified": None})
        path.write_text(json.dumps(data))
        run = self.prepare({"bookmarks"})
        with closing(sqlite3.connect(run / "candidate/places.sqlite")) as db:
            added, modified = db.execute(
                "select dateAdded,lastModified from moz_bookmarks where title='Bookmark example'"
            ).fetchone()
        self.assertGreater(added, 0)
        self.assertEqual(added, modified)

    def test_invalid_cookie_metadata_does_not_become_negative_unix_times(self):
        with closing(sqlite3.connect(self.arc / "Cookies")) as db, db:
            db.execute(
                "insert into cookies values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "sid",
                    ".example.test",
                    "/",
                    b"invented ciphertext",
                    1,
                    cookies.EPOCH_US + 2_000_000_000_123_000,
                    "",
                    0,
                    2,
                    1,
                    1,
                    1,
                    0,
                    0,
                    0,
                ),
            )
        before = workflow.fingerprint(self.zen, workflow.ALLOWED_FILES)
        with (
            patch("arc2zen.cookies.read_key", return_value=b"invented key"),
            patch("arc2zen.cookies.decrypt", return_value="invented value"),
        ):
            with self.assertRaisesRegex(MigrationError, "timestamp"):
                self.prepare({"cookies"})
        self.assertEqual(workflow.fingerprint(self.zen, workflow.ALLOWED_FILES), before)
