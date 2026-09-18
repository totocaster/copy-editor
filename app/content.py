"""Utilities for TipTap/ProseMirror document JSON.

The editor stores documents as ProseMirror JSON. Everything the server needs
from that JSON lives here: plain text, HTML rendering for read-only views,
annotation ranges (derived from marks, so anchors always match the content),
and word-level diffs between revisions.
"""

from __future__ import annotations

import difflib
import html
import json
import re
from typing import Any

EMPTY_DOC: dict[str, Any] = {"type": "doc", "content": [{"type": "paragraph"}]}

TEXTBLOCKS = {"paragraph", "heading", "codeBlock"}
LEAF_NODES = {"horizontalRule", "hardBreak", "image"}
MARK_TAGS = {"bold": "strong", "italic": "em", "strike": "s", "code": "code", "underline": "u"}

WORD_RE = re.compile(r"[\w'’-]+", re.UNICODE)
DIFF_TOKEN_RE = re.compile(r"\n+|[^\S\n]+|\S+")


def utf16_len(s: str) -> int:
    """ProseMirror positions count UTF-16 code units, like JS string length."""
    return len(s.encode("utf-16-le")) // 2


def word_count(text: str) -> int:
    return len(WORD_RE.findall(text))


def _inline_text(node: dict) -> str:
    out: list[str] = []
    for child in node.get("content") or []:
        t = child.get("type")
        if t == "text":
            out.append(child.get("text", ""))
        elif t == "hardBreak":
            out.append("\n")
        else:
            out.append(_inline_text(child))
    return "".join(out)


def to_text(doc: dict) -> str:
    """Plain text: one line per text block, blocks separated by a blank line."""
    lines: list[str] = []

    def visit(node: dict) -> None:
        if node.get("type") in TEXTBLOCKS:
            lines.append(_inline_text(node))
        else:
            for child in node.get("content") or []:
                visit(child)

    visit(doc)
    return "\n\n".join(lines)


def _render_text(node: dict) -> str:
    s = html.escape(node.get("text", ""))
    for mark in reversed(node.get("marks") or []):
        mt = mark.get("type")
        attrs = mark.get("attrs") or {}
        if mt in MARK_TAGS:
            tag = MARK_TAGS[mt]
            s = f"<{tag}>{s}</{tag}>"
        elif mt == "link":
            href = html.escape(str(attrs.get("href", "")), quote=True)
            s = f'<a href="{href}" rel="noopener">{s}</a>'
        elif mt == "annotation":
            aid = html.escape(str(attrs.get("id", "")), quote=True)
            kind = html.escape(str(attrs.get("kind", "note")), quote=True)
            s = f'<mark class="annotation" data-annotation-id="{aid}" data-kind="{kind}">{s}</mark>'
    return s


def _render_node(node: dict) -> str:
    t = node.get("type")
    children = node.get("content") or []

    def inner() -> str:
        return "".join(_render_node(c) for c in children)

    if t == "text":
        return _render_text(node)
    if t == "paragraph":
        return f"<p>{inner()}</p>"
    if t == "heading":
        level = int((node.get("attrs") or {}).get("level", 1))
        level = min(max(level, 1), 6)
        return f"<h{level}>{inner()}</h{level}>"
    if t == "bulletList":
        return f"<ul>{inner()}</ul>"
    if t == "orderedList":
        start = int((node.get("attrs") or {}).get("start", 1))
        attr = f' start="{start}"' if start != 1 else ""
        return f"<ol{attr}>{inner()}</ol>"
    if t == "listItem":
        return f"<li>{inner()}</li>"
    if t == "blockquote":
        return f"<blockquote>{inner()}</blockquote>"
    if t == "codeBlock":
        return f"<pre><code>{inner()}</code></pre>"
    if t == "horizontalRule":
        return "<hr>"
    if t == "hardBreak":
        return "<br>"
    return inner()


def to_html(doc: dict) -> str:
    """Render document JSON to static HTML for read-only views."""
    return "".join(_render_node(c) for c in doc.get("content") or [])


def annotation_ranges(doc: dict) -> dict[str, dict[str, Any]]:
    """Find every annotation mark in the document.

    Returns {annotation_id: {"from", "to", "kind", "quote"}} using ProseMirror
    positions, so the server can order annotations and refresh their quotes
    without trusting client-supplied offsets.
    """
    found: dict[str, dict[str, Any]] = {}

    def note_text(aid: str, kind: str, pos: int, text: str) -> None:
        entry = found.get(aid)
        if entry is None:
            entry = found[aid] = {"from": pos, "to": pos, "kind": kind, "parts": [], "last_end": None}
        if entry["last_end"] == pos and entry["parts"]:
            entry["parts"][-1] += text
        else:
            entry["parts"].append(text)
        entry["last_end"] = pos + utf16_len(text)
        entry["to"] = entry["last_end"]

    def walk(node: dict, pos: int) -> int:
        t = node.get("type")
        if t == "text":
            text = node.get("text", "")
            for mark in node.get("marks") or []:
                if mark.get("type") == "annotation":
                    attrs = mark.get("attrs") or {}
                    aid = attrs.get("id")
                    if aid:
                        note_text(str(aid), str(attrs.get("kind") or "note"), pos, text)
            return utf16_len(text)
        if t in LEAF_NODES:
            if t == "hardBreak":
                for entry in found.values():
                    if entry["last_end"] == pos:
                        entry["parts"][-1] += "\n"
                        entry["last_end"] = pos + 1
            return 1
        is_doc = t == "doc"
        inner = pos if is_doc else pos + 1
        for child in node.get("content") or []:
            inner += walk(child, inner)
        return inner - pos if is_doc else inner - pos + 1

    walk(doc, 0)
    result: dict[str, dict[str, Any]] = {}
    for aid, entry in found.items():
        result[aid] = {
            "from": entry["from"],
            "to": entry["to"],
            "kind": entry["kind"],
            "quote": " … ".join(p.strip("\n") for p in entry["parts"]).strip(),
        }
    return result


def diff_html(old_text: str, new_text: str) -> str:
    """Word-level diff as HTML with <ins>/<del>. Render inside white-space: pre-wrap."""
    a = DIFF_TOKEN_RE.findall(old_text)
    b = DIFF_TOKEN_RE.findall(new_text)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    out: list[str] = []

    def emit(tokens: list[str], tag: str | None = None) -> None:
        s = html.escape("".join(tokens))
        out.append(f"<{tag}>{s}</{tag}>" if tag else s)

    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            emit(a[i1:i2])
        elif op == "delete":
            emit(a[i1:i2], "del")
        elif op == "insert":
            emit(b[j1:j2], "ins")
        else:
            emit(a[i1:i2], "del")
            emit(b[j1:j2], "ins")
    return "".join(out)


def diff_stats(old_text: str, new_text: str) -> dict[str, int]:
    a = WORD_RE.findall(old_text)
    b = WORD_RE.findall(new_text)
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    added = removed = 0
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op in ("delete", "replace"):
            removed += i2 - i1
        if op in ("insert", "replace"):
            added += j2 - j1
    return {"added": added, "removed": removed}


# ----------------------------------------------------------------------------
# Paragraph addressing for AI passes
# ----------------------------------------------------------------------------

# One-to-one character normalisation so offsets survive: curly quotes, dashes, nbsp.
_NORMALISE = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "‒": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", "…": ".",
})


def normalise(s: str) -> str:
    """Same-length normalisation used for quote matching."""
    return s.translate(_NORMALISE)


def loose_key(s: str) -> str:
    """Whitespace-collapsed, lower-cased, normalised form for fingerprints."""
    return " ".join(normalise(s).lower().split())


def paragraphs(doc: dict) -> list[dict[str, Any]]:
    """Every text block in document order, 1-based, with its text and PM positions.

    `text` uses "\\n" for hard breaks, so a UTF-16 offset into it maps to a
    ProseMirror position as `from + utf16_len(text[:offset])`.
    """
    out: list[dict[str, Any]] = []

    def walk(node: dict, pos: int) -> int:
        t = node.get("type")
        if t == "text":
            return utf16_len(node.get("text", ""))
        if t in LEAF_NODES:
            return 1
        is_doc = t == "doc"
        inner = pos if is_doc else pos + 1
        if t in TEXTBLOCKS:
            text = _inline_text(node)
            out.append({"index": len(out) + 1, "type": t, "text": text,
                        "from": inner, "to": inner + utf16_len(text)})
        for child in node.get("content") or []:
            inner += walk(child, inner)
        return inner - pos if is_doc else inner - pos + 1

    walk(doc, 0)
    return out


def locate_quote(paragraph: dict[str, Any], quote: str) -> tuple[int, int] | None:
    """PM positions of `quote` inside a paragraph, exact first then normalised."""
    text = paragraph["text"]
    quote = quote.strip()
    if not quote:
        return None
    i = text.find(quote)
    if i < 0:
        i = normalise(text).find(normalise(quote))
    if i < 0:
        return None
    start = paragraph["from"] + utf16_len(text[:i])
    return start, start + utf16_len(text[i:i + len(quote)])


def windows(paras: list[dict[str, Any]], max_words: int = 1500) -> list[list[dict[str, Any]]]:
    """Group consecutive non-empty paragraphs so each window stays under max_words."""
    result: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    count = 0
    for p in paras:
        if not p["text"].strip():
            continue
        w = word_count(p["text"])
        if current and count + w > max_words:
            result.append(current)
            current, count = [], 0
        current.append(p)
        count += w
    if current:
        result.append(current)
    return result


def numbered_text(paras: list[dict[str, Any]]) -> str:
    return "\n\n".join(f"[{p['index']}] {p['text']}" for p in paras if p["text"].strip())


# ----------------------------------------------------------------------------
# Export: Markdown and plain text
# ----------------------------------------------------------------------------

LIST_TYPES = {"bulletList", "orderedList"}


def _inline_runs(node: dict) -> list[tuple[str, tuple | None]]:
    """Flatten inline content into (text, marks) runs, merging neighbours with the
    same marks. Annotation marks are dropped. A hard break is (\"\\n\", None)."""
    runs: list[tuple[str, tuple | None]] = []
    for child in node.get("content") or []:
        t = child.get("type")
        if t == "text":
            marks = tuple(sorted(
                (m.get("type"), json.dumps(m.get("attrs") or {}, sort_keys=True))
                for m in child.get("marks") or [] if m.get("type") != "annotation"
            ))
            text = child.get("text", "")
            if runs and runs[-1][1] is not None and runs[-1][1] == marks:
                runs[-1] = (runs[-1][0] + text, marks)
            else:
                runs.append((text, marks))
        elif t == "hardBreak":
            runs.append(("\n", None))
        else:
            runs.extend(_inline_runs(child))
    return runs


def _render_run(text: str, marks: tuple | None, plain: bool) -> str:
    if marks is None:
        return "\n" if plain else "  \n"
    types = {t: json.loads(a) for t, a in marks}
    if plain:
        href = (types.get("link") or {}).get("href")
        return f"{text} ({href})" if href and text.strip() else text
    if not types:
        return text
    core = text.strip()
    if not core:
        return text
    lead = text[: len(text) - len(text.lstrip())]
    trail = text[len(text.rstrip()):]
    if "code" in types:
        core = f"`{core}`"
    else:
        if "bold" in types:
            core = f"**{core}**"
        if "italic" in types:
            core = f"*{core}*"
        if "strike" in types:
            core = f"~~{core}~~"
    if "link" in types:
        core = f"[{core}]({types['link'].get('href') or ''})"
    return lead + core + trail


def _render_inline(node: dict, plain: bool) -> str:
    return "".join(_render_run(text, marks, plain) for text, marks in _inline_runs(node))


def _render_blocks(nodes: list[dict], plain: bool) -> str:
    parts = [s for s in (_render_block(n, plain) for n in nodes) if s is not None]
    return "\n\n".join(parts)


def _render_item(item: dict, plain: bool) -> str:
    """A list item's blocks; a nested list hugs the paragraph above it."""
    out = ""
    children = item.get("content") or []
    for i, child in enumerate(children):
        s = _render_block(child, plain)
        if s is None:
            continue
        if out:
            out += "\n" if child.get("type") in LIST_TYPES else "\n\n"
        out += s
    return out


def _render_list(node: dict, plain: bool, ordered: bool) -> str:
    items = node.get("content") or []
    start = int((node.get("attrs") or {}).get("start") or 1) if ordered else 1
    loose = any(
        sum(1 for c in (item.get("content") or []) if c.get("type") not in LIST_TYPES) > 1 for item in items
    )
    rendered = []
    for i, item in enumerate(items):
        marker = f"{start + i}. " if ordered else "- "
        pad = " " * len(marker)
        lines = _render_item(item, plain).splitlines() or [""]
        text = marker + lines[0]
        for ln in lines[1:]:
            text += "\n" + (pad + ln if ln else "")
        rendered.append(text)
    return ("\n\n" if loose else "\n").join(rendered)


def _render_block(node: dict, plain: bool) -> str | None:
    t = node.get("type")
    if t == "paragraph":
        text = _render_inline(node, plain)
        return text if text.strip() else None
    if t == "heading":
        level = min(max(int((node.get("attrs") or {}).get("level", 1)), 1), 6)
        text = _render_inline(node, plain).strip()
        if not text:
            return None
        return text if plain else f"{'#' * level} {text}"
    if t == "bulletList":
        return _render_list(node, plain, ordered=False)
    if t == "orderedList":
        return _render_list(node, plain, ordered=True)
    if t == "blockquote":
        inner = _render_blocks(node.get("content") or [], plain)
        if not inner:
            return None
        if plain:
            return inner
        return "\n".join(("> " + ln) if ln else ">" for ln in inner.splitlines())
    if t == "codeBlock":
        text = _inline_text(node)
        if plain:
            return text
        lang = (node.get("attrs") or {}).get("language") or ""
        return f"```{lang}\n{text}\n```"
    if t == "horizontalRule":
        return None if plain else "---"
    if t == "listItem":
        return _render_item(node, plain) or None
    inner = _render_blocks(node.get("content") or [], plain)
    return inner or None


def _title_is_first_heading(doc: dict, title: str) -> bool:
    first = (doc.get("content") or [{}])[0]
    if first.get("type") != "heading":
        return False
    return " ".join(_inline_text(first).split()).casefold() == " ".join(title.split()).casefold()


def _export(doc: dict, title: str | None, plain: bool) -> str:
    body = _render_blocks(doc.get("content") or [], plain)
    title = " ".join((title or "").split())
    if title and not _title_is_first_heading(doc, title):
        head = title if plain else f"# {title}"
        body = f"{head}\n\n{body}" if body else head
    return body + "\n" if body else ""


def to_markdown(doc: dict, title: str | None = None) -> str:
    """Light Markdown: headings, emphasis, links, lists, quotes, code, rules.
    The title is prepended as an H1 unless the document already opens with it."""
    return _export(doc, title, plain=False)


def to_plaintext(doc: dict, title: str | None = None) -> str:
    """Plain text with list markers kept and everything else stripped."""
    return _export(doc, title, plain=True)
