"""Prompt assembly for editing passes, and the reply schema Codex must satisfy."""

from __future__ import annotations

import sqlite3
from typing import Any

from .content import numbered_text
from .repo import DISMISS_REASONS, KINDS

FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "paragraph": {"type": "integer"},
                    "quote": {"type": "string"},
                    "rule": {"type": ["integer", "null"]},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "replacement": {"type": ["string", "null"]},
                    "comment": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["paragraph", "quote", "rule", "kind", "replacement", "comment", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "findings"],
    "additionalProperties": False,
}

KIND_GUIDE = {
    "suggest": "suggest: something is wrong or clearly weaker; give the replacement text for exactly the quoted span.",
    "cut": "cut: the quoted span should go entirely; no replacement.",
    "question": "question: an editorial query about the writing itself (intent, ambiguity, structure, what the reader "
                "is meant to take). Never about facts; facts go under check.",
    "check": "check: a factual claim you doubt or cannot verify (a name, date, number, place, quotation, attribution, "
             "technical or historical statement, or an internal contradiction). Say what is claimed, what is wrong or "
             "uncertain, and what to verify. If you are confident of the correct fact, give it as the replacement for "
             "the quoted span; otherwise leave replacement null.",
    "note": "note: an observation or a pattern, no specific change.",
}


def _kinds_of(pass_row: sqlite3.Row) -> list[str]:
    kinds = [k.strip() for k in (pass_row["allowed_kinds"] or "").split(",") if k.strip() in KINDS]
    return kinds or ["suggest", "question", "note"]


def render_rules(rules: list[sqlite3.Row]) -> str:
    return "\n".join(f"R{r['number']}. {r['text']}" for r in rules)


def _dismissal_line(row: sqlite3.Row) -> str:
    quote = " ".join(row["quote"].split())
    if len(quote) > 140:
        quote = quote[:139] + "…"
    proposed = ""
    if row["kind"] == "suggest" and row["suggested_text"]:
        proposed = f' → "{row["suggested_text"]}"'
    elif row["kind"] == "cut":
        proposed = " → (cut)"
    reason = DISMISS_REASONS.get(row["dismiss_reason"], "")
    why = f" — author's reason: {reason.lower()}" if reason else ""
    rule = f" (R{row['rule_number']})" if row["rule_number"] else ""
    return f'- [{row["kind"]}{rule}] "{quote}"{proposed}{why}'


def build_prompt(*, doc_title: str, pass_row: sqlite3.Row, rules: list[sqlite3.Row], window: list[dict[str, Any]],
                 window_index: int, window_total: int, dismissed_here: list[sqlite3.Row],
                 dismissed_elsewhere: list[sqlite3.Row]) -> str:
    kinds = _kinds_of(pass_row)
    parts: list[str] = []

    parts.append(
        "You are a senior professional copy editor with twenty years of experience, working for the author of the "
        "text below. You are thorough, specific, and respectful of the author's voice. You never rewrite for taste."
    )
    parts.append(f"## This pass: {pass_row['name']}\n{pass_row['instructions'].strip()}")

    if rules:
        parts.append(
            "## The author's rules\nCheck every rule against every sentence. When a finding comes from a rule, set "
            "its `rule` to the rule number. Rules are the author's standing preferences; treat them as binding.\n"
            + render_rules(rules)
        )
        if not pass_row["general_findings"]:
            parts.append("Report only violations of the numbered rules. Leave everything else alone.")
        else:
            parts.append(
                "Also report anything a careful professional would mark that no rule covers; set `rule` to null for those."
            )

    if dismissed_here or dismissed_elsewhere:
        lines = ["## What the author has dismissed before",
                 "Do not raise these findings again, and do not raise findings of the same type. Read them as "
                 "evidence of the author's preferences and calibrate accordingly."]
        if dismissed_here:
            lines.append("In this document:")
            lines.extend(_dismissal_line(r) for r in dismissed_here)
        if dismissed_elsewhere:
            lines.append("In other documents:")
            lines.extend(_dismissal_line(r) for r in dismissed_elsewhere)
        parts.append("\n".join(lines))

    kind_lines = "\n".join(f"- {KIND_GUIDE[k]}" for k in kinds)
    parts.append(
        "## How to report\n"
        f"Allowed kinds for this pass:\n{kind_lines}\n"
        "- `paragraph` is the number in square brackets before the paragraph.\n"
        "- `quote` is copied verbatim from that paragraph: the smallest span that needs to change, from a single word "
        "up to about 40 words, and it must occur exactly once in that paragraph. Never paraphrase the quote.\n"
        "- One finding per issue; never report the same issue twice; never quote across paragraphs.\n"
        "- `replacement` is the full text that should replace the quote (suggest only); null otherwise.\n"
        "- `comment` is one or two plain sentences: what is wrong and why, for the author. Name the rule if there is one.\n"
        "- `confidence`: high when it is clearly an error, medium for judgement calls, low when you are guessing. "
        "For a check it is how sure you are that the claim is wrong.\n"
        "- Do not rewrite whole paragraphs, do not comment on the bracketed numbers, and do not invent facts.\n"
        "- If you are unsure whether something is deliberate, ask (question) rather than change.\n"
        "- Never praise. Report only what should change or what you need to ask.\n"
        "- `summary`: two to five plain sentences for the author: which rules fired and how often, which stayed "
        "clean, and any pattern that spans the text. No preamble.\n"
        "Report findings in document order."
    )

    scope = "The whole document follows." if window_total == 1 else (
        f"This is part {window_index} of {window_total} of the document. Report only on the paragraphs given."
    )
    parts.append(f"## Document: {doc_title}\n{scope} Bracketed numbers are references, not part of the text.\n\n"
                 + numbered_text(window))
    return "\n\n".join(parts) + "\n"
