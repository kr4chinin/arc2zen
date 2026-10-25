"""Reproductions for confirmed independent-review findings, with invented data."""

import io
import json
import os
import shlex
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fixtures import UNIX_US, make_profiles

from arc2zen import cli, cookies, jsonlz4, workflow
from arc2zen.common import MigrationError, digest


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.arc, self.side, self.zen = make_profiles(self.root)
        guard = patch("arc2zen.workflow.ensure_closed")
        guard.start()
        self.addCleanup(guard.stop)

    def prepare(self, parts, name="run"):
        return workflow.prepare(self.arc, self.side, self.zen, self.root / name, parts)[0]

    def source_cookie(self, *, persistent=0, expiry=0):
        timestamp = cookies.EPOCH_US + UNIX_US
        with closing(sqlite3.connect(self.arc / "Cookies")) as db, db:
            db.execute(
                "insert into cookies values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "sid",
                    ".example.test",
                    "/",
                    b"invented ciphertext",
                    persistent,
                    expiry,
                    "",
                    0,
                    2,
                    1,
                    1,
                    1,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )

    def test_first_party_session_cookie_is_not_replaced_by_unpartitioned_arc_cookie(self):
        path = self.zen / "sessionstore.jsonlz4"
        session = jsonlz4.read(path)
        isolated = {
            "host": ".example.test",
            "path": "/",
            "name": "sid",
            "value": "invented isolated session",
            "originAttributes": {"firstPartyDomain": "first.example.test"},
        }
        session["cookies"].append(isolated)
        jsonlz4.write(path, session)
        self.source_cookie()
        with (
            patch("arc2zen.cookies.read_key", return_value=b"invented key"),
            patch("arc2zen.cookies.decrypt", return_value="invented Arc session"),
        ):
            run = self.prepare({"cookies"})
        result = jsonlz4.read(run / "candidate/sessionstore.jsonlz4")["cookies"]
        self.assertIn(isolated, result)
        self.assertEqual(len(result), 3)
        self.assertEqual(cookies.cookie_key(isolated)[-1], "^firstPartyDomain=first.example.test")

    def test_unknown_session_origin_attribute_stops_preparation(self):
        path = self.zen / "sessionstore.jsonlz4"
        session = jsonlz4.read(path)
        session["cookies"][0]["originAttributes"] = {"futureContext": "invented context"}
        jsonlz4.write(path, session)
        before = workflow.fingerprint(self.zen, workflow.ALLOWED_FILES)
        with patch("arc2zen.cookies.read_key", return_value=b"invented key"):
            with self.assertRaises(MigrationError):
                self.prepare({"cookies"})
        self.assertEqual(workflow.fingerprint(self.zen, workflow.ALLOWED_FILES), before)

    def test_reimport_appends_new_bookmarks_without_displacing_zen_siblings(self):
        workflow.apply(self.prepare({"bookmarks"}))
        with closing(sqlite3.connect(self.zen / "places.sqlite")) as db, db:
            parent = db.execute("select id from moz_bookmarks where title='Nested'").fetchone()[0]
            db.execute(
                "insert into moz_bookmarks(type,parent,position,title,guid) values(2,?,1,?,?)",
                (parent, "Local Zen folder", "localGuid___"),
            )
        path = self.arc / "Bookmarks"
        data = json.loads(path.read_text())
        data["roots"]["bookmark_bar"]["children"][0]["children"].insert(
            0,
            {
                "type": "url",
                "guid": "new-arc-bookmark",
                "name": "New Arc bookmark",
                "url": "https://new.example.test/",
            },
        )
        path.write_text(json.dumps(data))
        run = self.prepare({"bookmarks"}, name="repeat")
        with closing(sqlite3.connect(run / "candidate/places.sqlite")) as db:
            rows = db.execute(
                "select title,position from moz_bookmarks where parent=? order by position,id",
                (parent,),
            ).fetchall()
        self.assertEqual(
            rows, [("Bookmark example", 0), ("Local Zen folder", 1), ("New Arc bookmark", 2)]
        )

    def test_address_line_three_and_unmapped_token_reporting(self):
        with closing(sqlite3.connect(self.arc / "Web Data")) as db, db:
            db.execute("delete from address_type_tokens where type=77")
            db.executemany(
                "insert into address_type_tokens values('invented-address',?,?)",
                [
                    (30, "123 Example Street"),
                    (31, "Example building"),
                    (83, "Third address line"),
                    (76, "Invented non-address token"),
                    (999, "Invented unknown token"),
                ],
            )
        run = self.prepare({"addresses"})
        address = json.loads((run / "candidate/autofill-profiles.json").read_text())["addresses"][0]
        self.assertEqual(
            address["street-address"], "123 Example Street\nExample building\nThird address line"
        )
        report = json.loads((run / "report.json").read_text())["extras"]
        self.assertEqual(report["address_tokens_unmapped"], 2)
        self.assertEqual(report["address_token_types_unmapped"], [76, 999])
        self.assertNotIn("Invented unknown token", json.dumps(report))

    def test_cookie_expiry_is_milliseconds_and_other_times_are_microseconds(self):
        expected_ms = 2_000_000_000_123
        self.source_cookie(persistent=1, expiry=cookies.EPOCH_US + expected_ms * 1000)
        with (
            patch("arc2zen.cookies.read_key", return_value=b"invented key"),
            patch("arc2zen.cookies.decrypt", return_value="invented persistent value"),
        ):
            run = self.prepare({"cookies"})
        with closing(sqlite3.connect(run / "candidate/cookies.sqlite")) as db:
            row = db.execute(
                "select expiry,creationTime,lastAccessed,updateTime from moz_cookies where name='sid'"
            ).fetchone()
        self.assertEqual(row, (expected_ms, UNIX_US, UNIX_US, UNIX_US))

    def test_closed_wal_databases_are_snapshotted_without_creating_source_sidecars(self):
        for path in (self.arc / "History", self.zen / "places.sqlite"):
            with closing(sqlite3.connect(path)) as db:
                db.execute("pragma journal_mode=wal")
            self.assertFalse(Path(str(path) + "-wal").exists())
        before = {
            directory: {p.name: digest(p) for p in directory.iterdir()}
            for directory in (self.arc, self.zen)
        }
        run = self.prepare({"history"})
        self.assertEqual(json.loads((run / "report.json").read_text())["places"]["visits_added"], 2)
        for directory, original in before.items():
            self.assertEqual({p.name: digest(p) for p in directory.iterdir()}, original)

    def test_backup_and_restore_preserve_unselected_symlinks_without_following_them(self):
        external = self.root / "external-style.css"
        external.write_text("/* invented user style */")
        chrome = self.zen / "chrome"
        chrome.mkdir()
        link = chrome / "userChrome.css"
        link.symlink_to(external)
        run = self.prepare({"addresses"})
        workflow.apply(run)
        backup_link = run / "backup/chrome/userChrome.css"
        self.assertTrue(backup_link.is_symlink())
        self.assertEqual(os.readlink(backup_link), str(external))
        workflow.restore(run)
        self.assertTrue(link.is_symlink())
        self.assertEqual(external.read_text(), "/* invented user style */")

    def test_stale_temporary_install_file_does_not_block_apply(self):
        run = self.prepare({"addresses"})
        stale = self.zen / "autofill-profiles.json.arc2zen-tmp"
        stale.write_text("invented interrupted install")
        self.assertEqual(workflow.apply(run)["status"], "applied")
        self.assertEqual(
            len(json.loads((self.zen / "autofill-profiles.json").read_text())["addresses"]), 1
        )

    def test_interrupted_run_has_an_explicit_recovery_message(self):
        for state in ("applying", "restoring"):
            with self.subTest(state=state):
                run = self.prepare({"addresses"}, name=state)
                workflow.apply(run)
                path = run / "manifest.json"
                manifest = json.loads(path.read_text())
                manifest["status"] = state
                manifest.pop("applied", None)
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(MigrationError, "interrupted"):
                    workflow.restore(run)
                self.assertEqual(workflow.restore(run, discard_new_data=True)["status"], "restored")

    def test_failed_cli_prepare_reports_its_private_run_path_without_secret(self):
        output, error = io.StringIO(), io.StringIO()
        run = self.root / "failed run"
        with (
            patch("arc2zen.workflow.discover", return_value={}),
            patch("arc2zen.cookies.read_key", side_effect=RuntimeError("invented secret error")),
            patch("sys.stdout", output),
            patch("sys.stderr", error),
        ):
            status = cli.main(
                [
                    "prepare",
                    "--arc-profile",
                    str(self.arc),
                    "--arc-sidebar",
                    str(self.side),
                    "--zen-profile",
                    str(self.zen),
                    "--output",
                    str(run),
                    "--parts",
                    "cookies",
                ]
            )
        self.assertEqual(status, 1)
        self.assertIn(str(run), error.getvalue())
        self.assertNotIn("invented secret error", output.getvalue() + error.getvalue())
        self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "preparing")

    def test_cli_explicit_profile_prepare_apply_restore_roundtrip(self):
        run = self.root / "CLI run with spaces"
        output = io.StringIO()
        with (
            patch("arc2zen.workflow.discover", return_value={}),
            patch("sys.stdout", output),
            patch("sys.stderr", io.StringIO()),
        ):
            status = cli.main(
                [
                    "prepare",
                    "--arc-profile",
                    str(self.arc),
                    "--arc-sidebar",
                    str(self.side),
                    "--zen-profile",
                    str(self.zen),
                    "--output",
                    str(run),
                    "--parts",
                    "forms",
                ]
            )
        self.assertEqual(status, 0)
        prepared = json.loads(output.getvalue())
        self.assertEqual(shlex.split(prepared["next"]), ["arc2zen", "apply", "--run", str(run)])
        for command, expected in (("apply", "applied"), ("restore", "restored")):
            output = io.StringIO()
            with patch("sys.stdout", output):
                self.assertEqual(cli.main([command, "--run", str(run)]), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], expected)


if __name__ == "__main__":
    unittest.main()
