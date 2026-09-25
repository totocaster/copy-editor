"""Editing-pass runs: start, execute in a worker thread, cancel."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
import time
import traceback
from typing import Any

from . import providers, repo
from .content import locate_quote, paragraphs, windows
from .db import connect
from .errors import ProviderError
from .prompts import FINDINGS_SCHEMA, build_prompt

MAX_WINDOW_WORDS = 1500

_procs: dict[str, subprocess.Popen[str]] = {}
_procs_lock = threading.Lock()


class RunError(Exception):
    pass


def select_rules(conn: sqlite3.Connection, pass_row: sqlite3.Row) -> list[sqlite3.Row]:
    mode = pass_row["rule_mode"]
    if mode == "none":
        return []
    rules = repo.list_rules(conn, enabled_only=True)
    if mode == "tag" and pass_row["rule_tag"]:
        tag = pass_row["rule_tag"].strip().lower()
        rules = [r for r in rules if r["tag"].strip().lower() == tag]
    return rules


def start_run(conn: sqlite3.Connection, doc_id: str, pass_id: str, *, model: str, effort: str,
              scope: str = "document", snapshot: bool = True, provider: str = "codex") -> sqlite3.Row:
    """Create the run row (inside the caller's transaction). Call `launch` after commit."""
    doc = repo.get_document(conn, doc_id)
    pass_row = repo.get_pass(conn, pass_id)
    if doc is None or pass_row is None:
        raise RunError("Unknown document or pass")
    if repo.active_run(conn, doc_id) is not None:
        raise RunError("A pass is already running on this document")
    try:
        mod = providers.get(provider)
    except ProviderError as exc:
        raise RunError(str(exc))
    st = mod.status()
    if not st["installed"]:
        raise RunError(f"{providers.short_label(provider)} is not installed")
    if not st["signed_in"]:
        raise RunError(f"{providers.short_label(provider)} is not signed in. Sign in from Settings → Account.")
    rules = select_rules(conn, pass_row)
    rules_snapshot = "\n".join(f"R{r['number']}. {r['text']}" for r in rules)
    revision_id = None
    if snapshot:
        rev = repo.snapshot(conn, doc_id, note=f"Before {pass_row['name']} pass", origin="auto")
        revision_id = rev["id"]
    return repo.create_run(conn, doc_id, pass_row, model=model, effort=effort, scope=scope,
                           revision_id=revision_id, rules_snapshot=rules_snapshot, provider=provider)


def launch(run_id: str) -> None:
    threading.Thread(target=execute_run, args=(run_id,), name=f"run-{run_id[:8]}", daemon=True).start()


def cancel_run(conn: sqlite3.Connection, run_id: str) -> None:
    run = repo.get_run(conn, run_id)
    if run is None or run["status"] not in ("queued", "running"):
        return
    repo.update_run(conn, run_id, status="cancelled", finished_at=repo.iso(repo.utcnow()))
    with _procs_lock:
        proc = _procs.get(run_id)
    if proc is not None and proc.poll() is None:
        proc.terminate()


def _register(run_id: str, proc: subprocess.Popen[str]) -> None:
    with _procs_lock:
        _procs[run_id] = proc


def _unregister(run_id: str) -> None:
    with _procs_lock:
        _procs.pop(run_id, None)


def _is_cancelled(conn: sqlite3.Connection, run_id: str) -> bool:
    run = repo.get_run(conn, run_id)
    return run is None or run["status"] == "cancelled"


def _wait_for_run(conn: sqlite3.Connection, run_id: str, timeout: float = 5.0) -> sqlite3.Row | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = repo.get_run(conn, run_id)
        if run is not None:
            return run
        time.sleep(0.1)
    return None


def materialise_finding(conn: sqlite3.Connection, doc_id: str, run_id: str, finding: dict[str, Any], *,
                        paras: list[dict[str, Any]], rules_by_number: dict[int, sqlite3.Row], allowed: list[str],
                        general_ok: bool, known: dict[str, str]) -> sqlite3.Row | None:
    """Validate one model finding and store it as a pending annotation, or return None if dropped."""
    quote = " ".join(str(finding.get("quote") or "").split())
    if not quote:
        return None
    kind = str(finding.get("kind") or "note")
    if kind not in allowed:
        kind = "note" if "note" in allowed else allowed[0]
    replacement = finding.get("replacement")
    replacement = str(replacement).strip() if replacement else ""
    if kind == "suggest" and not replacement:
        kind = "question" if "question" in allowed else "note"
    if kind not in ("suggest", "check"):
        replacement = ""
    if replacement and replacement == quote:
        replacement = ""  # no-op correction: keep it as a query, not an accept button
    rule_number = finding.get("rule")
    rule = rules_by_number.get(int(rule_number)) if isinstance(rule_number, int) else None
    if rule is None and not general_ok:
        return None
    comment = " ".join(str(finding.get("comment") or "").split())
    if not comment and rule is not None:
        comment = f"Rule {rule['number']}: {rule['text']}"

    fp = repo.fingerprint(kind, quote)
    if fp in known:
        return None

    hint = finding.get("paragraph")
    hint = int(hint) if isinstance(hint, int) and 1 <= hint <= len(paras) else None
    anchors = None
    if hint is not None:
        anchors = locate_quote(paras[hint - 1], quote)
    if anchors is None:
        hits = [(p, locate_quote(p, quote)) for p in paras]
        hits = [(p, a) for p, a in hits if a is not None]
        if len(hits) == 1:
            hint, anchors = hits[0][0]["index"], hits[0][1]
    if anchors is None:
        base = paras[hint - 1]["from"] if hint is not None else 0
        anchors = (base, base)

    confidence = str(finding.get("confidence") or "")
    if confidence not in ("high", "medium", "low"):
        confidence = ""
    ann = repo.create_ai_annotation(
        conn, doc_id, run_id=run_id, kind=kind, quote=quote, body=comment, suggested_text=replacement,
        paragraph_hint=hint, anchor_from=anchors[0], anchor_to=anchors[1],
        rule_id=rule["id"] if rule is not None else None, rule_number=rule["number"] if rule is not None else None,
        confidence=confidence,
    )
    known[fp] = "pending"
    return ann


def execute_run(run_id: str, conn: sqlite3.Connection | None = None) -> None:
    """Worker entry point. Opens its own connection unless one is injected (tests)."""
    own = conn is None
    conn = conn or connect()
    try:
        run = _wait_for_run(conn, run_id)
        if run is None:
            return
        try:
            _execute(conn, run)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user via the run row
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            traceback.print_exc()
            repo.update_run(conn, run_id, status="failed", error=str(exc)[:2000], finished_at=repo.iso(repo.utcnow()))
    finally:
        _unregister(run_id)
        if own:
            conn.close()


def _execute(conn: sqlite3.Connection, run: sqlite3.Row) -> None:
    run_id = run["id"]
    doc_id = run["document_id"]
    doc = repo.get_document(conn, doc_id)
    pass_row = repo.get_pass(conn, run["pass_id"]) if run["pass_id"] else None
    if doc is None or pass_row is None:
        raise RunError("Document or pass disappeared")

    mod = providers.get(run["provider"] if "provider" in run.keys() and run["provider"] else "codex")
    # The editor can change while a worker starts. Review exactly what was saved
    # when the pass was requested, including when the revision checkbox was off.
    content = json.loads(run["content_json"] or doc["content_json"])
    doc_title = run["document_title"] if run["content_json"] else doc["title"]
    paras = paragraphs(content)
    wins = windows(paras, MAX_WINDOW_WORDS)
    rules = select_rules(conn, pass_row)
    rules_by_number = {r["number"]: r for r in rules}
    allowed = [k.strip() for k in pass_row["allowed_kinds"].split(",") if k.strip() in repo.KINDS] or ["note"]
    known = repo.known_fingerprints(conn, doc_id)
    dismissed_here = repo.dismissed_findings(conn, doc_id)
    dismissed_elsewhere = []
    if repo.get_setting(conn, "include_other_documents", "0") == "1":
        dismissed_elsewhere = [r for r in repo.dismissed_findings(conn, None, 40) if r["document_id"] != doc_id][:25]

    repo.update_run(conn, run_id, status="running", started_at=repo.iso(repo.utcnow()), windows_total=len(wins))
    if not wins:
        repo.update_run(conn, run_id, status="done", summary="The document is empty.",
                        finished_at=repo.iso(repo.utcnow()))
        return

    summaries: list[str] = []
    total = suppressed = 0
    fired: dict[str, int] = {}
    cancelled = False
    for i, win in enumerate(wins, start=1):
        if _is_cancelled(conn, run_id):
            cancelled = True
            break
        prompt = build_prompt(doc_title=doc_title, pass_row=pass_row, rules=rules, window=win, window_index=i,
                              window_total=len(wins), dismissed_here=dismissed_here,
                              dismissed_elsewhere=dismissed_elsewhere)
        try:
            data = mod.run_exec(prompt, FINDINGS_SCHEMA, model=run["model"], effort=run["effort"],
                                on_start=lambda p: _register(run_id, p))
        except ProviderError:
            if _is_cancelled(conn, run_id):
                cancelled = True
                break
            raise
        finally:
            _unregister(run_id)
        if _is_cancelled(conn, run_id):
            cancelled = True
            break

        summary = " ".join(str(data.get("summary") or "").split())
        if summary:
            summaries.append(summary if len(wins) == 1 else f"Part {i}: {summary}")
        conn.execute("BEGIN")
        for finding in data.get("findings") or []:
            if not isinstance(finding, dict):
                continue
            ann = materialise_finding(conn, doc_id, run_id, finding, paras=paras, rules_by_number=rules_by_number,
                                      allowed=allowed, general_ok=bool(pass_row["general_findings"]), known=known)
            if ann is None:
                suppressed += 1
                continue
            total += 1
            if ann["rule_id"]:
                fired[ann["rule_id"]] = fired.get(ann["rule_id"], 0) + 1
        repo.update_run(conn, run_id, windows_done=i, findings_count=total, suppressed_count=suppressed)
        conn.execute("COMMIT")

    conn.execute("BEGIN")
    repo.bump_rule_fired(conn, fired)
    repo.update_run(conn, run_id, status="cancelled" if cancelled else "done", summary="\n\n".join(summaries),
                    findings_count=total, suppressed_count=suppressed, finished_at=repo.iso(repo.utcnow()))
    conn.execute("COMMIT")
