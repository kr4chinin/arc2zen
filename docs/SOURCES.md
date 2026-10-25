# Format references and provenance

The import adapters were developed by inspecting installed browser modules and the
following primary sources. Current upstream `main` files can drift; verify the
installed version before expanding support.

- [noneedofit/arc2zen](https://github.com/noneedofit/arc2zen), baseline commit
  `e1d73cd1baa0d582ea387e62d7bcddcb2980c91f`: Arc sidebar structure, space metadata
  and original migration approach. MIT notice retained.
- [Zen Browser source](https://github.com/zen-browser/desktop): workspace/session
  formats and native shortcut command IDs. Original local verification: 1.23.4b.
- [Mozilla HashFunctions.h](https://github.com/mozilla-firefox/firefox/blob/main/mfbt/HashFunctions.h):
  persisted 32-bit string hash.
- [Mozilla Places SQL functions](https://github.com/mozilla-firefox/firefox/blob/main/toolkit/components/places/SQLFunctions.cpp):
  URL hash scheme prefix and URL length handling.
- [Mozilla Places history](https://github.com/mozilla-firefox/firefox/blob/main/toolkit/components/places/History.cpp):
  visit and referrer semantics.
- [Mozilla FormHistory](https://github.com/mozilla-firefox/firefox/blob/main/toolkit/components/satchel/FormHistory.sys.mjs):
  native form history and microsecond timestamps.
- [Mozilla FormAutofillStorage](https://github.com/mozilla-firefox/firefox/blob/main/browser/extensions/formautofill/FormAutofillStorage.sys.mjs):
  address storage and metadata.
- [Chromium autofill table](https://chromium.googlesource.com/chromium/src/+/main/components/autofill/core/browser/webdata/autofill_table.cc):
  source autofill tables and timestamp units.
- [Chromium cookie store](https://chromium.googlesource.com/chromium/src/+/main/net/extras/sqlite/sqlite_persistent_cookie_store.cc):
  schema-24 hostname binding and source cookie fields.
- [Chromium macOS OSCrypt](https://chromium.googlesource.com/chromium/src/+/main/components/os_crypt/sync/os_crypt_mac.mm):
  legacy `v10` encryption and key derivation.
- [Mozilla cookie service](https://github.com/mozilla-firefox/firefox/tree/main/netwerk/cookie):
  native cookie schema, flags and expiry units.
- [Mozilla OriginAttributes](https://github.com/mozilla-firefox/firefox/blob/main/caps/OriginAttributes.cpp):
  partition keys and origin suffix encoding.
- [Mozilla profile backup](https://support.mozilla.org/en-US/kb/back-and-restore-information-firefox-profiles)
  and [password CSV import](https://support.mozilla.org/en-US/kb/import-login-data-file).

No vendor source dumps, real browser databases, decrypted data, or original user
reports are distributed in this repository.
