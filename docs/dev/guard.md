# The development guard

`scripts/dev/guard.sh` is the backstop for CLAUDE.md's standing rules on this machine. Run `scripts/dev/guard.sh snapshot` at the start of a session and `scripts/dev/guard.sh check` at the end, and report the check output (CLAUDE.md, Enforcement). This page says what it records and compares. The installed tool never reads any of it.

- **Snapshots:** each snapshot is kept as `.guard/snapshot-<UTC timestamp>.txt` (gitignored). `check` compares against the newest one, or against a file named on the command line.
- **Repos:** the guarded repos are in the gitignored `.guard-paths`: the Parallax, Loupe and ISR clones under ~/code.
- **~/.claude.json:** its size and mtime are informational only. Changes to its MCP config (hashes of the top-level `mcpServers` and of each guarded repo's `projects[<path>].mcpServers`) count as real changes. ~/.claude/settings.json is compared by metadata.
- **Tool directories:** compared by metadata only; contents are never opened.
  - **~/.local/share/parallax** (Parallax's runtime directory) is recorded as a summary: file count, total size, newest mtime, and one sha256 over the sorted path, size and mtime lines.
  - **~/.config/parallax and ~/isr-notes** are recorded line by line (path, size, mtime).
  - A search by name on Oct 2, 2026 found no Loupe or ISR directories under ~/.local/share, ~/.config or ~/.cache. Add any that appear later to the guard.
- **Settings change, Oct 2, 2026, 20:21:58 UTC:** ~/.claude/settings.json grew from 433 to 3270 bytes between turns, when the user ran /auto-mode-setup. The user made that change; no session wrote it. It matters because user-scope settings apply to every claude run, including scripts/live-check.sh, so a live-check result depends on them. Never open the file; the guard compares it by metadata only. A new baseline snapshot was taken after the change.
- **Root .mcp.json:** `check` prints a warning line and exits non-zero if ~/code/polarizer/.mcp.json exists (CLAUDE.md rule 13).
- **Old snapshots** are never deleted. `check` reads the older line-by-line format too, by summarizing it the same way.
- **Not guarded:** the Windows-side Parallax clone on the Windows drive (the origin of the old backup clone). It isn't under ~/code, and /mnt/c isn't mounted in this WSL distro, so the guard can't see it. Don't touch it.
- **Backup clone archived, Oct 11, 2026:** at the owner's request, ~/code/parallax-backup-before-rewrite (pre-rewrite Parallax history, HEAD a6d6ce2) was saved as a git bundle of every ref and a tarball of the whole folder in ~/private-backups, both checked by a test restore, then deleted. It was removed from the gitignored .guard-paths and from ledger_forbidden_paths in polarizer.toml.
