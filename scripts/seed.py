"""Seed a sample document that shows every part of the app.

Creates, if missing: a small rulebook, the default passes, a document with two
revisions, two notes by the author, and a finished Copy edit run whose findings
cover suggest, cut, question and check (with rule links and confidence), plus
one finding already dismissed so the dismissal memory has something to show.

Safe to run again: it does nothing when the sample document already exists,
and it never touches an existing rulebook.

    make seed          (or: uv run python scripts/seed.py)
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import repo  # noqa: E402
from app.content import annotation_ranges, paragraphs  # noqa: E402
from app.db import connect, init_db  # noqa: E402
from app.prompts import render_rules  # noqa: E402

TITLE = "The Keeper of Small Hours"


# ---------------------------------------------------------------- document builders

def t(text: str, *marks: dict) -> dict:
    node: dict = {"type": "text", "text": text}
    if marks:
        node["marks"] = list(marks)
    return node


def ann(aid: str, kind: str) -> dict:
    return {"type": "annotation", "attrs": {"id": aid, "kind": kind}}


def p(*children: dict) -> dict:
    return {"type": "paragraph", "content": list(children)}


def h(level: int, text: str) -> dict:
    return {"type": "heading", "attrs": {"level": level}, "content": [t(text)]}


ITALIC = {"type": "italic"}

# Deterministic ids so the seed can recognise its own rows.
N_VOICE, N_TURN = "seed0000000000000000000000000001", "seed0000000000000000000000000002"
F_DASH, F_PLAIN, F_CUT, F_QUERY, F_CHECK, F_DISMISSED = (
    "seed0000000000000000000000000011",
    "seed0000000000000000000000000012",
    "seed0000000000000000000000000013",
    "seed0000000000000000000000000014",
    "seed0000000000000000000000000015",
    "seed0000000000000000000000000016",
)

FIRST_DRAFT = {
    "type": "doc",
    "content": [
        h(1, TITLE),
        p(t("Marta had kept the lighthouse for eleven years, and in that time she had learned that the sea "
            "did not care about schedules. It came when it came.")),
        p(t("The lamp needed winding every four hours. She had set an alarm once, in the first year, and "
            "slept through it, and woke to a dark tower and a fishing boat that had turned back in time. "
            "She never set an alarm again.")),
        p(t("Her brother wrote from the city to ask when she would come home. She read the letters "
            "twice and put them in the drawer with the others.")),
    ],
}

CURRENT = {
    "type": "doc",
    "content": [
        h(1, TITLE),
        p(
            t("Marta had kept the lighthouse for eleven years, and in that time she had learned that the sea "),
            t("did not care about schedules", ann(N_VOICE, "note")),
            t(". It came when it came."),
        ),
        p(
            t("The lamp needed winding every "),
            t("four hours", ann(F_CHECK, "check")),
            t(". She had set an alarm once, in the first year, and slept through it, and woke to a dark tower "
              "and a fishing boat that had "),
            t("turned back in time", ann(F_QUERY, "question")),
            t(". She never set an alarm again."),
        ),
        p(
            t("Her brother wrote from the city to ask when she would come home. She read the letters "),
            t("twice — and put", ann(F_DASH, "suggest")),
            t(" them in the drawer with the others. "),
            t("She was not lonely, exactly, but she had begun to notice the shape of the word.",
              ann(N_TURN, "note")),
        ),
        p(
            t("On the nights when the fog came in, the light seemed to go nowhere at all. It "),
            t("illuminated", ann(F_PLAIN, "suggest")),
            t(" a wall of grey and stopped. "),
            t("This was, she thought, a very good metaphor for something, though she could not have said what.",
              ann(F_CUT, "cut")),
        ),
        p(
            t("She wound the lamp. She wrote nothing back. Outside, the sea was doing what it did, "),
            t("which was everything", ITALIC),
            t(", and none of it for her."),
        ),
    ],
}

RULES = [
    ("No em dashes. Use a comma or a full stop.", "Punctuation"),
    ("Prefer the plain word over the elevated one.", "Diction"),
    ("Short declaratives are deliberate; never join them.", "Rhythm"),
    ("Never explain an image after it is made.", "Craft"),
    ("Cut very, really, quite, rather.", "Diction"),
    ("If unsure whether something is deliberate, ask.", "Meta"),
]

# The author's own notes: kind is always "note".
NOTES = [
    (N_VOICE, "This line sets the whole voice. Protect it in every pass."),
    (N_TURN, "This is the emotional turn of the piece, but it arrives in a paragraph about letters. "
             "Consider giving it its own paragraph so it lands."),
]

# Findings from the Copy edit run: (id, kind, quote, rule number or None, replacement, comment, confidence)
FINDINGS = [
    (F_DASH, "suggest", "twice — and put", 1, "twice, and put",
     "Rule 1: a comma keeps the pause without the dash.", "high"),
    (F_PLAIN, "suggest", "illuminated", 2, "lit",
     "Rule 2: prefer the plain word. Everything around it is plain-spoken, so this one sticks out.", "high"),
    (F_CUT, "cut", "This was, she thought, a very good metaphor for something, though she could not have said what.", 4, "",
     "Rule 4: the fog image has just been given; this sentence explains it. It also carries a 'very' (rule 5).", "high"),
    (F_QUERY, "question", "turned back in time", 6, "",
     "Because of the dark, or in spite of it? Both readings are available on first pass.", "medium"),
    (F_CHECK, "check", "four hours", None, "",
     "Clockwork lighthouse lamps of this era usually ran under three hours between windings. "
     "Verify, or keep it as the story's own fact.", "low"),
]

# Already dismissed by the author, so the next run will not raise it and will learn from the reason.
DISMISSED = (F_DISMISSED, "suggest", "She never set an alarm again.", None, "She did not set an alarm again.",
             "'Never' overstates a single decision.", "medium", "style")

LETTER = ("Checked 6 rules. Clean on R3 and R5 on their own; R5 is caught inside the R4 cut. Fired: R1 ×1, R2 ×1, "
          "R4 ×1, R6 ×1. One fact check on the winding interval. Tense is consistent throughout and the voice holds.")


def paragraph_of(paras: list[dict], quote: str) -> int | None:
    for para in paras:
        if quote in para["text"]:
            return para["index"]
    return None


def main() -> None:
    conn = connect()
    init_db(conn)
    repo.seed_passes(conn)
    if conn.execute("SELECT 1 FROM documents WHERE title = ?", (TITLE,)).fetchone():
        print("Sample document already exists; nothing to do.")
        return

    conn.execute("BEGIN")

    # Rulebook, only when the user has none yet.
    if not repo.list_rules(conn):
        for text, tag in RULES:
            repo.create_rule(conn, text, tag)
    rules_by_number = {r["number"]: r for r in repo.list_rules(conn, enabled_only=True)}

    # Document with two revisions: the first draft and the current text (flagged major).
    doc = repo.create_document(conn, TITLE, FIRST_DRAFT)
    doc_id = doc["id"]
    repo.save_content(conn, doc_id, CURRENT)
    major = repo.snapshot(conn, doc_id, note="Second pass: added the fog paragraph and the ending", is_major=True)

    # The author's notes.
    ranges = annotation_ranges(CURRENT)
    for aid, body in NOTES:
        rng = ranges[aid]
        repo.create_annotation(conn, doc_id, aid, "note", rng["quote"], rng["from"], rng["to"])
        repo.update_annotation(conn, doc_id, aid, kind="note", body=body, suggested_text="")

    # A finished Copy edit run and its findings.
    pass_row = repo.get_pass(conn, "copy-edit")
    run = repo.create_run(conn, doc_id, pass_row, model="gpt-6-astra", effort="medium", scope="document",
                          revision_id=major["id"], rules_snapshot=render_rules(list(rules_by_number.values())),
                          provider="codex")
    paras = paragraphs(CURRENT)
    fired: dict[str, int] = {}
    for aid, kind, quote, rule_no, replacement, comment, confidence in FINDINGS + [DISMISSED[:7]]:
        rule = rules_by_number.get(rule_no) if rule_no else None
        rng = ranges.get(aid)
        conn.execute(  # create_ai_annotation would mint an id; the seed needs deterministic ones
            "INSERT INTO annotations (id, document_id, kind, body, suggested_text, quote, anchor_from, anchor_to, status, "
            "author, run_id, rule_id, rule_number, paragraph_hint, confidence, fingerprint, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'ai', ?, ?, ?, ?, ?, ?, ?, ?)",
            (aid, doc_id, kind, comment, replacement, quote, rng["from"] if rng else 0, rng["to"] if rng else 0,
             run["id"], rule["id"] if rule else None, rule["number"] if rule else None, paragraph_of(paras, quote),
             confidence, repo.fingerprint(kind, quote), repo.iso(repo.utcnow()), repo.iso(repo.utcnow())),
        )
        if rule is not None:
            fired[rule["id"]] = fired.get(rule["id"], 0) + 1
    repo.set_annotation_status(conn, doc_id, DISMISSED[0], "dismissed", DISMISSED[7])
    repo.bump_rule_fired(conn, fired)
    started = repo.utcnow() - timedelta(seconds=41)
    repo.update_run(conn, run["id"], status="done", started_at=repo.iso(started), finished_at=repo.iso(repo.utcnow()),
                    windows_total=1, windows_done=1, findings_count=len(FINDINGS) + 1, suppressed_count=0,
                    summary=LETTER)

    # Highlights already sit in the text, so pending findings become open and anchors are refreshed.
    repo.sync_annotations(conn, doc_id, ranges)
    conn.execute("COMMIT")
    print(f"Seeded “{TITLE}” at /d/{doc_id} with {len(NOTES)} notes, a Copy edit run of {len(FINDINGS)} findings "
          f"and 1 dismissed, {len(rules_by_number)} rules.")


if __name__ == "__main__":
    main()
