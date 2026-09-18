# Write

A workshop tool for prose: a Notion-style editor with highlight-anchored
commentary in a Genius-style sidebar, editing passes run by a model under
your ChatGPT subscription, a rulebook the passes check against, and a revision
history you can flag.

**Stack:** Python 3.12 / FastAPI / Jinja2 · SQLite (stdlib) · HTMX · Tailwind v4
(built locally) · TipTap (ProseMirror) bundled with esbuild · Codex CLI as the
model runner.

## Run it

```bash
make setup    # uv sync + npm install
make build    # tailwind + esbuild + vendor htmx into app/static
make seed     # optional: a sample document with annotations and two revisions
make run      # http://localhost:8000
```

`make dev` runs the Tailwind and esbuild watchers alongside uvicorn `--reload`.
`make test` runs pytest.

Editing passes run through command-line tools already signed in on your
machine, under your subscriptions; no API key is used anywhere:

- **OpenAI** via the Codex CLI (`npm i -g @openai/codex`, then `codex login`).
  Models come from Codex's live catalog, current generation only.
- **Anthropic** via Claude Code (`claude auth login`). Models are the current
  Claude line-up (Fable 5.1, Opus 5, Sonnet 5, Haiku 4.5).

Both can be signed in from Settings → Account, and every pass picks a model
from either.

## What's here

| Path | Role |
| --- | --- |
| `app/main.py` | Routes. Pages are Jinja; HTMX partials for the sidebar, cards, revision panel, settings tabs, pass menu and run strip; JSON endpoints for the editor. |
| `app/repo.py` | Data access: documents, revisions, annotations, rules, passes, runs, settings. Revision policy and dismissal memory live here. |
| `app/runs.py` | The pass pipeline: snapshot, window the document, call Codex, validate findings, store them as pending annotations. |
| `app/providers.py` | Provider registry (`codex`, `claude`) with a uniform surface: status, models, run, sign-in. |
| `app/codex.py` | Codex CLI driver: sign-in status, device-code login, model catalog, `codex exec` with a JSON schema. |
| `app/claude_cli.py` | Claude Code driver: `claude auth status/login`, `claude -p` with `--json-schema`. |
| `app/prompts.py` | Prompt assembly (rules, dismissals, pass instructions, numbered paragraphs) and the reply schema. |
| `app/content.py` | ProseMirror JSON utilities: plain text, HTML rendering, annotation ranges, paragraph addressing, word diff. |
| `app/db.py` | SQLite connection, schema, idempotent migrations. DB at `data/write.db` (override with `WRITE_DB`). |
| `app/templates/` | `index`, `document` (editor), `settings`, `revision`, `revisions`, partials. |
| `frontend/editor.js` | Entry: editor setup, autosave, pass menu, placing findings, bulk accept, `window.Workshop`. |
| `frontend/placement.js` | Locates a finding's quote in the live document (anchors, hinted paragraph, unique match, loose match). |
| `frontend/annotation.js` | The `annotation` mark and its commands. |
| `frontend/sidebar.js` | Card positioning beside highlights, filters, prev/next stepping. |
| `frontend/slash.js`, `frontend/bubble.js` | The `/` block menu and the selection toolbar. |
| `frontend/app.css` | Tailwind source, component classes, highlight/card colours per kind. |
| `docs/mockups/` | The design mockup and proposal for the AI passes. |

## How the pieces fit

**Documents** are stored as ProseMirror JSON, plus derived plain text and word
count. Autosave `PUT`s the JSON about a second after the last keystroke, on
`Cmd+S`, and on tab hide/unload.

**Annotations** are a mark in the document (`<mark data-annotation-id>`), so
highlights move with the text and survive snapshots. The row in `annotations`
holds the commentary: kind (`note`, `suggest`, `question`, `cut`, `check`),
body, optional replacement text, author (`you` or `ai`), rule, confidence, and
status. `question` is strictly editorial; `check` is a factual claim to verify,
carrying a correction as its replacement when the model is confident, so it
can be applied with one click or marked Verified. The author writes notes only;
the other kinds come from passes, and a pass's findings can be accepted,
resolved or dismissed but not edited. On every
save the server rescans the marks: anchors and quotes are refreshed, open
annotations whose highlight was deleted become `orphaned`, and pending or
orphaned ones whose highlight appears become `open`.

**Sidebar** is its own scroll panel; cards sit in a plain list in document
order, so clusters never drift below their text. Activating from a highlight
scrolls the panel so the card is level with it; activating from a card scrolls
the page so the text comes to the card; while a pair is active the panel
follows page scrolling to keep them level, until you scroll the panel yourself.
A connector line links the active pair, and hovering either side draws a
fainter one. Filters (all, yours, per pass) hide cards; prev/next walks what is
visible.

**Rules** are one-line rows with a stable number, a tag, an on/off flag, and
fired/dismissed counters. Import and export are plain text, one rule per line
(`Tag: text` sets the tag). Every enabled rule is rendered into the prompt of
any pass whose rule mode is "all" (or "tag").

**Passes** are presets: instructions, allowed kinds, whether to report general
findings beyond the rules, optional pinned model and effort. Copy edit is the
primary one and ships with a full professional remit; Fact check examines
claims only and says nothing about style.

**Runs.** Starting a pass saves a revision, creates a run row, and starts a
worker thread. The worker splits the document into ~1,500-word windows of
whole paragraphs, builds a prompt per window (rules, dismissal history, pass
instructions, numbered paragraphs), and hands it to the chosen provider:
`codex exec` with an ephemeral session, a read-only sandbox, and a strict JSON
schema, or `claude -p` with `--json-schema`, no tools, and no session kept.
Each model offers its own reasoning levels; the run menu adjusts as you pick. Each finding names a
paragraph, a verbatim quote, a kind, an optional rule number and replacement.
Findings are validated, de-duplicated against anything already raised or
dismissed on the document (same kind on the same span), anchored where
possible, and stored as `pending` annotations. The editor page polls the run
strip every two seconds, places pending findings by locating their quote in
the live document, and autosaves; the next sync flips them to `open`. Quotes
that cannot be found become orphaned cards with their text and comment intact.

**Dismissal memory.** Dismissing (or resolving) an AI finding records it.
Later runs on that document skip identical findings, and the prompt lists the
document's dismissals plus recent dismissals from other documents, with the
reason you gave, so the model stops proposing that type of change.

**Revisions** are full snapshots, numbered per document. Automatic ones come
from `repo.save_content` (end of a sitting after ten idle minutes; a checkpoint
every thirty minutes of continuous work) and from each pass ("Before … pass").
Manual snapshots take a note and a **major** flag. Restore snapshots the live
text first. The revision page diffs word by word against the previous revision
or the current text.

**Export.** The header's Export menu copies the document to the clipboard or
downloads it as Markdown (`/d/{id}/export.md`) or plain text (`export.txt`).
Both come from one server-side serializer in `app/content.py`; copying flushes
unsaved edits first. The title is prepended as an H1 unless the document
already opens with it; highlights are dropped, formatting is kept in Markdown
and stripped in plain text (list markers stay).

## Keyboard shortcuts

Press `?` in the editor for the sheet. In short: `/` opens the block menu and
Markdown shortcuts work (`# `, `- `, `> `, `**bold**`); `Cmd+Alt+M` adds a
note on the selection; `Cmd+S` saves now. `Alt+↓` / `Alt+↑` step through notes
from anywhere, `Alt+Enter` accepts the active suggestion; when the cursor is
not in the text, `J`/`K` step, `A` accepts, `R` resolves, `X` dismisses with a
reason, `E` edits your note, `P` opens the pass menu, `H` the history. `Esc`
deselects or closes menus.
