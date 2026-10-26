"""Command line entry point. No migration happens without an explicit command."""

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

from . import __version__, workflow
from .common import MigrationError, require


def parser():
    root = argparse.ArgumentParser(
        description="Local Arc to Zen migration: discover, prepare, apply, restore"
    )
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("discover", help="List Arc and Zen profile paths; does not change browsers")
    prepare = commands.add_parser(
        "prepare",
        help="Prepare an isolated candidate; does not change the live profile",
    )
    prepare.add_argument("--arc-profile", type=Path)
    prepare.add_argument("--arc-sidebar", type=Path)
    prepare.add_argument("--zen-profile", type=Path)
    prepare.add_argument("--output", type=Path, help="New run directory OUTSIDE any Git repository")
    prepare.add_argument(
        "--parts",
        default=",".join(sorted(workflow.DEFAULT_PARTS)),
        help="Comma-separated: "
        + ",".join(sorted(workflow.PARTS))
        + ". spaces includes pinned tabs/Essentials; tabs imports open web tabs. "
        "Cookies and keyboard are opt-in.",
    )
    for name in ("apply", "restore"):
        command = commands.add_parser(
            name,
            help="Apply a prepared run"
            if name == "apply"
            else "Restore files from the pre-apply backup",
        )
        command.add_argument("--run", type=Path, required=True)
        if name == "restore":
            command.add_argument(
                "--discard-new-data",
                action="store_true",
                help="Explicitly allow replacing imported files after using Zen",
            )
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    os.umask(0o077)
    try:
        if args.command == "discover":
            result = workflow.discover()
        elif args.command == "prepare":
            found = workflow.discover()
            profile = args.zen_profile
            if profile is None:
                profiles = found["zen_profiles"]
                require(
                    len(profiles) == 1 and profiles[0]["exists"],
                    "Several or no Zen profiles found. Run discover and pass --zen-profile explicitly.",
                )
                profile = Path(profiles[0]["path"])
            parts = {part.strip() for part in args.parts.split(",") if part.strip()}
            run_path = (args.output or workflow.default_run()).expanduser().resolve()
            print(
                f"Private run directory: {run_path}",
                file=sys.stderr,
                flush=True,
            )
            run, report = workflow.prepare(
                args.arc_profile or Path(found["arc_profile"]),
                args.arc_sidebar or Path(found["arc_sidebar"]),
                profile,
                run_path,
                parts,
            )
            result = {
                "status": "prepared",
                "run": str(run),
                "report": report,
                "next": "arc2zen apply --run " + shlex.quote(str(run)),
            }
        elif args.command == "apply":
            result = workflow.apply(args.run)
        else:
            result = workflow.restore(args.run, discard_new_data=args.discard_new_data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except MigrationError as error:
        print(f"Stopped: {error}", file=sys.stderr)
        return 1
    except (Exception, KeyboardInterrupt) as error:
        # Never echo SQLite rows, URLs, addresses, cookies, Keychain output or traceback locals.
        print(
            f"Stopped: {type(error).__name__}. Browser values were not logged. "
            "Check compatibility and the private run directory; do not upload it.",
            file=sys.stderr,
        )
        return 1
