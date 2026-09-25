"""Data access for documents, revisions, annotations, rules, passes, runs, and settings.

Every function takes an open sqlite3 connection; the request layer owns the
transaction. Revision policy lives in `save_content`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .content import EMPTY_DOC, annotation_ranges, loose_key, to_text, word_count

# A save after this much idle time closes the previous "session": the content
# as it stood before the edit is snapshotted so the end of each sitting is kept.
SESSION_GAP = timedelta(minutes=10)
# During one long sitting, checkpoint at least this often.
CHECKPOINT_EVERY = timedelta(minutes=30)

KINDS: dict[str, dict[str, str]] = {
    "note": {"label": "Note", "hint": "Comment on this passage"},
    "suggest": {"label": "Suggest", "hint": "Propose replacement text"},
    "question": {"label": "Question", "hint": "Ask the author something"},
    "cut": {"label": "Cut", "hint": "Propose removing this"},
    "check": {"label": "Fact check", "hint": "A claim to verify or correct"},
}
# "pending" = created by an AI run, highlight not yet placed in the text.
OPEN_STATUSES = ("open", "pending")
CLOSED_STATUSES = ("resolved", "accepted", "dismissed", "orphaned")
ALL_STATUSES = OPEN_STATUSES + CLOSED_STATUSES

DISMISS_REASONS: dict[str, str] = {
    "style": "Not my style",
    "deliberate": "Deliberate",
    "wrong": "Wrong",
    "keep": "Keep as is",
}

EFFORTS = ("low", "medium", "high")


class DocumentConflict(Exception):
    """The caller edited an older version of the document."""

    def __init__(self, version: int):
        self.version = version
        super().__init__("This document changed in another tab. Your draft has not replaced it.")


def check_version(doc: sqlite3.Row, expected_version: int | None) -> None:
    if expected_version is not None and doc["version"] != expected_version:
        raise DocumentConflict(doc["version"])


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def new_id() -> str:
    return uuid.uuid4().hex


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def fingerprint(kind: str, quote: str) -> str:
    """Identity of a finding for dismissal memory: same kind on the same span."""
    return hashlib.sha1(f"{kind}\n{loose_key(quote)}".encode()).hexdigest()


# ----------------------------------------------------------------------------
# Documents
# ----------------------------------------------------------------------------

def list_documents(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT d.*,
               (SELECT COUNT(*) FROM revisions r WHERE r.document_id = d.id) AS revision_count,
               (SELECT COUNT(*) FROM revisions r WHERE r.document_id = d.id AND r.is_major = 1) AS major_count,
               (SELECT COUNT(*) FROM annotations a WHERE a.document_id = d.id AND a.status IN ('open', 'pending')) AS open_annotations
        FROM documents d
        ORDER BY d.updated_at DESC
        """
    ).fetchall()


def get_document(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()


def create_document(conn: sqlite3.Connection, title: str = "Untitled", content: dict | None = None) -> sqlite3.Row:
    content = content or EMPTY_DOC
    doc_id = new_id()
    now = iso(utcnow())
    text = to_text(content)
    conn.execute(
        "INSERT INTO documents (id, title, content_json, content_text, word_count, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc_id, title, dumps(content), text, word_count(text), now, now),
    )
    _insert_revision(conn, doc_id, dumps(content), text, word_count(text), origin="create", note="Created", now=now)
    sync_annotations(conn, doc_id, annotation_ranges(content))
    return get_document(conn, doc_id)  # type: ignore[return-value]


def update_title(conn: sqlite3.Connection, doc_id: str, title: str, *, expected_version: int | None = None) -> int:
    doc = get_document(conn, doc_id)
    if doc is None:
        raise KeyError(doc_id)
    check_version(doc, expected_version)
    title = title.strip() or "Untitled"
    if title != doc["title"]:
        conn.execute("UPDATE documents SET title = ?, version = version + 1 WHERE id = ?", (title, doc_id))
        return doc["version"] + 1
    return doc["version"]


def delete_document(conn: sqlite3.Connection, doc_id: str) -> None:
    conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))


def save_content(conn: sqlite3.Connection, doc_id: str, content: dict, *, expected_version: int | None = None,
                 title: str | None = None, preserve_before: bool = False) -> dict[str, Any]:
    """Store new editor content and apply the automatic revision policy."""
    doc = get_document(conn, doc_id)
    if doc is None:
        raise KeyError(doc_id)
    check_version(doc, expected_version)
    new_json = dumps(content)
    title = doc["title"] if title is None else title.strip() or "Untitled"
    now_dt = utcnow()
    now = iso(now_dt)
    if new_json == doc["content_json"] and title == doc["title"]:
        changes = sync_annotations(conn, doc_id, annotation_ranges(content))
        return {"changed": False, "word_count": doc["word_count"], "saved_at": now, "revision": None,
                "annotation_changes": changes, "version": doc["version"]}

    created: sqlite3.Row | None = None
    latest = latest_revision(conn, doc_id)
    last_edit = parse_iso(doc["updated_at"])
    if preserve_before and (latest is None or latest["content_json"] != doc["content_json"]):
        created = _insert_revision(conn, doc_id, doc["content_json"], doc["content_text"], doc["word_count"],
                                   origin="auto", note="Before recovering a draft", now=now)
    elif (latest is None or latest["content_json"] != doc["content_json"]) and now_dt - last_edit >= SESSION_GAP:
        created = _insert_revision(conn, doc_id, doc["content_json"], doc["content_text"], doc["word_count"],
                                   origin="auto", note="End of session", now=iso(last_edit))

    text = to_text(content)
    wc = word_count(text)
    conn.execute(
        "UPDATE documents SET content_json = ?, content_text = ?, word_count = ?, updated_at = ?, "
        "title = ?, version = version + 1 WHERE id = ?",
        (new_json, text, wc, now, title, doc_id),
    )

    latest = latest_revision(conn, doc_id)
    if latest is None or now_dt - parse_iso(latest["created_at"]) >= CHECKPOINT_EVERY:
        if latest is None or latest["content_json"] != new_json:
            created = _insert_revision(conn, doc_id, new_json, text, wc, origin="auto", note="Checkpoint", now=now)

    changes = sync_annotations(conn, doc_id, annotation_ranges(content))
    return {"changed": True, "word_count": wc, "saved_at": now, "revision": created,
            "annotation_changes": changes, "version": doc["version"] + 1}


# ----------------------------------------------------------------------------
# Revisions
# ----------------------------------------------------------------------------

def _insert_revision(conn: sqlite3.Connection, doc_id: str, content_json: str, content_text: str, wc: int, *,
                     origin: str, note: str = "", is_major: bool = False, now: str | None = None) -> sqlite3.Row:
    now = now or iso(utcnow())
    seq = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM revisions WHERE document_id = ?", (doc_id,)).fetchone()[0]
    rev_id = new_id()
    conn.execute(
        "INSERT INTO revisions (id, document_id, seq, content_json, content_text, word_count, note, is_major, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (rev_id, doc_id, seq, content_json, content_text, wc, note, int(is_major), origin, now),
    )
    return get_revision(conn, doc_id, rev_id)  # type: ignore[return-value]


def list_revisions(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, document_id, seq, word_count, note, is_major, origin, created_at "
        "FROM revisions WHERE document_id = ? ORDER BY seq DESC",
        (doc_id,),
    ).fetchall()


def get_revision(conn: sqlite3.Connection, doc_id: str, rev_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM revisions WHERE document_id = ? AND id = ?", (doc_id, rev_id)).fetchone()


def get_revision_by_seq(conn: sqlite3.Connection, doc_id: str, seq: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM revisions WHERE document_id = ? AND seq = ?", (doc_id, seq)).fetchone()


def latest_revision(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM revisions WHERE document_id = ? ORDER BY seq DESC LIMIT 1", (doc_id,)
    ).fetchone()


def snapshot(conn: sqlite3.Connection, doc_id: str, note: str = "", is_major: bool = False,
             origin: str = "manual") -> sqlite3.Row:
    """Save the current content as a revision, or promote an identical latest one."""
    doc = get_document(conn, doc_id)
    if doc is None:
        raise KeyError(doc_id)
    latest = latest_revision(conn, doc_id)
    note = note.strip()
    if latest is not None and latest["content_json"] == doc["content_json"]:
        conn.execute(
            "UPDATE revisions SET note = CASE WHEN ? = '' THEN note ELSE ? END, "
            "is_major = is_major OR ?, origin = CASE WHEN ? = 'manual' THEN 'manual' ELSE origin END WHERE id = ?",
            (note, note, int(is_major), origin, latest["id"]),
        )
        return get_revision(conn, doc_id, latest["id"])  # type: ignore[return-value]
    return _insert_revision(conn, doc_id, doc["content_json"], doc["content_text"], doc["word_count"],
                            origin=origin, note=note, is_major=is_major)


def set_major(conn: sqlite3.Connection, doc_id: str, rev_id: str, is_major: bool) -> sqlite3.Row | None:
    conn.execute("UPDATE revisions SET is_major = ? WHERE document_id = ? AND id = ?", (int(is_major), doc_id, rev_id))
    return get_revision(conn, doc_id, rev_id)


def set_note(conn: sqlite3.Connection, doc_id: str, rev_id: str, note: str) -> sqlite3.Row | None:
    conn.execute("UPDATE revisions SET note = ? WHERE document_id = ? AND id = ?", (note.strip(), doc_id, rev_id))
    return get_revision(conn, doc_id, rev_id)


def restore_revision(conn: sqlite3.Connection, doc_id: str, rev_id: str, *,
                     expected_version: int | None = None) -> sqlite3.Row:
    doc = get_document(conn, doc_id)
    rev = get_revision(conn, doc_id, rev_id)
    if doc is None or rev is None:
        raise KeyError(rev_id)
    check_version(doc, expected_version)
    latest = latest_revision(conn, doc_id)
    if latest is None or latest["content_json"] != doc["content_json"]:
        _insert_revision(conn, doc_id, doc["content_json"], doc["content_text"], doc["word_count"],
                         origin="auto", note="Before restore")
    now = iso(utcnow())
    conn.execute(
        "UPDATE documents SET content_json = ?, content_text = ?, word_count = ?, updated_at = ?, "
        "version = version + 1 WHERE id = ?",
        (rev["content_json"], rev["content_text"], rev["word_count"], now, doc_id),
    )
    restored = _insert_revision(conn, doc_id, rev["content_json"], rev["content_text"], rev["word_count"],
                                origin="restore", note=f"Restored from revision {rev['seq']}", now=now)
    sync_annotations(conn, doc_id, annotation_ranges(json.loads(rev["content_json"])))
    return restored


# ----------------------------------------------------------------------------
# Annotations
# ----------------------------------------------------------------------------

def list_annotations(conn: sqlite3.Connection, doc_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM annotations WHERE document_id = ? "
        "ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'pending' THEN 0 ELSE 1 END, anchor_from ASC, created_at ASC",
        (doc_id,),
    ).fetchall()


def open_annotation_ids(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM annotations WHERE document_id = ? AND status IN ('open', 'pending')", (doc_id,)
    ).fetchall()
    return [r["id"] for r in rows]


def get_annotation(conn: sqlite3.Connection, doc_id: str, ann_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM annotations WHERE document_id = ? AND id = ?", (doc_id, ann_id)).fetchone()


def create_annotation(conn: sqlite3.Connection, doc_id: str, ann_id: str, kind: str, quote: str,
                      anchor_from: int, anchor_to: int) -> sqlite3.Row:
    if kind not in KINDS:
        kind = "note"
    now = iso(utcnow())
    conn.execute(
        "INSERT INTO annotations (id, document_id, kind, quote, anchor_from, anchor_to, status, author, "
        "fingerprint, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'open', 'you', ?, ?, ?)",
        (ann_id, doc_id, kind, quote, anchor_from, anchor_to, fingerprint(kind, quote), now, now),
    )
    return get_annotation(conn, doc_id, ann_id)  # type: ignore[return-value]


def create_ai_annotation(conn: sqlite3.Connection, doc_id: str, *, run_id: str, kind: str, quote: str, body: str,
                         suggested_text: str, paragraph_hint: int | None, anchor_from: int, anchor_to: int,
                         rule_id: str | None, rule_number: int | None, confidence: str) -> sqlite3.Row:
    ann_id = new_id()
    now = iso(utcnow())
    conn.execute(
        "INSERT INTO annotations (id, document_id, kind, body, suggested_text, quote, anchor_from, anchor_to, status, "
        "author, run_id, rule_id, rule_number, paragraph_hint, confidence, fingerprint, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'ai', ?, ?, ?, ?, ?, ?, ?, ?)",
        (ann_id, doc_id, kind, body, suggested_text, quote, anchor_from, anchor_to, run_id, rule_id, rule_number,
         paragraph_hint, confidence, fingerprint(kind, quote), now, now),
    )
    return get_annotation(conn, doc_id, ann_id)  # type: ignore[return-value]


def update_annotation(conn: sqlite3.Connection, doc_id: str, ann_id: str, *, kind: str, body: str,
                      suggested_text: str) -> sqlite3.Row | None:
    if kind not in KINDS:
        kind = "note"
    conn.execute(
        "UPDATE annotations SET kind = ?, body = ?, suggested_text = ?, updated_at = ? WHERE document_id = ? AND id = ?",
        (kind, body.strip(), suggested_text.strip(), iso(utcnow()), doc_id, ann_id),
    )
    return get_annotation(conn, doc_id, ann_id)


def set_annotation_status(conn: sqlite3.Connection, doc_id: str, ann_id: str, status: str,
                          reason: str = "") -> sqlite3.Row | None:
    if status not in ALL_STATUSES:
        raise ValueError(status)
    now = iso(utcnow())
    resolved_at = None if status in OPEN_STATUSES else now
    reason = reason if reason in DISMISS_REASONS else ""
    before = get_annotation(conn, doc_id, ann_id)
    conn.execute(
        "UPDATE annotations SET status = ?, updated_at = ?, resolved_at = ?, dismiss_reason = ? "
        "WHERE document_id = ? AND id = ?",
        (status, now, resolved_at, reason if status == "dismissed" else "", doc_id, ann_id),
    )
    if before is not None and status == "dismissed" and before["status"] != "dismissed" and before["rule_id"]:
        conn.execute("UPDATE rules SET dismissed_count = dismissed_count + 1 WHERE id = ?", (before["rule_id"],))
    return get_annotation(conn, doc_id, ann_id)


def bulk_status(conn: sqlite3.Connection, doc_id: str, ids: list[str], status: str, reason: str = "") -> list[str]:
    done = []
    for ann_id in ids:
        if set_annotation_status(conn, doc_id, ann_id, status, reason) is not None:
            done.append(ann_id)
    return done


def delete_annotation(conn: sqlite3.Connection, doc_id: str, ann_id: str) -> None:
    conn.execute("DELETE FROM annotations WHERE document_id = ? AND id = ?", (doc_id, ann_id))


def sync_annotations(conn: sqlite3.Connection, doc_id: str, ranges: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """Reconcile annotation rows with the marks actually present in the content.

    Open annotations whose mark vanished become orphaned; orphaned or pending
    ones whose mark is present become open. Anchors and quotes are refreshed.
    Pending annotations without a mark are left alone: the editor has not
    placed them yet. Returns the list of status changes.
    """
    now = iso(utcnow())
    changes: list[dict[str, str]] = []
    rows = conn.execute(
        "SELECT id, status FROM annotations WHERE document_id = ? AND status IN ('open', 'orphaned', 'pending')",
        (doc_id,),
    ).fetchall()
    for row in rows:
        rng = ranges.get(row["id"])
        if rng is not None:
            conn.execute(
                "UPDATE annotations SET anchor_from = ?, anchor_to = ?, quote = ?, status = 'open', "
                "resolved_at = NULL, updated_at = ? WHERE id = ?",
                (rng["from"], rng["to"], rng["quote"], now, row["id"]),
            )
            if row["status"] != "open":
                changes.append({"id": row["id"], "status": "open"})
        elif row["status"] == "open":
            conn.execute(
                "UPDATE annotations SET status = 'orphaned', resolved_at = ?, updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )
            changes.append({"id": row["id"], "status": "orphaned"})
    return changes


def pending_findings(conn: sqlite3.Connection, doc_id: str, run_id: str | None = None) -> list[dict[str, Any]]:
    """AI annotations awaiting placement by the editor."""
    sql = "SELECT * FROM annotations WHERE document_id = ? AND status = 'pending' AND author = 'ai'"
    args: list[Any] = [doc_id]
    if run_id:
        sql += " AND run_id = ?"
        args.append(run_id)
    rows = conn.execute(sql + " ORDER BY anchor_from", args).fetchall()
    return [{"id": r["id"], "kind": r["kind"], "quote": r["quote"], "paragraph": r["paragraph_hint"],
             "from": r["anchor_from"], "to": r["anchor_to"]} for r in rows]


def known_fingerprints(conn: sqlite3.Connection, doc_id: str) -> dict[str, str]:
    """fingerprint -> status for AI findings on this document (dismissal memory + de-dup)."""
    rows = conn.execute(
        "SELECT fingerprint, status FROM annotations WHERE document_id = ? AND author = 'ai' AND fingerprint != ''",
        (doc_id,),
    ).fetchall()
    out: dict[str, str] = {}
    for r in rows:
        # A closed verdict beats an open duplicate; either way the finding is not re-raised.
        if r["fingerprint"] not in out or r["status"] in CLOSED_STATUSES:
            out[r["fingerprint"]] = r["status"]
    return out


def dismissed_findings(conn: sqlite3.Connection, doc_id: str | None, limit: int = 40) -> list[sqlite3.Row]:
    """Findings the author closed without accepting: this document's, or recent ones anywhere."""
    if doc_id is not None:
        return conn.execute(
            "SELECT a.*, d.title AS doc_title FROM annotations a JOIN documents d ON d.id = a.document_id "
            "WHERE a.document_id = ? AND a.author = 'ai' AND a.status IN ('dismissed', 'resolved') "
            "ORDER BY a.resolved_at DESC LIMIT ?",
            (doc_id, limit),
        ).fetchall()
    return conn.execute(
        "SELECT a.*, d.title AS doc_title FROM annotations a JOIN documents d ON d.id = a.document_id "
        "WHERE a.author = 'ai' AND a.status = 'dismissed' ORDER BY a.resolved_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


# ----------------------------------------------------------------------------
# Rules
# ----------------------------------------------------------------------------

def list_rules(conn: sqlite3.Connection, enabled_only: bool = False) -> list[sqlite3.Row]:
    where = "WHERE enabled = 1" if enabled_only else ""
    return conn.execute(f"SELECT * FROM rules {where} ORDER BY sort_order, number").fetchall()


def get_rule(conn: sqlite3.Connection, rule_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM rules WHERE id = ?", (rule_id,)).fetchone()


def create_rule(conn: sqlite3.Connection, text: str, tag: str = "") -> sqlite3.Row | None:
    text = " ".join(text.split())
    if not text:
        return None
    now = iso(utcnow())
    number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM rules").fetchone()[0]
    order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM rules").fetchone()[0]
    rule_id = new_id()
    conn.execute(
        "INSERT INTO rules (id, number, text, tag, enabled, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, 1, ?, ?, ?)",
        (rule_id, number, text, tag.strip(), order, now, now),
    )
    return get_rule(conn, rule_id)


def update_rule(conn: sqlite3.Connection, rule_id: str, *, text: str | None = None, tag: str | None = None,
                enabled: bool | None = None) -> sqlite3.Row | None:
    rule = get_rule(conn, rule_id)
    if rule is None:
        return None
    new_text = " ".join(text.split()) if text is not None else rule["text"]
    new_tag = tag.strip() if tag is not None else rule["tag"]
    new_enabled = int(enabled) if enabled is not None else rule["enabled"]
    conn.execute(
        "UPDATE rules SET text = ?, tag = ?, enabled = ?, updated_at = ? WHERE id = ?",
        (new_text or rule["text"], new_tag, new_enabled, iso(utcnow()), rule_id),
    )
    return get_rule(conn, rule_id)


def delete_rule(conn: sqlite3.Connection, rule_id: str) -> None:
    conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))


def move_rule(conn: sqlite3.Connection, rule_id: str, direction: int) -> None:
    rules = list_rules(conn)
    ids = [r["id"] for r in rules]
    if rule_id not in ids:
        return
    i = ids.index(rule_id)
    j = i + (1 if direction > 0 else -1)
    if 0 <= j < len(ids):
        ids[i], ids[j] = ids[j], ids[i]
    for order, rid in enumerate(ids, start=1):
        conn.execute("UPDATE rules SET sort_order = ? WHERE id = ?", (order, rid))


def import_rules(conn: sqlite3.Connection, text: str) -> int:
    """One rule per line. A line like `Rhythm: Keep it short.` sets the tag."""
    count = 0
    existing = {loose_key(r["text"]) for r in list_rules(conn)}
    for line in text.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        tag = ""
        if ":" in line and len(line.split(":", 1)[0]) <= 20 and " " not in line.split(":", 1)[0].strip():
            tag, line = (part.strip() for part in line.split(":", 1))
        if loose_key(line) in existing:
            continue
        if create_rule(conn, line, tag) is not None:
            existing.add(loose_key(line))
            count += 1
    return count


def export_rules(conn: sqlite3.Connection) -> str:
    lines = []
    for r in list_rules(conn):
        prefix = f"{r['tag']}: " if r["tag"] else ""
        lines.append(f"{'' if r['enabled'] else '# '}{prefix}{r['text']}")
    return "\n".join(lines) + ("\n" if lines else "")


def bump_rule_fired(conn: sqlite3.Connection, counts: dict[str, int]) -> None:
    for rule_id, n in counts.items():
        conn.execute("UPDATE rules SET fired_count = fired_count + ? WHERE id = ?", (n, rule_id))


# ----------------------------------------------------------------------------
# Passes
# ----------------------------------------------------------------------------

DEFAULT_PASSES: list[dict[str, Any]] = [
    {
        "slug": "copy-edit", "name": "Copy edit", "is_primary": 1, "rule_mode": "all", "general_findings": 1,
        "allowed_kinds": "suggest,cut,question,check,note",
        "description": "Everything a senior copy editor would mark, plus every rule in your rulebook.",
        "instructions": (
            "Do a complete professional copy edit, the kind a senior copy editor at a good publisher does before a "
            "manuscript goes to proof. Report everything you would mark on paper: spelling, typos, grammar, punctuation, "
            "capitalisation and hyphenation consistency, agreement, tense and point-of-view slips, dangling or misplaced "
            "modifiers, unclear antecedents, repeated words, redundancy, wordiness, awkward or ambiguous phrasing, misused "
            "words, weak clichés, inconsistent names, places, times and facts, and continuity errors within the text. "
            "Fix what is clearly wrong with a suggestion; propose a cut for words that add nothing; ask a question where "
            "the author may have meant it; leave a note for a pattern that spans several places. Flag any factual "
            "claim you doubt as a check. Do not rephrase for taste, and do not smooth a voice that is doing something "
            "on purpose."
        ),
    },
    {
        "slug": "fact-check", "name": "Fact check", "rule_mode": "all", "general_findings": 1,
        "allowed_kinds": "check,note",
        "description": "Names, dates, numbers, places, quotations, and claims. Nothing about style.",
        "instructions": (
            "Fact-check the text as a research editor would before publication. Examine every checkable claim: names "
            "and their spellings, titles, dates, numbers and units, places, quotations and who said them, "
            "attributions, historical and technical statements, and anything the text asserts as true about the "
            "world. Also check the text against itself: names, timelines and figures that contradict an earlier "
            "passage. Report each doubtful or unverifiable claim as a check, saying what is claimed, what you believe "
            "is wrong or uncertain, and what the author should verify; when you are confident of the correct fact, "
            "give it as the replacement. Say nothing about style, grammar or phrasing."
        ),
    },
    {
        "slug": "proofread", "name": "Proofread", "rule_mode": "all", "general_findings": 1, "allowed_kinds": "suggest",
        "description": "Mechanical errors only. Never touches phrasing.",
        "instructions": (
            "Proofread only: spelling, typos, punctuation, capitalisation, spacing, doubled words, and obvious grammar "
            "slips. No rephrasing and no comments on style. Apply the rulebook only where a rule is mechanical."
        ),
    },
    {
        "slug": "line-edit", "name": "Line edit", "rule_mode": "all", "general_findings": 1,
        "allowed_kinds": "suggest,cut,note",
        "description": "Rhythm, word choice, redundancy. May propose cuts.",
        "instructions": (
            "Line edit at sentence level: rhythm, word choice, flat verbs, redundancy, sentences that trail off or "
            "carry two ideas. Propose the stronger line or a cut. Keep the author's voice; improve the sentence "
            "they wrote rather than writing yours. Do not praise; only report what should change."
        ),
    },
    {
        "slug": "tighten", "name": "Tighten", "rule_mode": "all", "general_findings": 1, "allowed_kinds": "cut,suggest",
        "description": "Cut 10–15% without losing beats.",
        "instructions": (
            "Tighten by ten to fifteen percent without losing a beat: throat-clearing openers, doubled adjectives, "
            "stage directions, restated ideas, qualifiers that soften nothing. Only cuts and shorter replacements."
        ),
    },
    {
        "slug": "reader", "name": "Read as a reader", "rule_mode": "none", "general_findings": 1,
        "allowed_kinds": "note,question",
        "description": "Where attention flags, what lands. No rewrites.",
        "instructions": (
            "Read as an attentive first-time reader, not an editor. Where did your attention flag, where were you "
            "confused, where did you stop believing it. Questions and notes only; no praise and no rewrites."
        ),
    },
]


def list_passes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM passes ORDER BY is_primary DESC, sort_order, name").fetchall()


def get_pass(conn: sqlite3.Connection, pass_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM passes WHERE id = ? OR slug = ?", (pass_id, pass_id)).fetchone()


def primary_pass(conn: sqlite3.Connection) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM passes WHERE is_primary = 1 ORDER BY sort_order LIMIT 1").fetchone()
    return row or conn.execute("SELECT * FROM passes ORDER BY sort_order LIMIT 1").fetchone()


def seed_passes(conn: sqlite3.Connection) -> None:
    """Insert any default pass whose slug is missing; never touches ones the user has edited."""
    existing = {r["slug"] for r in conn.execute("SELECT slug FROM passes").fetchall()}
    now = iso(utcnow())
    base = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM passes").fetchone()[0]
    for order, p in enumerate(DEFAULT_PASSES, start=1):
        if p["slug"] in existing:
            continue
        conn.execute(
            "INSERT INTO passes (id, slug, name, description, rule_mode, rule_tag, instructions, allowed_kinds, "
            "general_findings, model, effort, is_primary, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, '', '', ?, ?, ?, ?)",
            (new_id(), p["slug"], p["name"], p["description"], p["rule_mode"], p["instructions"], p["allowed_kinds"],
             p["general_findings"], p.get("is_primary", 0) if not existing else 0, base + order, now, now),
        )


def create_pass(conn: sqlite3.Connection, name: str = "New pass") -> sqlite3.Row:
    now = iso(utcnow())
    order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM passes").fetchone()[0]
    pass_id = new_id()
    conn.execute(
        "INSERT INTO passes (id, slug, name, description, rule_mode, rule_tag, instructions, allowed_kinds, "
        "general_findings, model, effort, is_primary, sort_order, created_at, updated_at) "
        "VALUES (?, NULL, ?, '', 'all', '', '', 'suggest,cut,question,note', 1, '', '', 0, ?, ?, ?)",
        (pass_id, name, order, now, now),
    )
    return get_pass(conn, pass_id)  # type: ignore[return-value]


def update_pass(conn: sqlite3.Connection, pass_id: str, **fields: Any) -> sqlite3.Row | None:
    allowed = {"name", "description", "rule_mode", "rule_tag", "instructions", "allowed_kinds", "general_findings",
               "provider", "model", "effort", "is_primary"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return get_pass(conn, pass_id)
    if sets.get("is_primary"):
        conn.execute("UPDATE passes SET is_primary = 0")
    sets["updated_at"] = iso(utcnow())
    assignments = ", ".join(f"{k} = ?" for k in sets)
    conn.execute(f"UPDATE passes SET {assignments} WHERE id = ?", (*sets.values(), pass_id))
    return get_pass(conn, pass_id)


def duplicate_pass(conn: sqlite3.Connection, pass_id: str) -> sqlite3.Row | None:
    src = get_pass(conn, pass_id)
    if src is None:
        return None
    new = create_pass(conn, f"{src['name']} (copy)")
    return update_pass(conn, new["id"], description=src["description"], rule_mode=src["rule_mode"],
                       rule_tag=src["rule_tag"], instructions=src["instructions"], allowed_kinds=src["allowed_kinds"],
                       general_findings=src["general_findings"], provider=src["provider"], model=src["model"],
                       effort=src["effort"])


def delete_pass(conn: sqlite3.Connection, pass_id: str) -> None:
    conn.execute("DELETE FROM passes WHERE id = ? AND is_primary = 0", (pass_id,))


# ----------------------------------------------------------------------------
# Runs
# ----------------------------------------------------------------------------

def create_run(conn: sqlite3.Connection, doc_id: str, pass_row: sqlite3.Row, *, model: str, effort: str,
               scope: str, revision_id: str | None, rules_snapshot: str, provider: str = "codex") -> sqlite3.Row:
    run_id = new_id()
    doc = get_document(conn, doc_id)
    conn.execute(
        "INSERT INTO runs (id, document_id, pass_id, pass_name, provider, model, effort, scope, status, revision_id, "
        "rules_snapshot, created_at, content_json, document_title) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
        (run_id, doc_id, pass_row["id"], pass_row["name"], provider, model, effort, scope, revision_id, rules_snapshot,
         iso(utcnow()), doc["content_json"], doc["title"]),
    )
    return get_run(conn, run_id)  # type: ignore[return-value]


def get_run(conn: sqlite3.Connection, run_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()


def update_run(conn: sqlite3.Connection, run_id: str, **fields: Any) -> None:
    if not fields:
        return
    assignments = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE runs SET {assignments} WHERE id = ?", (*fields.values(), run_id))


def latest_run(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE document_id = ? ORDER BY created_at DESC LIMIT 1", (doc_id,)
    ).fetchone()


def active_run(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM runs WHERE document_id = ? AND status IN ('queued', 'running') ORDER BY created_at DESC LIMIT 1",
        (doc_id,),
    ).fetchone()


def list_runs(conn: sqlite3.Connection, doc_id: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    if doc_id:
        return conn.execute(
            "SELECT r.*, d.title AS doc_title FROM runs r JOIN documents d ON d.id = r.document_id "
            "WHERE r.document_id = ? ORDER BY r.created_at DESC LIMIT ?", (doc_id, limit)
        ).fetchall()
    return conn.execute(
        "SELECT r.*, d.title AS doc_title FROM runs r JOIN documents d ON d.id = r.document_id "
        "ORDER BY r.created_at DESC LIMIT ?", (limit,)
    ).fetchall()


def run_counts(conn: sqlite3.Connection, run_id: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM annotations WHERE run_id = ? GROUP BY status", (run_id,)
    ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    counts["total"] = sum(counts.values())
    counts["open"] = counts.get("open", 0) + counts.get("pending", 0)
    return counts


def run_ai_annotation_ids(conn: sqlite3.Connection, run_id: str, statuses: tuple[str, ...] = OPEN_STATUSES) -> list[str]:
    marks = ",".join("?" for _ in statuses)
    rows = conn.execute(
        f"SELECT id FROM annotations WHERE run_id = ? AND status IN ({marks}) ORDER BY anchor_from DESC",
        (run_id, *statuses),
    ).fetchall()
    return [r["id"] for r in rows]


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))
