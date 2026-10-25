# Compatibility and data formats

## Scope

macOS only for live migration. Python 3.11+ and liblz4 are required. CommonCrypto
and the macOS `security` command are used only when cookies are selected.

The predecessor scripts were used with Arc 1.170.0, Zen 1.23.4b and Mozilla cookie
schema 17. The new staged CLI uses those adapters with synthetic integration
coverage. Other versions are not certified. Compatibility checks stop unsupported
input in the candidate; no target writes occur during preparation.

- Arc `User Data/Default`; multiple/custom Arc profiles are rejected.
- One local sidebar container with alternating key/value arrays.
- Flat pinned tabs; pinned folders fail. Open tabs accept HTTP(S) and skip browser
  internal URLs. Existing Zen tabs, folders, groups and split-view data are retained.
- Current `zen-sessions.jsonlz4` structure: spaces, tabs, folders, groups, splitViewData.
- Stable identifiers deduplicate repeated imports. Existing imported records are
  generally retained on rerun; this is a one-time importer, not ongoing sync.
- Workspaces are mapped by stable source IDs, never by potentially duplicated names.
- `places.sqlite` requires the modern columns used in the adapter and no persistent
  triggers. Existing native URL hashes are checked before adding records.
- `formhistory.sqlite`, `search.json.mozlz4`, session files and keyboard settings
  must exist for the selected parts. If missing, use/init the relevant feature in
  Zen, close it normally, or omit that part. The tool does not invent an unknown schema.
- Address JSON version 1; existing credit card records are retained without importing
  Chromium payment data. Unsupported address country formats stop preparation.
- GET search templates containing `{searchTerms}` only. Existing aliases win;
  POST engines and unsupported URLs are skipped.

## Cookie mapping

Arc cookie database `meta.version = 24` → Mozilla `user_version = 17`.

- `v10` AES-CBC; PBKDF2-HMAC-SHA1 (`saltysalt`, 1003 iterations, 16-byte key).
- Validate the SHA256 hostname prefix after decryption before accepting the value.
- Chromium timestamps are microseconds since 1601. Mozilla persistent expiry in
  this tested schema is Unix **milliseconds**; access/creation/update use Unix
  microseconds. Do not reuse this conversion for older Firefox expiry schemas.
- Chromium SameSite -1/0/1/2 → Mozilla 256/0/1/2.
- Partition `top_frame_site_key` → Mozilla `originAttributes.partitionKey`, retaining
  the cross-site ancestor marker. Never flatten partitioned cookies.
- Persistent cookies go into SQLite; session cookies go into SessionStore and
  retain their session lifetime. A one-shot session restore preference is set.
- Expired persistent cookies and extension-origin partitions are counted/skipped.
- When Chromium entries collapse to one Mozilla identity, the most recently updated
  record wins. On identity collisions with Zen, imported Arc cookies replace Zen's.
- Other existing Zen cookies are retained. Containers/private-context cookies that
  need an explicit mapping stop conversion.
- The global cookie capacity becomes at least 5000 or the prepared total plus 1000.
  Firefox per-host limits and server-side session checks can still affect logins.

Cookie values, credentials and Keychain output are never included in printed errors.
Reports contain counts; private candidate files necessarily contain native plaintext
cookie records. No secret is sent over the network by this tool.

## History, bookmarks and autofill

The Places URL hash uses Mozilla's persisted hash algorithm, including scheme
prefix bits and the 1500-byte URL hash limit. Dates and referrers are imported,
visit counts exclude the same non-counting transition types as Places, and native
frecency recalculation flags are set. Duplicate visits use URL + date + transition.

Ordinary bookmark hierarchy is retained under a dedicated import root. Bookmark
timestamps are converted from Chromium's 1601 microseconds. Form timestamps in the
tested Arc `autofill` table are Unix **seconds**, converted to Mozilla microseconds;
unsupported units/dates stop preparation. Existing form pairs merge usage maxima
and the earliest/latest timestamps. Address metadata uses Unix milliseconds.

## Recovery

Only `apply` and `restore` write live Zen files. Both require closed browsers and
hold a per-profile process lock to exclude another cooperating migration writer.
SQLite uses its backup API, including WAL content; JSON files use atomic replacement.
The main database and WAL hashes are included in drift checks. Multi-file apply is
not a filesystem transaction: exceptions trigger rollback, but a power failure or
SIGKILL can interrupt it. Check `manifest.json` before reopening Zen.

- `preparing`: incomplete candidate. Live profile was not written. Start a new run.
- `prepared`: candidate ready. Reopening Zen requires preparing a new run.
- `applying`: interrupted apply. Use `restore --run ... --discard-new-data`, or
  manually restore the complete `backup/` with Zen closed.
- `applied`: import finished. Verify it in Zen and retain `backup/`.
- `rolled_back`: automatic rollback completed after an apply error. Keep the run for inspection.
- `restoring`: interrupted restore. The same explicit restore can be retried. A
  before-restore rescue copy is retained.
- `restored`: selected files were restored. Full backups remain available.

A complete profile backup is made with all files except process lock files.
Symlinked profile contents are refused. Restore changes only selected files and
removes a newly created address file if it did not exist before the import. If
post-import data exists, `--discard-new-data` is required and a fresh full rescue
backup is made first. Nothing automatically deletes runs or original Arc data.

Manual full-profile recovery with both browsers closed follows Mozilla's
[profile backup/restore instructions](https://support.mozilla.org/en-US/kb/back-and-restore-information-firefox-profiles).
Keep an extra copy of the current profile before replacing it manually.
