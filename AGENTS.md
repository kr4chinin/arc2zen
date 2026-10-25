# Repository instructions

- This is a macOS migration tool, not browser sync. Preserve the staged
  `prepare` / `apply` / `restore` boundary.
- Never commit browser profiles, cookie/session databases, source snapshots,
  backups, password CSV files, Keychain output or data-bearing reports.
- Use invented fixtures under reserved example domains. Tests must not read a
  real home profile, request Keychain access, launch browsers or use the network.
- Do not add process-guard or backup bypass flags for live operations.
- Keychain secrets remain in memory. Error output must never contain imported
  browser values or subprocess stdout from the `security` command.
- SQLite changes use its backup API; JSON replacements are atomic. Preserve WAL
  data and check drift before writing. Keep complete backups and rescue copies.
- Unsupported profile/container/schema layouts must fail during preparation.
- Keep English and Russian user instructions consistent and state verification
  limits. Do not imply synthetic checks prove arbitrary native browser versions.
- Run `ruff check src tests`, `ruff format --check src tests` and
  `python -m unittest discover -s tests -v` before publishing code changes.
- GitHub comments and authored reports must disclose agent authorship according
  to the user's global instructions; ordinary code and commit messages need no note.
