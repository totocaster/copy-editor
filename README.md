# Copy Editor

A workshop for prose. Write in a Notion-style editor, get a professional
copy edit from a model that runs under the subscriptions you already have,
and work through its findings the way you would with a human editor: accept,
correct, question, dismiss. Every dismissal teaches the next pass. Every
revision is kept.

No separate API key is required. Passes run through the Codex CLI or Claude
Code signed in on your machine; their provider services receive each pass
prompt.

## What it does

- **Editor.** Blocks, `/` menu, markdown shortcuts, smart typography, autosave.
- **Your notes.** Select text, leave a note to self. Notes are the only kind
  you write; the editing is the model's job.
- **Editing passes.** Copy edit, Proofread, Line edit, Tighten, Read as a
  reader, Fact check. Each is a preset you can edit or duplicate. Choose a
  model from OpenAI (via Codex) or Anthropic (via Claude Code) per run.
- **A rulebook.** One-line rules in your own words, numbered, tagged,
  switchable. Every enabled rule is checked on every copy edit, and each
  finding names the rule it came from.
- **Findings as cards.** Suggestions with one-click Accept, cuts, editorial
  questions, fact checks with a Correct or Verified action, notes on patterns.
  Cards sit beside their highlight with a connector line; the panel scrolls
  on its own and follows the text while you step through.
- **Dismissal memory.** Dismiss with a reason and the same finding is suppressed
  on that document. Later passes receive that document's dismissal history.
  Sharing dismissal examples from other documents is an optional Account setting.
- **Revisions.** Automatic snapshots at session boundaries and, by default,
  before passes; manual ones with a note and a *major* flag; word-level diffs and restore.
- **Export.** Copy or download as Markdown or plain text.
- **Keyboard.** `J`/`K` or `Alt+↓`/`Alt+↑` to step, `A` accept, `R` resolve,
  `X` dismiss, `?` for the full sheet.

## Quick start

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node 20+, npm,
GNU or BSD Make, and at least one signed-in model CLI for editing passes:

- OpenAI: `npm i -g @openai/codex` then `codex login`
- Anthropic: Claude Code, then `claude auth login`

```bash
git clone https://github.com/totocaster/copy-editor.git
cd copy-editor
make setup      # install dependencies from uv.lock and package-lock.json
make build      # create the ignored app/static/ asset directories and bundles
make run        # serve at http://127.0.0.1:8000
```

The Make targets work on macOS and Linux. For live CSS/JavaScript rebuilding and
server reload, use `make dev` after setup. `make seed` adds a sample document.
Generated assets stay out of Git, so a new checkout needs `make build` before
serving pages. `make test` runs the Python and browser-free JavaScript tests;
tests use a temporary database and stub model calls.

On macOS, `make cli` installs a `ce` launcher symlink in `~/.local/bin`. Add
that directory to your `PATH` if necessary, then run `ce` from anywhere. It
installs missing dependencies, builds changed assets, and starts a server on
the next free port; Ctrl+C stops it. `ce --open` opens your browser and
`ce --port 8010` chooses a starting port. The launcher uses macOS `zsh`,
`lsof`, and `open`; use Make on Linux.

Then, in the app: Settings → Account to confirm your sign-ins, Settings → Rules
to write your first rules, and the **Edit pass** button in any document.

## How it works

**Documents** are stored as ProseMirror JSON in SQLite, with derived plain text
and a word count. The editor autosaves after you stop typing and reports save
errors; close the page only after it shows the latest changes are saved. Unsaved
edits are also kept in this browser's local storage for draft recovery. They
remain on this computer until saved or discarded, and clearing browser site
data can remove them.

**Annotations** are marks inside the document, so highlights move with the
text and survive snapshots. The row holds the commentary: kind (`note`,
`suggest`, `question`, `cut`, `check`), body, replacement text, author, rule,
confidence, and status. `question` is strictly editorial; `check` is a factual
claim to verify. Pass findings can be accepted, resolved or dismissed but not
edited. On every save the server rescans the marks: highlights that were
deleted orphan their card, and pending findings whose highlight appears open.

**Passes** run in a worker thread. The document is split into ~1,500-word
windows of whole paragraphs; each window's prompt carries the document title,
applicable rules, this document's dismissal history, pass instructions, and
the numbered paragraphs, and goes
to the chosen provider: `codex exec` with a strict JSON schema, an ephemeral
session and a read-only sandbox, or `claude -p --json-schema` with no tools
and no session kept. Replies name a paragraph and a verbatim quote per
finding; the open editor places each one by locating the quote (exact, then
normalised, then loose), and anything it cannot place is kept as an orphaned
card with its text intact. Results stream in per window. If you enable
**Include dismissed findings from other documents** in Settings → Account, both providers
also receive selected quotes, proposed changes, and dismissal reasons from
other documents in later prompts. It is off by default.

**Models** come from Codex's live catalogue (current generation only, each
with its own reasoning levels) and the current Claude line-up. Defaults live in
Settings → Account; a pass can pin its own.

**Revisions** are full snapshots numbered per document: a session-end snapshot
when you return after ten idle minutes, a checkpoint every thirty minutes of
continuous work, optional snapshots before passes (on by default), and manual
ones from the History drawer. Restore snapshots the live text first.

## Data, backups, and access

The app stores documents, revisions, rules, settings, and pass results in
`data/write.db` by default. Set `WRITE_DB` to use another SQLite path. The
database and its `-wal` and `-shm` companion files are ignored by Git. Keep
backups and exported documents somewhere private; they contain your writing
and possibly provider responses. This is a single-user local app served on
`127.0.0.1`, with no user login. Local requests are checked for a loopback
client, local host, and a per-process write token. Do not expose its port to
a public network or run it as a shared service.

An editing pass sends its prompt to the selected provider through the signed-in
CLI. It includes portions of the active document and the context described
above. Provider authentication and data handling follow that provider's CLI
and account configuration. The app itself does not upload your database or
automatically run passes. If your document is sensitive, review the Account
setting and the selected provider before starting a pass.

To make a consistent backup while the app is running, use SQLite's online
backup API. Pick a private path that does not already exist:

```bash
uv run python - ~/Private/write-backup.db <<'PY'
from pathlib import Path
import sqlite3
import sys

source = Path("data/write.db")  # change this if WRITE_DB is set
target = Path(sys.argv[1]).expanduser()
if not source.is_file() or target.exists():
    raise SystemExit("Source missing or backup path already exists")
target.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
    src.backup(dst)
print(target)
PY
```

To restore, stop `ce`, `make run`, or `make dev` first. Set `backup` to your
chosen backup file, then move the current database and any WAL files together
before copying it back:

```bash
backup="$HOME/Private/write-backup.db"  # change to your backup path
(
  set -e
  test -f "$backup"
  mkdir -p data
  archive=$(mktemp -d data/pre-restore.XXXXXX)
  for file in data/write.db data/write.db-wal data/write.db-shm; do
    if [ -e "$file" ]; then mv "$file" "$archive/"; fi
  done
  cp -p "$backup" data/write.db
)
```

Start the app again and check the document list. Keep the archived files until
you have verified the restore. Never replace only the main database file while
the app is running or while old WAL files remain beside it. If you set
`WRITE_DB`, use that path and its companion files in both commands.

## Project layout

| Path | Role |
| --- | --- |
| `app/main.py` | Routes: pages, HTMX partials, JSON endpoints for the editor. |
| `app/repo.py` | Data access: documents, revisions, annotations, rules, passes, runs, settings. Revision policy and dismissal memory. |
| `app/runs.py` | The pass pipeline: snapshot, window, prompt, provider call, validate, store. |
| `app/providers.py`, `app/codex.py`, `app/claude_cli.py` | Provider registry and the two CLI drivers. |
| `app/prompts.py` | Prompt assembly and the reply schema. |
| `app/content.py` | ProseMirror JSON utilities: text, HTML, Markdown, anchors, diffs. |
| `app/db.py` | SQLite connection, schema, idempotent migrations (`data/write.db`, override with `WRITE_DB`). |
| `app/templates/` | Jinja pages and partials. |
| `frontend/` | Editor entry, annotation mark, placement, sidebar, connectors, menus, Tailwind source. |
| `bin/ce` | The launcher. |
| `scripts/watch.sh`, `scripts/dev.sh` | Frontend watchers and live server lifecycle. |
| `docs/mockups/` | The design mockup and proposal for the AI passes. |
| `tests/` | pytest and Node `node:test` suites (`make test`). |

Stack: FastAPI, Jinja2, HTMX, SQLite, Tailwind v4 built locally, TipTap
(ProseMirror) bundled with esbuild.

## Keyboard shortcuts

Press `?` in the editor. `/` opens the block menu and Markdown shortcuts work;
`Cmd+Alt+M` adds a note on the selection; `Cmd+S` saves now. `Alt+↓`/`Alt+↑`
step through notes from anywhere and `Alt+Enter` accepts; when the cursor is
not in the text, `J`/`K` step, `A` accepts, `R` resolves, `X` dismisses with a
reason, `E` edits your note, `P` opens the pass menu, `H` the history. `Esc`
deselects or closes menus.

## License

MIT. See [LICENSE](LICENSE).
