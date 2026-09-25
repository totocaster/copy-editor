# Copy Editor

A local editor for writing and revising prose, with AI editing passes,
annotations, and revision history. Write your draft, ask for a pass, and
work through the findings beside your text. You decide which changes to make.

Copy Editor runs in your browser with a server on your own computer. Editing
passes use the Codex CLI or Claude Code signed in on that machine. They send
prompt context to the selected provider; no separate API key is required
when using a supported subscription sign-in.

## Why I made it

I enjoy writing, and I don't like AI-generated text. But I can't deny that AI
is a phenomenal tool in many ways. It can help me spot a mistake, question a
claim, or notice where a sentence gets in the way of what I mean. I made
Copy Editor to make that kind of help part of my writing process.

This follows the idea in [The Human Border](https://ttvl.co/notes/the-human-border/):
I am happy to use AI as a private tool, while taking responsibility for the
words I share with other people. I want to do the writing, make the decisions,
and stand behind the result.

The workflow reflects that. I bring the draft and my own rules. The model
leaves suggestions, questions, and notes. I can accept a correction, rewrite
a passage myself, or dismiss a finding and explain why. The judgment stays
with me.

[Installation](#installation) · [Usage](#usage) · [Updating](#updating) ·
[Troubleshooting](#troubleshooting) · [Data and backups](#data-backups-and-access) ·
[Development](#development)

## What it does

- **Editor.** Blocks, `/` menu, markdown shortcuts, smart typography, autosave.
- **Your notes.** Select text and leave a note to yourself alongside the draft.
- **Editing passes.** Copy edit, Proofread, Line edit, Tighten, Read as a
  reader, Fact check. Each is a preset you can edit or duplicate. Choose a
  model from OpenAI (via Codex) or Anthropic (via Claude Code) per run.
- **A rulebook.** One-line rules in your own words, numbered, tagged,
  switchable. Copy edit passes receive every enabled rule; findings can name
  the rule they came from.
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

## Installation

### Requirements

- macOS or Linux. The optional `ce` launcher is macOS-only.
- Git, Python 3.12 or newer, [uv](https://docs.astral.sh/uv/getting-started/installation/),
  [Node.js](https://nodejs.org/en/download) 20 or newer with npm, and Make.
- A browser.
- For AI passes, at least one installed and signed-in provider CLI:
  [Codex CLI](https://learn.chatgpt.com/docs/codex/cli) or
  [Claude Code](https://code.claude.com/docs/en/setup).

Writing, notes, revisions, and export work without a model account. AI passes
need an internet connection and use the selected provider account's access
and usage limits.

### Download, build, and start

Run these commands in Terminal:

```bash
git clone https://github.com/totocaster/copy-editor.git
cd copy-editor
make setup
make build
make run
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser. Keep the
Terminal window open while you use the app; press **Ctrl+C** there to stop it.
To start it again, return to the `copy-editor` directory and run `make run`.

`make setup` installs the versions in the Python and Node lockfiles.
`make build` creates the CSS and JavaScript assets, which are not stored in
Git. Keep this checkout: the app and, by default, your database live here.

### Install the macOS launcher

Stop the server with **Ctrl+C**, then run this from the checkout:

```bash
make cli
export PATH="$HOME/.local/bin:$PATH"
ce --open
```

This installs a symlink at `~/.local/bin/ce` and opens the app in your browser.
If `~/.local/bin` is not already on your PATH, add the `export` line to
`~/.zshrc` so new Terminal windows can find it. You can then run `ce --open`
from any directory. Keep the checkout in place; rerun `make cli` if you move it.

Other launch options:

```bash
ce                   # start the app and print its address
ce --port 8010 --open # start at a different port and open the browser
ce --help            # show launcher options
```

The launcher installs missing dependencies and rebuilds changed frontend
assets. It reuses a running Copy Editor at the requested port, or tries nearby
ports if another program occupies it. **Ctrl+C** stops a server started by
that Terminal window. On Linux, use `make run`.

### Connect an AI provider

You only need one provider; installing both lets you choose between them for
each pass. If the CLI is already installed and signed in, open
**Settings → Account** and click **Re-check**.

For **OpenAI**, install Codex and sign in:

```bash
npm install -g @openai/codex
codex login
```

Complete the browser sign-in with your ChatGPT account. See the official
[Codex installation guide](https://learn.chatgpt.com/docs/codex/cli) and
[authentication guide](https://learn.chatgpt.com/docs/auth) for other options.

For **Anthropic**, install Claude Code using its
[installation guide](https://code.claude.com/docs/en/setup), then sign in:

```bash
claude auth login
```

The [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)
describes its account commands. Use the same computer and operating-system
user that runs Copy Editor.

In **Settings → Account**, confirm that the provider is signed in, choose
your default model and reasoning effort, and click **Save**. You can also use
the **Sign in** button there once a CLI is installed. Review **AI data sharing**
on the same page: sharing dismissed findings from other documents is off by
default.

## Usage

### 1. Write a draft

Click **New document** on the Documents page. Give it a title and write or
paste your draft into the editor. Type `/` to choose a block, or use Markdown
shortcuts while typing. Select text to open the formatting toolbar and add
a note to yourself.

Text and title save automatically after you pause. Wait for **Saved** before
closing the tab; **Cmd+S** on macOS or **Ctrl+S** on Linux saves immediately.

### 2. Add your editing rules

Open **Settings → Rules** and add one rule per line, in your own words. For
example:

```text
Keep contractions where I use them.
Flag repeated words in neighbouring sentences.
Ask about an unclear reference instead of guessing what I mean.
```

Rules can be tagged, reordered, switched off, imported, and exported. The
default **Copy edit** pass receives all enabled rules. Use
**Settings → Passes** to edit or duplicate a preset, change its instructions
and rule selection, or pin a model for it.

### 3. Run an editing pass

Open **Edit pass** in the document header and choose the kind of feedback
you want:

| Pass | Focus |
| --- | --- |
| Copy edit | Grammar, consistency, clarity, and your enabled rules. |
| Proofread | Spelling, punctuation, and other mechanical errors. |
| Line edit | Sentence rhythm, word choice, and redundancy. |
| Tighten | Proposed cuts and shorter wording. |
| Read as a reader | Questions and notes about attention, clarity, and credibility. |
| Fact check | Names, dates, numbers, quotations, and claims to verify. |

Choose a model and effort level, then click **Run pass**. Leave
**Save a revision before running** checked if you want a snapshot of the
draft you submitted. The app saves pending edits before starting the pass.
Findings appear as the pass progresses, and you can cancel from its status
strip. **Settings → Runs** shows previous runs and their status.

Fact-check findings are leads for your own verification. Check the underlying
sources before changing a claim or marking it verified.

### 4. Work through the findings

Click a highlight or its card to focus it. Use **Alt+↓** and **Alt+↑** to move
between findings without leaving the keyboard.

- **Accept** applies a suggested replacement; **Cut it** removes a proposed cut.
- **Correct** applies a proposed factual correction. **Verified**, when shown,
  records that you have checked the claim.
- **Resolve** closes a question or note after you have dealt with it.
- **Dismiss** leaves the wording alone and lets you choose a reason.

You can also edit the draft yourself. Suggestions are applied only when you
choose to apply them. Copy Editor reuses dismissal reasons as prompt context
in later passes on that document.

### 5. Keep and compare revisions

Open **History**, add an optional note, and click **Save revision** at a point
you want to keep. Flag important revisions as major. **View & diff** opens a
saved revision and its word-level changes; **Restore** brings its text back
while preserving the current text in history first.

If you see **Save conflict**, another tab has saved a newer version. Choose
**Load saved version** to discard this tab's unsaved edits, or **Replace saved
version** to keep this tab's draft and preserve the previous saved text in
history.

If a tab closes before saving finishes, reopen the document in the same
browser at the same local address. When a local backup is available,
**Recover draft** restores it for review. Browser backups belong to that
browser and address, including the port; clearing site data removes them.

### 6. Export your writing

Open **Export** in the header. Copy or download the document as **Markdown**
or **plain text**. Export waits for pending edits to save. These exports contain
the document text; use a database backup to keep annotations, rules, and
revision history too.

## Updating

Wait for **Saved**, stop the server with **Ctrl+C**, and
[back up your database](#data-backups-and-access). From the existing checkout:

```bash
git pull --ff-only
make setup
make build
make run
```

On macOS, you can use `ce --open` instead of the final `make run`. Keep using
the same checkout and database path so your documents remain available, and
reload open browser tabs after restarting the server.

## Troubleshooting

| Problem | What to check |
| --- | --- |
| `ce: command not found` | Run `make cli` from the checkout and add `~/.local/bin` to your PATH, or launch with `~/.local/bin/ce --open`. |
| A required command is missing | Install the tools listed under Requirements, open a new Terminal window, and rerun `make setup`. |
| The editor is blank or unstyled | Run `make build`, restart the server, and reload the page. |
| `make run` reports that port 8000 is in use | On macOS, use `ce --port 8010 --open`. On Linux, run `uv run uvicorn app.main:app --host 127.0.0.1 --port 8010` from the checkout. |
| **Run pass** is disabled | Check **Settings → Account → Re-check** for an installed, signed-in provider. Wait for or cancel any active pass on the document. |
| A pass fails | Hover over **error** under **Settings → Runs** to read the message. Check the provider sign-in, CLI version, connection, and account usage limits. |
| **Save failed** or **Backup unavailable** | Keep the tab open, copy your draft somewhere safe, and retry after checking the server and browser storage. If the server was restarted, preserve unsaved text before reloading the page. |

For a bug report, include your OS, browser, provider CLI version, and the error
message, with invented text that reproduces it. See
[CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

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

## Development

After installation, run the frontend watchers and reload server together:

```bash
make dev
```

Run the automated checks:

```bash
make test
```

This runs pytest and the browser-free JavaScript tests. Tests use a temporary
database and stub model calls; a signed-in provider is not required. CI builds
and tests clean checkouts on macOS and Linux.

`make seed` adds a sample document with annotations to the configured database
if you want to explore the interface. See [CONTRIBUTING.md](CONTRIBUTING.md)
before submitting a change.

### Project layout

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

On Linux, use **Ctrl** wherever **Cmd** is shown. **Alt** is the **Option** key
on macOS.

Press `?` in the editor. `/` opens the block menu and Markdown shortcuts work;
`Cmd+Alt+M` adds a note on the selection; `Cmd+S` saves now. `Alt+↓`/`Alt+↑`
step through notes from anywhere and `Alt+Enter` accepts; when the cursor is
not in the text, `J`/`K` step, `A` accepts, `R` resolves, `X` dismisses with a
reason, `E` edits your note, `P` opens the pass menu, `H` the history. `Esc`
deselects or closes menus.

## License

MIT. See [LICENSE](LICENSE).
