# Development

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e . ruff
ruff check src
ruff format --check src
```

CI checks source lint and formatting. Browser verification must be explicit and
local, with a fresh backup; never upload a profile or run folder as a CI artifact.
Do not add bypasses for process guards, backups or candidate isolation.

## Layout

- `workflow.py`: discover, snapshot, drift checks, backup, apply and restore.
- `sidebar.py`: stable workspace/tab IDs, default-profile Essentials, portable HTML.
- `places.py`: history/bookmarks and native Places metadata.
- `extras.py`: addresses, free-form autocomplete, search shortcuts and key preset.
- `cookies.py`: local macOS Keychain and CommonCrypto conversion.
- `jsonlz4.py`: Mozilla block format via liblz4.

Converters require a candidate marker; calling them with a browser profile directly
is rejected. Keep generated browser data outside the checkout. Extend compatibility
only with documented formats. See [format references](SOURCES.md).
