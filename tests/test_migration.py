import ctypes
import hashlib
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fixtures import UNIX_US, make_profiles

from arc2zen import cli, cookies, jsonlz4, places, workflow
from arc2zen.common import MigrationError, digest


class CodecTests(unittest.TestCase):
    def test_roundtrip_unicode_and_corruption(self):
        value = {"space": "Example 🌱", "tabs": list(range(1000))}
        encoded = jsonlz4.encode(value)
        self.assertEqual(jsonlz4.decode(encoded), value)
        for data in (
            b"",
            b"not a session",
            encoded[:20],
            b"mozLz40\0" + (2**32 - 1).to_bytes(4, "little") + b"x",
        ):
            with self.assertRaises(MigrationError):
                jsonlz4.decode(data)

    def test_partition_identity_keeps_isolation(self):
        row = {
            "top_frame_site_key": "https://top.example.test",
            "has_cross_site_ancestor": 1,
        }
        suffix, attrs = cookies.partition(row)
        self.assertEqual(attrs, {"partitionKey": "(https,top.example.test,f)"})
        self.assertEqual(suffix, "^partitionKey=%28https%2Ctop.example.test%2Cf%29")
        identity = cookies.cookie_key(
            {
                "name": "sid",
                "host": ".example.test",
                "path": "/",
                "originAttributes": attrs,
            }
        )
        self.assertEqual(identity[-1], suffix)
        with self.assertRaises(MigrationError):
            cookies.partition(
                {
                    "top_frame_site_key": "https://top.example.test:8443",
                    "has_cross_site_ancestor": 0,
                }
            )

    @unittest.skipUnless(sys.platform == "darwin", "CommonCrypto is macOS-only")
    def test_real_aes_and_host_verification_without_keychain(self):
        key, host = b"0123456789abcdef", ".example.test"
        plaintext = hashlib.sha256(host.encode()).digest() + b"invented-cookie-value"
        output = ctypes.create_string_buffer(len(plaintext) + 16)
        written = ctypes.c_size_t()
        status = cookies.crypto().CCCrypt(
            0,
            0,
            1,
            key,
            len(key),
            b" " * 16,
            plaintext,
            len(plaintext),
            output,
            len(output),
            ctypes.byref(written),
        )
        self.assertEqual(status, 0)
        row = {
            "host_key": host,
            "encrypted_value": b"v10" + output.raw[: written.value],
        }
        self.assertEqual(cookies.decrypt(row, key), "invented-cookie-value")
        with self.assertRaises(MigrationError):
            cookies.decrypt({**row, "host_key": "other.example.test"}, key)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.arc, self.side, self.zen = make_profiles(self.root)
        self.closed = patch("arc2zen.workflow.ensure_closed")
        self.closed.start()
        self.addCleanup(self.closed.stop)
        self.addCleanup(self.temp.cleanup)
        self.passwords = {
            name: digest(self.zen / name) for name in ("logins.json", "key4.db", "cookies.sqlite")
        }

    def logical_state(self):
        result = {}
        for name in workflow.ALLOWED_FILES:
            path = self.zen / name
            if name.endswith(".sqlite") and path.exists():
                with closing(sqlite3.connect(path)) as connection:
                    result[name] = list(connection.iterdump())
            else:
                result[name] = path.read_bytes() if path.exists() else None
        return result

    def prepare(self, parts=None, name="run"):
        return workflow.prepare(
            self.arc,
            self.side,
            self.zen,
            self.root / name,
            parts or workflow.DEFAULT_PARTS,
        )[0]

    def test_prepare_apply_restore_and_exact_fields(self):
        before = self.logical_state()
        source_before = {p: digest(p) for p in self.arc.iterdir()}
        run = self.prepare(workflow.DEFAULT_PARTS | {"keyboard"})
        self.assertEqual(self.logical_state(), before)
        report = json.loads((run / "report.json").read_text())
        self.assertEqual(report["places"]["visits_added"], 2)
        self.assertEqual(report["places"]["duplicate_visits_skipped"], 1)
        self.assertEqual(report["places"]["unsupported_visits_skipped"], 1)
        self.assertEqual(report["places"]["bookmarks_added"], 1)
        self.assertEqual(report["sidebar"]["essentials_added"], 1)
        self.assertEqual(report["sidebar"]["unsupported_open_tabs_skipped"], 1)
        self.assertEqual(workflow.apply(run)["status"], "applied")
        self.assertEqual({p: digest(p) for p in self.arc.iterdir()}, source_before)
        self.assertEqual({name: digest(self.zen / name) for name in self.passwords}, self.passwords)
        with closing(sqlite3.connect(self.zen / "formhistory.sqlite")) as db:
            self.assertEqual(
                db.execute(
                    "select fieldname,value,timesUsed,firstUsed,lastUsed from moz_formhistory"
                ).fetchone(),
                ("example-field", "invented form value", 5, UNIX_US - 1000000, UNIX_US),
            )
        with closing(sqlite3.connect(self.zen / "places.sqlite")) as db:
            self.assertEqual(
                db.execute(
                    "select visit_count,last_visit_date,typed from moz_places where url=?",
                    ("https://history.example.test/",),
                ).fetchone(),
                (2, UNIX_US + 1000, 1),
            )
            self.assertEqual(
                db.execute(
                    "select from_visit from moz_historyvisits order by id desc limit 1"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(db.execute("pragma foreign_key_check").fetchall(), [])
        zen = jsonlz4.read(self.zen / "zen-sessions.jsonlz4")
        self.assertEqual(len(zen["spaces"]), 2)
        self.assertEqual(zen["spaces"][1]["icon"], "🌱")
        self.assertEqual(len(zen["tabs"]), 4)
        addresses = json.loads((self.zen / "autofill-profiles.json").read_text())
        self.assertEqual(addresses["addresses"][0]["name"], "Example Person")
        keyboard = json.loads((self.zen / "zen-keyboard-shortcuts.json").read_text())
        self.assertTrue(keyboard["shortcuts"][-1]["disabled"])
        self.assertEqual(workflow.restore(run)["status"], "restored")
        self.assertEqual(self.logical_state(), before)

    def test_repeat_import_deduplicates(self):
        workflow.apply(self.prepare())
        run = self.prepare(name="run-two")
        report = json.loads((run / "report.json").read_text())
        self.assertEqual(report["places"]["visits_added"], 0)
        self.assertEqual(report["places"]["bookmarks_added"], 0)
        self.assertEqual(report["sidebar"]["spaces_added"], 0)
        self.assertEqual(report["sidebar"]["open_tabs_added"], 0)
        self.assertEqual(report["extras"]["addresses_added"], 0)
        self.assertEqual(report["extras"]["forms_added"], 0)
        self.assertEqual(report["extras"]["search_added"], 0)

    def test_history_and_bookmarks_are_independent(self):
        run = self.prepare({"history"})
        self.assertFalse((run / "source/Bookmarks.json").exists())
        self.assertEqual(
            json.loads((run / "report.json").read_text())["places"]["bookmarks_added"],
            0,
        )
        run = self.prepare({"bookmarks"}, name="bookmarks-only")
        self.assertFalse((run / "source/History.sqlite").exists())
        self.assertEqual(json.loads((run / "report.json").read_text())["places"]["visits_added"], 0)

    def test_wal_is_snapshotted_and_drift_is_detected(self):
        with closing(sqlite3.connect(self.zen / "formhistory.sqlite")) as writer:
            writer.execute("pragma journal_mode=wal")
            writer.execute("pragma wal_autocheckpoint=0")
            writer.execute(
                "insert into moz_formhistory values(1,'existing','invented existing',2,1,2,'existingGuid')"
            )
            writer.commit()
            run = self.prepare({"forms"})
            with closing(sqlite3.connect(run / "candidate/formhistory.sqlite")) as db:
                self.assertEqual(
                    db.execute("select count(*) from moz_formhistory").fetchone()[0], 2
                )
            writer.execute("update moz_formhistory set timesUsed=3 where id=1")
            writer.commit()
            with self.assertRaisesRegex(MigrationError, "changed since prepare"):
                workflow.apply(run)

    def test_profile_lock_excludes_parallel_writers(self):
        with workflow.profile_lock(self.zen):
            with self.assertRaisesRegex(MigrationError, "Another Arc to Zen"):
                with workflow.profile_lock(self.zen):
                    self.fail("Second writer acquired the same profile")
        with workflow.profile_lock(self.zen):
            pass

    def test_drift_tampering_and_unprepared_paths_are_refused(self):
        run = self.prepare({"forms"})
        with (self.zen / "logins.json").open("a") as stream:
            stream.write(" ")
        with self.assertRaisesRegex(MigrationError, "changed since prepare"):
            workflow.apply(run)
        with self.assertRaises(MigrationError):
            places.convert(run / "source", self.zen)
        # A separate run detects candidate tampering before backing up or writing.
        run = self.prepare({"forms"}, name="another-run")
        with (run / "candidate/formhistory.sqlite").open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaisesRegex(MigrationError, "Candidate changed"):
            workflow.apply(run)
        self.assertFalse((run / "backup").exists())

    def test_apply_failure_rolls_back(self):
        run = self.prepare({"addresses", "forms"})
        original = self.logical_state()
        real_install = workflow.install_file

        def fail_on_candidate_forms(source, target):
            if source.parent.name == "candidate" and target.name == "formhistory.sqlite":
                raise OSError("injected failure")
            real_install(source, target)

        with patch("arc2zen.workflow.install_file", side_effect=fail_on_candidate_forms):
            with self.assertRaises(OSError):
                workflow.apply(run)
        self.assertEqual(self.logical_state(), original)
        self.assertEqual(json.loads((run / "manifest.json").read_text())["status"], "rolled_back")

    def test_restore_detects_new_data_and_keeps_rescue_backup(self):
        run = self.prepare({"addresses"})
        workflow.apply(run)
        with (self.zen / "prefs.js").open("a") as stream:
            stream.write("// newer user changes\n")
        with self.assertRaisesRegex(MigrationError, "discard newer data"):
            workflow.restore(run)
        result = workflow.restore(run, discard_new_data=True)
        self.assertIn(
            "newer user changes",
            (Path(result["rescue_backup"]) / "prefs.js").read_text(),
        )

    def test_rejects_folders_and_custom_profiles_without_live_writes(self):
        original = workflow.fingerprint(self.zen, workflow.ALLOWED_FILES)
        data = json.loads(self.side.read_text())
        items = data["sidebar"]["containers"][1]["items"]
        items[items.index("pinned-tab") + 1]["data"] = {"list": {}}
        self.side.write_text(json.dumps(data))
        with self.assertRaisesRegex(MigrationError, "folders"):
            self.prepare({"spaces"})
        self.assertEqual(workflow.fingerprint(self.zen, workflow.ALLOWED_FILES), original)
        data["sidebar"]["containers"][1]["spaces"][1]["profile"] = {"custom": {}}
        self.side.write_text(json.dumps(data))
        with self.assertRaisesRegex(MigrationError, "custom Arc profiles"):
            self.prepare({"spaces"}, name="custom-profile-run")

    def test_no_browser_data_inside_a_git_repository(self):
        checkout = self.root / "checkout"
        (checkout / ".git").mkdir(parents=True)
        with self.assertRaisesRegex(MigrationError, "outside Git"):
            workflow.prepare(self.arc, self.side, self.zen, checkout / "run", {"forms"})

    def test_cookie_merge_sessions_partitions_and_expiry(self):
        now = cookies.EPOCH_US + UNIX_US
        rows = []
        for name, persistent, expiry, site in (
            ("persistent", 1, 99999999999999999, ""),
            ("session", 0, 0, "https://top.example.test"),
            ("expired", 1, now, ""),
            ("extension", 0, 0, "chrome-extension://invented-extension"),
        ):
            rows.append(
                (
                    name,
                    ".example.test",
                    "/",
                    b"synthetic ciphertext",
                    persistent,
                    expiry,
                    site,
                    0,
                    2,
                    1,
                    1,
                    1,
                    now,
                    now,
                    now,
                )
            )
        with closing(sqlite3.connect(self.arc / "Cookies")) as db, db:
            db.executemany("insert into cookies values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        with (
            patch("arc2zen.cookies.read_key", return_value=b"invented-key"),
            patch("arc2zen.cookies.decrypt", return_value="invented-value"),
        ):
            run = self.prepare({"cookies"})
        report = json.loads((run / "report.json").read_text())["cookies"]
        self.assertEqual(
            (
                report["persistent_imported"],
                report["session_imported"],
                report["expired_skipped"],
            ),
            (1, 1, 1),
        )
        self.assertEqual(report["unsupported_extension_partition_skipped"], 1)
        workflow.apply(run)
        session = jsonlz4.read(self.zen / "sessionstore.jsonlz4")
        self.assertEqual(len(session["cookies"]), 2)
        self.assertFalse(any("expiry" in row for row in session["cookies"]))
        self.assertEqual(
            session["cookies"][-1]["originAttributes"],
            {"partitionKey": "(https,top.example.test)"},
        )
        with closing(sqlite3.connect(self.zen / "cookies.sqlite")) as db:
            self.assertEqual(db.execute("select count(*) from moz_cookies").fetchone()[0], 2)
        self.assertIn("network.cookie.maxNumber", (self.zen / "prefs.js").read_text())

    def test_cli_does_not_print_data_bearing_errors(self):
        output = io.StringIO()
        with (
            patch(
                "arc2zen.workflow.discover",
                side_effect=ValueError("secret invented browser value"),
            ),
            patch("sys.stderr", output),
        ):
            self.assertEqual(cli.main(["discover"]), 1)
        self.assertNotIn("secret invented", output.getvalue())


class ProcessGuardTests(unittest.TestCase):
    def test_detects_arc_and_zen_processes(self):
        for process in (
            "/Applications/Arc.app/Contents/MacOS/Arc",
            "/Applications/Zen.app/Contents/MacOS/zen",
        ):
            with patch("sys.platform", "darwin"), patch("subprocess.run") as runner:
                runner.return_value.stdout = process
                with self.assertRaisesRegex(MigrationError, "Quit Arc and Zen"):
                    workflow.ensure_closed()


if __name__ == "__main__":
    unittest.main()
