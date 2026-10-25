# Arc → Zen

A local migration tool for **macOS**, adapted for Zen's current JSONLZ4 session format.
Prepare a candidate, review counts, then apply with a complete profile backup.

[Инструкция на русском](docs/README.ru.md) · [Compatibility and formats](docs/COMPATIBILITY.md) · [Development](docs/DEVELOPMENT.md)

This is an independent continuation of the migration work started with
[noneedofit/arc2zen](https://github.com/noneedofit/arc2zen). It does not use the old
`zen_pins` / `zen_workspaces` database layout. See [NOTICE](NOTICE.md) for attribution.

**Early release:** the original adapters were exercised on Arc 1.170.0 and Zen
1.23.4b on one Mac. The packaged CLI is covered by synthetic profile tests; it is
not a compatibility guarantee for every browser release or profile layout.

## What it transfers

| Data | Behavior |
| --- | --- |
| Spaces, icons, colors | New Zen workspaces; existing workspaces retained |
| Pinned tabs and Essentials | Arc order retained; shared Essentials included for the default profile |
| Open web tabs | Imported into their workspace; internal Arc URLs skipped |
| History | Native Places records, timestamps, referrers, visit types; duplicate visits skipped |
| Bookmarks | Nested folders under **Other Bookmarks → Arc Bookmarks** |
| Addresses | Names, street addresses, email, phone and usage metadata; enables Zen's address UI |
| Form values | Ordinary field names/values, usage counts and timestamps |
| Search shortcuts | Active custom HTTP(S) GET searches; default search engine retained |
| Cookies — opt-in | Persistent + session cookies, flags, SameSite and partition keys |
| Keyboard preset — opt-in | Cmd+D pin, Cmd+S sidebar, Cmd+Option+Return duplicate; conflicting bindings disabled |

Passwords use Zen's built-in CSV importer. Extensions must be installed from their
official Firefox listings; Chromium extension binaries and storage are not copied.
See [passwords and extensions](docs/PASSWORDS-AND-EXTENSIONS.md).

**Current limits:** only Arc's **Default** profile, flat pinned sidebars, and Zen
workspaces without containers. Bookmark folders work; pinned sidebar folders do
not. Unsupported structures stop candidate preparation. The key preset is a fixed
optional preset, not a converter for arbitrary Arc shortcuts.

## Install

Requires Python **3.11+**, `liblz4`, and Arc and Zen profiles that have been opened
and closed normally at least once. On macOS with Homebrew:

```sh
brew install python lz4
git clone https://github.com/kr4chinin/arc2zen.git
cd arc2zen
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
arc2zen --help
```

While this repository is private, cloning requires access to it and GitHub
authentication. No Python runtime dependencies are installed by the tool.

## Transfer

1. Run `arc2zen discover` to list profiles. Select the Zen profile you actually use;
   if more than one exists, the tool requires an explicit choice.
2. Quit **both Arc and Zen completely** with Cmd+Q. Keep them closed through prepare
   and apply. The tool never terminates your browsers.
3. Prepare a candidate, substituting the exact path printed by `discover`:

```sh
arc2zen prepare --zen-profile "/absolute/path/from/discover"
```

This prepares spaces, tabs, history, bookmarks, addresses, form values and search
shortcuts. It writes only to a private run directory, normally under
`~/Library/Application Support/arc2zen/runs/`. It does not change the live Zen profile.

To include cookies and the keyboard preset, select them explicitly:

```sh
arc2zen prepare --zen-profile "/absolute/path/from/discover" \
  --parts spaces,tabs,history,bookmarks,addresses,forms,search,cookies,keyboard
```

You can select fewer parts, for example `--parts history,bookmarks`. The `tabs`
part needs workspaces from an earlier import or `spaces` in the same preparation.

4. Review the printed counts and run the exact `arc2zen apply --run ...` command
   printed by preparation. Applying refuses modified candidates or profile files
   changed since preparation, then backs up the **entire Zen profile** before
   replacing only selected files. If Zen was reopened, prepare a new run.
5. Open Zen and check workspaces, **Arc Bookmarks**, history, autofill and a few
   important logins. Some sites may require signing in again.

`prepare` is the dry-run/staging step; there is no implicit apply. Use `--output`
to choose a new run directory, always **outside Git repositories and browser
profiles**. Reserve enough disk space for the source snapshot, candidate files and
one complete Zen backup.

### Cookies and macOS Keychain

Cookie conversion reads **Arc Safe Storage** from macOS Keychain once during
preparation. Approve the current system dialog yourself. If denied or timed out,
preparation stops; choose a new run directory and retry. There is no automated
dialog clicking or permission bypass. Apply/restore never read Keychain.

The Keychain secret stays in process memory and is never logged or written to disk.
Converted cookie values are stored in the local candidate and later in Zen's
native cookie stores. Session cookies stay session cookies; partition keys are
retained. The global cookie capacity is increased to avoid Firefox's default
capacity pruning a large import.

## Restore

Quit both browsers. To restore the files replaced by an applied run:

```sh
arc2zen restore --run "/absolute/path/of/the/run"
```

Restore refuses if those files changed after apply. After reviewing what will be
lost, `--discard-new-data` explicitly permits replacing newer history/bookmarks/
autofill/session changes in the selected files. A complete **before-restore**
rescue backup is created first. Unselected files, including password stores, are
left alone. SQLite restoration preserves schema and records; database page-header
bytes can differ because restoration uses SQLite's backup API.

If the process or machine was forcibly stopped mid-apply, keep Zen closed. Check
the run's `manifest.json` and complete `backup/`; a run left in `applying` can be
recovered with `restore --discard-new-data`. See [recovery details](docs/COMPATIBILITY.md#recovery).

## Privacy and sharing

The tool makes no HTTP requests and does not upload browser data. Run folders
contain private snapshots, decrypted cookies when selected, form values, a portable
tab HTML file and backups. They use private permissions and belong outside this
repository. **Never attach a run folder, browser profile, CSV, database or raw
session file to an issue.** The generated reports contain counts, not imported
values; manifests also contain local paths and hashes.

All committed fixtures are invented records under `example.test`. The repository
contains code, documentation and tests only. Keep the run until you have verified
the migration; delete private runs manually when you no longer need their backups.

MIT license. See [LICENSE](LICENSE) and [NOTICE](NOTICE.md).
