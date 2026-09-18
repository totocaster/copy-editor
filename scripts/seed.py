"""Insert a sample document with a few annotations and two revisions."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import repo  # noqa: E402
from app.content import annotation_ranges  # noqa: E402
from app.db import connect, init_db  # noqa: E402


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

A_NOTE, A_SUGGEST, A_QUESTION, A_CUT, A_PRAISE = (
    "seed0000000000000000000000000001",
    "seed0000000000000000000000000002",
    "seed0000000000000000000000000003",
    "seed0000000000000000000000000004",
    "seed0000000000000000000000000005",
)

FIRST_DRAFT = {
    "type": "doc",
    "content": [
        h(1, "The Keeper of Small Hours"),
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
        h(1, "The Keeper of Small Hours"),
        p(
            t("Marta had kept the lighthouse for eleven years, and in that time she had learned that the sea "),
            t("did not care about schedules", ann(A_PRAISE, "note")),
            t(". It came when it came."),
        ),
        p(
            t("The lamp needed winding every four hours. She had set an alarm once, in the first year, and "
              "slept through it, and woke to a dark tower and a fishing boat that had "),
            t("turned back in time", ann(A_QUESTION, "question")),
            t(". She never set an alarm again."),
        ),
        p(
            t("Her brother wrote from the city to ask when she would come home. She read the letters twice "
              "and put them in the drawer with the others. "),
            t("She was not lonely, exactly, but she had begun to notice the shape of the word.",
              ann(A_NOTE, "note")),
        ),
        p(
            t("On the nights when the fog came in, the light seemed to go nowhere at all. It "),
            t("illuminated", ann(A_SUGGEST, "suggest")),
            t(" a wall of grey and stopped. "),
            t("This was, she thought, a very good metaphor for something, though she could not have said what.",
              ann(A_CUT, "cut")),
        ),
        p(
            t("She wound the lamp. She wrote nothing back. Outside, the sea was doing what it did, "),
            t("which was everything", ITALIC),
            t(", and none of it for her."),
        ),
    ],
}

ANNOTATIONS = [
    (A_PRAISE, "note", "Clean, unfussy, and it sets the whole voice. Keep it exactly like this.", ""),
    (A_QUESTION, "question", "Turned back because of the dark, or in spite of it? The stakes are unclear on first read.", ""),
    (A_NOTE, "note", "This is the emotional turn of the piece, but it arrives in a paragraph about letters. "
                     "Consider giving it its own paragraph so it lands.", ""),
    (A_SUGGEST, "suggest", "'Illuminated' is a bit clinical for her register.", "lit"),
    (A_CUT, "cut", "The narrator winks at the reader here. The fog image already does the work.", ""),
]


def main() -> None:
    conn = connect()
    init_db(conn)
    if conn.execute("SELECT 1 FROM documents WHERE title = ?", ("The Keeper of Small Hours",)).fetchone():
        print("Sample document already exists; nothing to do.")
        return
    conn.execute("BEGIN")
    doc = repo.create_document(conn, "The Keeper of Small Hours", FIRST_DRAFT)
    repo.save_content(conn, doc["id"], CURRENT)
    repo.snapshot(conn, doc["id"], note="Second pass: added the fog paragraph and the ending", is_major=True)
    ranges = annotation_ranges(CURRENT)
    for aid, kind, body, suggested in ANNOTATIONS:
        rng = ranges[aid]
        repo.create_annotation(conn, doc["id"], aid, kind, rng["quote"], rng["from"], rng["to"])
        repo.update_annotation(conn, doc["id"], aid, kind=kind, body=body, suggested_text=suggested)
    repo.sync_annotations(conn, doc["id"], ranges)
    conn.execute("COMMIT")
    print(f"Seeded document {doc['id']} at /d/{doc['id']}")


if __name__ == "__main__":
    main()
