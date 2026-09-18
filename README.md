# Copy Editor

A workshop for prose. Write in a Notion-style editor, get a professional
copy edit from a model that runs under the subscriptions you already have,
and work through its findings the way you would with a human editor: accept,
correct, question, dismiss. Every dismissal teaches the next pass. Every
revision is kept.

No API keys. Passes run through the Codex CLI and Claude Code signed in on
your machine.

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
- **Dismissal memory.** Dismiss with a reason and the same finding is never
  raised again on that document; your reasons are fed to later passes so the
  model stops proposing that kind of change.
- **Revisions.** Automatic snapshots at session boundaries and before every
  pass, manual ones with a note and a *major* flag, word-level diffs, restore.
- **Export.** Copy or download as Markdown or plain text.
- **Keyboard.** `J`/`K` or `Alt+↓`/`Alt+↑` to step, `A` accept, `R` resolve,
  `X` dismiss, `?` for the full sheet.

## Quick start

Requirements: Python 3.12+ with [uv](https://docs.astral.sh/uv/), Node 20+,
and at least one signed-in model CLI:

- OpenAI: `npm i -g @openai/codex` then `codex login`
- Anthropic: Claude Code, then `claude auth login`

```bash
make setup      # uv sync + npm install
make cli        # puts the `ce` launcher on your PATH (~/.local/bin)
ce              # builds if needed, starts the app, prints a link; Ctrl+C stops it
```

`ce --open` also opens the browser, `ce --port 8010` picks a port. Without the
launcher: `make build` then `make run`, or `make dev` for watchers and reload.
`make seed` adds a sample document.

Then, in the app: Settings → Account to confirm your sign-ins, Settings → Rules
to write your first rules, and the **Edit pass** button in any document.

## How it works

**Documents** are stored as ProseMirror JSON in SQLite, with derived plain text
and a word count. Autosave runs a second after the last keystroke.

**Annotations** are marks inside the document, so highlights move with the
text and survive snapshots. The row holds the commentary: kind (`note`,
`suggest`, `question`, `cut`, `check`), body, replacement text, author, rule,
confidence, and status. `question` is strictly editorial; `check` is a factual
claim to verify. Pass findings can be accepted, resolved or dismissed but not
edited. On every save the server rescans the marks: highlights that were
deleted orphan their card, and pending findings whose highlight appears open.

**Passes** run in a worker thread. The document is split into ~1,500-word
windows of whole paragraphs; each window's prompt carries the rulebook, your
dismissal history, the pass instructions and the numbered paragraphs, and goes
to the chosen provider: `codex exec` with a strict JSON schema, an ephemeral
session and a read-only sandbox, or `claude -p --json-schema` with no tools
and no session kept. Replies name a paragraph and a verbatim quote per
finding; the open editor places each one by locating the quote (exact, then
normalised, then loose), and anything it cannot place is kept as an orphaned
card with its text intact. Results stream in per window.

**Models** come from Codex's live catalogue (current generation only, each
with its own reasoning levels) and the current Claude line-up. Defaults live in
Settings → Account; a pass can pin its own.

**Revisions** are full snapshots numbered per document: a session-end snapshot
when you return after ten idle minutes, a checkpoint every thirty minutes of
continuous work, one before every pass, and manual ones from the History
drawer. Restore snapshots the live text first.

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
| `docs/mockups/` | The design mockup and proposal for the AI passes. |
| `tests/` | pytest suite (`make test`). |

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
