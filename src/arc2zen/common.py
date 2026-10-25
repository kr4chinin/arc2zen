"""Small shared helpers; data-bearing errors are never printed by the CLI."""

import hashlib
import json
import os
from pathlib import Path


class MigrationError(RuntimeError):
    """An actionable error whose message contains no imported browser values."""


def require(condition, message="Unsupported data or failed validation"):
    if not condition:
        raise MigrationError(message)


def require_candidate(profile):
    profile = Path(profile).resolve()
    marker = profile / ".arc2zen-candidate.json"
    require(marker.is_file(), "Converters only write to a prepared candidate")
    require(
        json.loads(marker.read_text()) == {"format": 1, "profile": str(profile)},
        "Candidate marker does not match this directory",
    )


def digest(path):
    path = Path(path)
    require(not path.is_symlink(), "Symlinked profile files are unsupported")
    if not path.exists():
        return None
    require(path.is_file() and not path.is_symlink(), "Expected a regular profile file")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.chmod(path, 0o600)


def set_pref(profile, name, value):
    # Firefox uses the last assignment. Retain original comments and preferences.
    with (Path(profile) / "prefs.js").open("a") as stream:
        stream.write(f"\nuser_pref({json.dumps(name)}, {json.dumps(value)});\n")
