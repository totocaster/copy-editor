# Changelog

What changed in each release of Copy Editor, for the people who write with it.
Versions follow [Semantic Versioning](https://semver.org); before 1.0, a minor
release may change behavior. Releases that change the database say so under
**Upgrade notes**. [Back up your database](README.md#data-backups-and-access)
before updating either way.

## Unreleased

## 0.2.0 - 2026-09-26

The first tagged release.

### Added

- An editor with blocks, a `/` menu, markdown shortcuts, smart typography, and
  autosave, with your own notes kept beside the text.
- Editing passes (Copy edit, Proofread, Line edit, Tighten, Read as a reader,
  Fact check) run through your signed-in Codex CLI or Claude Code, with no API
  keys. Each pass is a preset you can edit or duplicate.
- A rulebook of one-line rules in your own words. Findings can name the rule
  they came from.
- Findings as cards beside their highlight: suggestions with one-click Accept,
  cuts, questions, and fact checks with Correct or Verified.
- Dismissal memory: dismiss a finding with a reason and later passes on that
  document leave it alone.
- Revisions: automatic snapshots at the end of a sitting and before passes,
  manual ones with a note and a major flag, word-level diffs, and restore.
- Archive: move documents you're not working on out of the main list. Archived
  documents stay editable and can be moved back at any time.
- Copy or download a document as Markdown or plain text.
- The `ce` launcher for macOS. `ce --version` and the Settings page show which
  version you're running.

### Safety

- A tab can no longer overwrite newer text saved from another tab. Save
  conflicts ask which version to keep.
- Unsaved edits are backed up in the browser and can be recovered after a tab
  closes early.
- Passes, revisions, restores, exports, and archiving wait for pending edits to
  save first.
- The app only answers requests from your own machine. Sharing dismissal
  examples from other documents with a model is opt-in.

### Upgrade notes

- If you ran Copy Editor from an earlier checkout, the first start adds columns
  to the `documents` table (`version`, `archived_at`). Existing documents are
  kept.
