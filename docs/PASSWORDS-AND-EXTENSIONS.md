# Passwords and extensions

## Passwords

Use Arc/Chromium's password export and Zen's built-in password CSV import on the
password manager page. This CLI does not export, inspect, import or modify
`logins.json` or `key4.db`; it checks their hashes before/after apply.

Password CSV files contain plaintext passwords. Keep them outside this repository
and remove the temporary export after verifying the import. Follow Mozilla's
[CSV import instructions](https://support.mozilla.org/en-US/kb/import-login-data-file).

## Extensions

Install the official Firefox edition of each extension through Zen's UI from
[addons.mozilla.org](https://addons.mozilla.org/). Export/import settings using the
extension's own controls when available. Chromium extension directories, IDs,
permissions and LevelDB storage are not portable Firefox add-ons.

One extension used during the original migration has an official Firefox edition:

- [Geld](https://addons.mozilla.org/en-US/firefox/addon/geld/), with
  [source repository](https://github.com/brandonmcconnell/geld). Install the signed
  listing manually and approve only the permissions you need. The tool does not
  download packages, grant permissions or copy caches.

Anthropic documents [Claude in Chrome](https://support.claude.com/en/articles/12012173-get-started-with-claude-in-chrome)
as a Chrome extension. No official Firefox edition was identified during the
original migration. Check the vendor's current documentation before installing;
this project does not recommend unofficial clones.

## Site-local data

Cookies do not include localStorage, IndexedDB, service workers or every website's
offline drafts. Those stores are not migrated generically between browser engines.
Use each site's sync/export tools for any missing drafts or settings.
