# Development

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e . ruff
ruff check src tests
ruff format --check src tests
python -m unittest discover -s tests -v
```

The tests create disposable, entirely invented Arc/Zen profiles. They do not read
a user's home profile, call Keychain, launch browsers, download extensions, or
access the network. macOS additionally exercises real CommonCrypto AES with an
invented key and verifies hostname tampering rejection. That test is skipped on
Linux. Process-guard mocks are test-only; never add a CLI bypass for live migration.

Covered behavior includes candidate isolation, history/URL/referrer counts,
duplicate imports, bookmark nesting, exact autofill values and timestamp units,
session/persistent cookie separation and partition identities, profile drift,
candidate tampering, unsupported folders/custom profiles, partial-failure rollback,
restore conflict detection, rescue backups and redacted CLI errors.

CI runs the same synthetic checks on macOS and Linux with Python 3.11 and 3.13.
Passing these tests does not establish native browser compatibility on arbitrary
versions. Real-profile testing must be explicit and local, with a fresh backup;
never upload the profile or run folder as a CI artifact.

## Layout

- `workflow.py`: discover, snapshot, drift checks, backup, apply and restore.
- `sidebar.py`: stable workspace/tab IDs, default-profile Essentials, portable HTML.
- `places.py`: history/bookmarks and native Places metadata.
- `extras.py`: addresses, free-form autocomplete, search shortcuts and key preset.
- `cookies.py`: local macOS Keychain and CommonCrypto conversion.
- `jsonlz4.py`: Mozilla block format via liblz4.

Converters require a candidate marker; calling them with a browser profile directly
is rejected. Keep generated browser data outside the checkout. Extend compatibility
only with documented formats and invented fixtures. See [format references](SOURCES.md).
