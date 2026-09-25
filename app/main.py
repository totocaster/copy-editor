"""HTTP layer: pages, HTMX partials, and the JSON endpoints the editor uses."""

from __future__ import annotations

import json
import re
import sqlite3
from urllib.parse import quote
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import codex, providers, repo, runs
from .content import diff_html, diff_stats, to_html, to_markdown, to_plaintext
from .db import connect, get_conn, get_write_conn, init_db
from .security import CSRF_TOKEN, LocalRequestGuard

BASE = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_: FastAPI):
    conn = connect()
    try:
        init_db(conn)
        repo.seed_passes(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="Copy Editor", lifespan=lifespan)
app.add_middleware(LocalRequestGuard)
app.mount("/static", StaticFiles(directory=BASE / "static", check_dir=False), name="static")
templates = Jinja2Templates(directory=BASE / "templates")
templates.env.globals["csrf_token"] = CSRF_TOKEN

Conn = Annotated[sqlite3.Connection, Depends(get_conn)]
WriteConn = Annotated[sqlite3.Connection, Depends(get_write_conn)]


@app.exception_handler(repo.DocumentConflict)
async def document_conflict(_: Request, exc: repo.DocumentConflict):
    return JSONResponse({"detail": str(exc), "version": exc.version}, status_code=409)


# ----------------------------------------------------------------------------
# Template helpers
# ----------------------------------------------------------------------------

def ago(value: str | None) -> str:
    if not value:
        return ""
    dt = repo.parse_iso(value)
    delta = datetime.now(timezone.utc) - dt
    secs = int(delta.total_seconds())
    if secs < 45:
        return "just now"
    if secs < 3600:
        return f"{max(1, secs // 60)} min ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h} hr ago" if h == 1 else f"{h} hrs ago"
    if secs < 7 * 86400:
        d = secs // 86400
        return "yesterday" if d == 1 else f"{d} days ago"
    return dt.astimezone().strftime("%b %-d, %Y")


def fmt_dt(value: str | None) -> str:
    if not value:
        return ""
    return repo.parse_iso(value).astimezone().strftime("%b %-d, %Y · %-I:%M %p")


def excerpt(value: str, length: int = 90) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= length else value[: length - 1].rstrip() + "…"


def asset(path: str) -> str:
    """Static URL with the file's mtime as a version, so rebuilt CSS/JS is never served stale from cache."""
    try:
        version = int((BASE / "static" / path).stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{path}?v={version}"


templates.env.globals["asset"] = asset
templates.env.filters["ago"] = ago
templates.env.filters["fmt_dt"] = fmt_dt
templates.env.filters["excerpt"] = excerpt
templates.env.globals["KINDS"] = repo.KINDS
templates.env.globals["DISMISS_REASONS"] = repo.DISMISS_REASONS
templates.env.globals["EFFORTS"] = repo.EFFORTS
templates.env.globals["ALL_EFFORTS"] = providers.ALL_EFFORTS
templates.env.globals["provider_label"] = providers.short_label


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def render(request: Request, name: str, ctx: dict[str, Any], *, headers: dict[str, str] | None = None,
           status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx, status_code=status_code, headers=headers)


def hx_trigger(event: str, payload: dict[str, Any]) -> dict[str, str]:
    return {"HX-Trigger": json.dumps({event: payload})}


def require_document(conn: sqlite3.Connection, doc_id: str) -> sqlite3.Row:
    doc = repo.get_document(conn, doc_id)
    if doc is None:
        raise HTTPException(404, "Document not found")
    return doc


# ----------------------------------------------------------------------------
# Documents
# ----------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request, conn: Conn):
    return render(request, "index.html", {"documents": repo.list_documents(conn)})


@app.post("/documents")
def create_document(request: Request, conn: Conn, title: Annotated[str, Form()] = "Untitled"):
    doc = repo.create_document(conn, title=title.strip() or "Untitled")
    if is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": f"/d/{doc['id']}"})
    return RedirectResponse(f"/d/{doc['id']}", status_code=303)


@app.delete("/documents/{doc_id}")
def delete_document(conn: Conn, doc_id: str):
    require_document(conn, doc_id)
    repo.delete_document(conn, doc_id)
    return Response(status_code=200)


def sidebar_ctx(conn: sqlite3.Connection, doc: sqlite3.Row, editing: str | None = None,
                oob_bar: bool = True) -> dict[str, Any]:
    """Context for the sidebar partial. `oob_bar` makes the response also refresh the
    sticky toolbar (filters + stepper) out of band; the full page renders it directly."""
    annotations = repo.list_annotations(conn, doc["id"])
    runs_by_id = {r["id"]: r for r in repo.list_runs(conn, doc["id"], limit=200)}
    rules_by_number = {r["number"]: r for r in repo.list_rules(conn)}
    return {"doc": doc, "annotations": annotations, "editing": editing, "runs_by_id": runs_by_id,
            "rules_by_number": rules_by_number, "oob_bar": oob_bar}


def strip_ctx(conn: sqlite3.Connection, doc: sqlite3.Row, run: sqlite3.Row | None) -> dict[str, Any]:
    if run is None:
        return {"doc": doc, "run": None}
    return {"doc": doc, "run": run, "counts": repo.run_counts(conn, run["id"]),
            "pending": repo.pending_findings(conn, doc["id"], run["id"])}


@app.get("/d/{doc_id}", response_class=HTMLResponse)
def document_page(request: Request, conn: Conn, doc_id: str):
    doc = require_document(conn, doc_id)
    ctx = sidebar_ctx(conn, doc, oob_bar=False)
    ctx.update({
        "open_ids": [a["id"] for a in ctx["annotations"] if a["status"] in repo.OPEN_STATUSES],
        "content": json.loads(doc["content_json"]),
        "latest_revision": repo.latest_revision(conn, doc_id),
        "pending": repo.pending_findings(conn, doc_id),
    })
    ctx.update(strip_ctx(conn, doc, repo.latest_run(conn, doc_id)))
    return render(request, "document.html", ctx)


@app.put("/d/{doc_id}/title")
def update_title(conn: WriteConn, doc_id: str, base_version: Annotated[int, Form(ge=0)],
                 title: Annotated[str, Form()] = ""):
    require_document(conn, doc_id)
    version = repo.update_title(conn, doc_id, title, expected_version=base_version)
    return JSONResponse({"ok": True, "version": version})


class ContentIn(BaseModel):
    content: dict[str, Any]
    base_version: int = Field(ge=0, strict=True)
    title: str | None = None
    preserve_before: bool = False


@app.get("/d/{doc_id}/content")
def get_content(conn: Conn, doc_id: str):
    doc = require_document(conn, doc_id)
    return JSONResponse({"content": json.loads(doc["content_json"]), "title": doc["title"],
                         "version": doc["version"]}, headers={"Cache-Control": "no-store"})


@app.put("/d/{doc_id}/content")
def save_content(conn: WriteConn, doc_id: str, payload: ContentIn):
    require_document(conn, doc_id)
    if payload.content.get("type") != "doc":
        raise HTTPException(422, "content must be a ProseMirror doc")
    result = repo.save_content(conn, doc_id, payload.content, expected_version=payload.base_version,
                               title=payload.title, preserve_before=payload.preserve_before)
    rev = result["revision"]
    return JSONResponse({
        "ok": True,
        "version": result["version"],
        "changed": result["changed"],
        "word_count": result["word_count"],
        "saved_at": result["saved_at"],
        "revision": {"id": rev["id"], "seq": rev["seq"], "note": rev["note"]} if rev is not None else None,
        "annotation_changes": result["annotation_changes"],
    })


def filename_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:80] or "document"


@app.get("/d/{doc_id}/export.{fmt}")
def export_document(conn: Conn, doc_id: str, fmt: str, inline: int = 0, base_version: int | None = None):
    """The document as Markdown or plain text; a download unless ?inline=1."""
    doc = require_document(conn, doc_id)
    repo.check_version(doc, base_version)
    content = json.loads(doc["content_json"])
    if fmt == "md":
        text, media = to_markdown(content, title=doc["title"]), "text/markdown"
    elif fmt == "txt":
        text, media = to_plaintext(content, title=doc["title"]), "text/plain"
    else:
        raise HTTPException(404)
    headers = {}
    if not inline:
        name = f"{filename_slug(doc['title'])}.{fmt}"
        pretty = quote(f"{' '.join(doc['title'].split()) or 'document'}.{fmt}")
        headers["Content-Disposition"] = f'attachment; filename="{name}"; filename*=UTF-8\'\'{pretty}'
    return PlainTextResponse(text, media_type=f"{media}; charset=utf-8", headers=headers)


# ----------------------------------------------------------------------------
# Annotations
# ----------------------------------------------------------------------------

@app.get("/d/{doc_id}/annotations", response_class=HTMLResponse)
def annotation_sidebar(request: Request, conn: Conn, doc_id: str, editing: str | None = None):
    doc = require_document(conn, doc_id)
    return render(request, "partials/sidebar.html", sidebar_ctx(conn, doc, editing))


@app.get("/d/{doc_id}/annotations/pending")
def annotation_pending(conn: Conn, doc_id: str):
    require_document(conn, doc_id)
    return JSONResponse(repo.pending_findings(conn, doc_id))


class AnnotationIn(BaseModel):
    id: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    kind: str = "note"
    quote: str = ""
    anchor_from: int = 0
    anchor_to: int = 0


@app.post("/d/{doc_id}/annotations")
def create_annotation(conn: Conn, doc_id: str, payload: AnnotationIn):
    require_document(conn, doc_id)
    if repo.get_annotation(conn, doc_id, payload.id) is not None:
        raise HTTPException(409, "annotation exists")
    # The author writes notes; suggestions, cuts and questions come from editing passes.
    ann = repo.create_annotation(conn, doc_id, payload.id, "note", payload.quote,
                                 payload.anchor_from, payload.anchor_to)
    return JSONResponse({"id": ann["id"], "kind": ann["kind"], "status": ann["status"]}, status_code=201)


class BulkStatusIn(BaseModel):
    ids: list[str]
    status: str
    reason: str = ""


@app.post("/d/{doc_id}/annotations/bulk_status")
def annotation_bulk_status(conn: Conn, doc_id: str, payload: BulkStatusIn):
    require_document(conn, doc_id)
    if payload.status not in repo.ALL_STATUSES:
        raise HTTPException(422, "bad status")
    done = repo.bulk_status(conn, doc_id, payload.ids, payload.status, payload.reason)
    return JSONResponse({"done": done})


def card_ctx(conn: sqlite3.Connection, doc: sqlite3.Row, ann: sqlite3.Row, editing: str | None) -> dict[str, Any]:
    ctx = sidebar_ctx(conn, doc, editing)
    ctx["a"] = ann
    return ctx


@app.get("/d/{doc_id}/annotations/{ann_id}", response_class=HTMLResponse)
def annotation_card(request: Request, conn: Conn, doc_id: str, ann_id: str):
    doc = require_document(conn, doc_id)
    ann = repo.get_annotation(conn, doc_id, ann_id)
    if ann is None:
        raise HTTPException(404)
    return render(request, "partials/annotation_card.html", card_ctx(conn, doc, ann, None))


@app.get("/d/{doc_id}/annotations/{ann_id}/edit", response_class=HTMLResponse)
def annotation_edit_form(request: Request, conn: Conn, doc_id: str, ann_id: str):
    doc = require_document(conn, doc_id)
    ann = repo.get_annotation(conn, doc_id, ann_id)
    if ann is None:
        raise HTTPException(404)
    if ann["author"] == "ai":
        raise HTTPException(403, "Findings from a pass can be accepted, resolved or dismissed, not edited")
    return render(request, "partials/annotation_card.html", card_ctx(conn, doc, ann, ann_id))


@app.put("/d/{doc_id}/annotations/{ann_id}", response_class=HTMLResponse)
def update_annotation(request: Request, conn: Conn, doc_id: str, ann_id: str,
                      body: Annotated[str, Form()] = "", kind: Annotated[str, Form()] = "note",
                      suggested_text: Annotated[str, Form()] = ""):
    doc = require_document(conn, doc_id)
    existing = repo.get_annotation(conn, doc_id, ann_id)
    if existing is None:
        raise HTTPException(404)
    if existing["author"] == "ai":
        raise HTTPException(403, "Findings from a pass can be accepted, resolved or dismissed, not edited")
    ann = repo.update_annotation(conn, doc_id, ann_id, kind=existing["kind"], body=body,
                                 suggested_text=existing["suggested_text"])
    headers = hx_trigger("annotation:changed", {"id": ann_id, "status": ann["status"], "kind": ann["kind"]})
    return render(request, "partials/annotation_card.html", card_ctx(conn, doc, ann, None), headers=headers)


@app.post("/d/{doc_id}/annotations/{ann_id}/status", response_class=HTMLResponse)
def set_annotation_status(request: Request, conn: Conn, doc_id: str, ann_id: str, status: Annotated[str, Form()],
                          reason: Annotated[str, Form()] = ""):
    doc = require_document(conn, doc_id)
    try:
        ann = repo.set_annotation_status(conn, doc_id, ann_id, status, reason)
    except ValueError:
        raise HTTPException(422, "bad status")
    if ann is None:
        raise HTTPException(404)
    headers = hx_trigger("annotation:changed", {"id": ann_id, "status": ann["status"], "kind": ann["kind"]})
    return render(request, "partials/sidebar.html", sidebar_ctx(conn, doc), headers=headers)


@app.delete("/d/{doc_id}/annotations/{ann_id}", response_class=HTMLResponse)
def delete_annotation(request: Request, conn: Conn, doc_id: str, ann_id: str):
    doc = require_document(conn, doc_id)
    repo.delete_annotation(conn, doc_id, ann_id)
    headers = hx_trigger("annotation:deleted", {"id": ann_id})
    return render(request, "partials/sidebar.html", sidebar_ctx(conn, doc), headers=headers)


# ----------------------------------------------------------------------------
# Editing-pass runs
# ----------------------------------------------------------------------------

def model_choices(conn: sqlite3.Connection) -> dict[str, Any]:
    """Providers, their models and effort levels, plus the default choice ('provider:model')."""
    groups = providers.groups()
    choices = [(g, m) for g in groups for m in g["models"]]
    valid = {providers.choice(g["id"], m["id"]) for g, m in choices}
    cfg = codex.config_defaults()
    default_choice = repo.get_setting(conn, "default_choice", "") or providers.choice("codex", cfg["model"])
    if default_choice not in valid:
        default_choice = next((providers.choice(g["id"], m["id"]) for g, m in choices if g["status"]["signed_in"]),
                              next(iter(sorted(valid)), ""))
    default_effort = repo.get_setting(conn, "default_effort", "") or "medium"
    efforts = {providers.choice(g["id"], m["id"]): {"levels": m["efforts"], "default": m["default_effort"]}
               for g, m in choices}
    any_signed_in = any(g["status"]["signed_in"] for g in groups)
    return {"groups": groups, "default_choice": default_choice, "default_effort": default_effort, "efforts": efforts,
            "any_signed_in": any_signed_in, "config_model": cfg["model"], "config_effort": cfg["effort"]}


@app.get("/d/{doc_id}/runs/menu", response_class=HTMLResponse)
def run_menu(request: Request, conn: Conn, doc_id: str):
    doc = require_document(conn, doc_id)
    ctx = {"doc": doc, "passes": repo.list_passes(conn), "rule_count": len(repo.list_rules(conn, enabled_only=True)),
           "active": repo.active_run(conn, doc_id)}
    ctx.update(model_choices(conn))
    return render(request, "partials/pass_menu.html", ctx)


@app.post("/d/{doc_id}/runs", response_class=HTMLResponse)
def start_run(request: Request, conn: WriteConn, doc_id: str, background: BackgroundTasks,
              pass_id: Annotated[str, Form()], base_version: Annotated[int, Form(ge=0)],
              choice: Annotated[str, Form()] = "",
              effort: Annotated[str, Form()] = "medium", snapshot: Annotated[str | None, Form()] = None):
    doc = require_document(conn, doc_id)
    repo.check_version(doc, base_version)
    try:
        provider, model = providers.parse_choice(choice)
        levels, model_default = providers.efforts_for(provider, model)
        if effort not in levels:
            effort = model_default if model_default in levels else levels[0]
        run = runs.start_run(conn, doc_id, pass_id, provider=provider, model=model, effort=effort,
                             snapshot=bool(snapshot))
    except (runs.RunError, providers.ProviderError) as exc:
        return render(request, "partials/run_strip.html", {"doc": doc, "run": None, "error": str(exc)},
                      status_code=200)
    background.add_task(runs.launch, run["id"])
    headers = hx_trigger("run:started", {"id": run["id"]})
    return render(request, "partials/run_strip.html", strip_ctx(conn, doc, run), headers=headers)


@app.get("/d/{doc_id}/runs/{run_id}/strip", response_class=HTMLResponse)
def run_strip(request: Request, conn: Conn, doc_id: str, run_id: str):
    doc = require_document(conn, doc_id)
    run = repo.get_run(conn, run_id)
    if run is None or run["document_id"] != doc_id:
        raise HTTPException(404)
    return render(request, "partials/run_strip.html", strip_ctx(conn, doc, run))


@app.post("/d/{doc_id}/runs/{run_id}/cancel", response_class=HTMLResponse)
def cancel_run(request: Request, conn: Conn, doc_id: str, run_id: str):
    doc = require_document(conn, doc_id)
    runs.cancel_run(conn, run_id)
    return render(request, "partials/run_strip.html", strip_ctx(conn, doc, repo.get_run(conn, run_id)))


@app.post("/d/{doc_id}/runs/{run_id}/dismiss_all", response_class=HTMLResponse)
def dismiss_all(request: Request, conn: Conn, doc_id: str, run_id: str):
    doc = require_document(conn, doc_id)
    ids = repo.run_ai_annotation_ids(conn, run_id)
    repo.bulk_status(conn, doc_id, ids, "dismissed", "keep")
    headers = hx_trigger("annotations:removed", {"ids": ids})
    return render(request, "partials/sidebar.html", sidebar_ctx(conn, doc), headers=headers)


# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------

SETTINGS_TABS = ("account", "rules", "passes", "runs")


def account_ctx(conn: sqlite3.Connection, force: bool = False) -> dict[str, Any]:
    if force:
        for mod in providers.REGISTRY.values():
            mod.status(force=True)
    ctx = model_choices(conn)
    ctx["logins"] = {pid: mod.login_state() for pid, mod in providers.REGISTRY.items()}
    ctx["login_active"] = any(state["active"] for state in ctx["logins"].values())
    ctx["include_other_documents"] = repo.get_setting(conn, "include_other_documents", "0") == "1"
    return ctx


def rules_ctx(conn: sqlite3.Connection) -> dict[str, Any]:
    rules = repo.list_rules(conn)
    return {"rules": rules, "enabled_count": sum(1 for r in rules if r["enabled"])}


def passes_ctx(conn: sqlite3.Connection, selected: str | None = None) -> dict[str, Any]:
    passes = repo.list_passes(conn)
    current = repo.get_pass(conn, selected) if selected else (passes[0] if passes else None)
    tags = sorted({r["tag"] for r in repo.list_rules(conn) if r["tag"]})
    ctx = {"passes": passes, "p": current, "tags": tags}
    ctx.update(model_choices(conn))
    return ctx


def settings_tab_ctx(conn: sqlite3.Connection, tab: str, selected: str | None = None) -> dict[str, Any]:
    if tab == "rules":
        return rules_ctx(conn)
    if tab == "passes":
        return passes_ctx(conn, selected)
    if tab == "runs":
        return {"runs": repo.list_runs(conn, None, limit=100)}
    return account_ctx(conn)


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, conn: Conn, tab: str = "account", id: str | None = None):
    if tab not in SETTINGS_TABS:
        tab = "account"
    ctx = {"tab": tab}
    ctx.update(settings_tab_ctx(conn, tab, id))
    return render(request, "settings.html", ctx)


@app.get("/settings/{tab}", response_class=HTMLResponse)
def settings_tab(request: Request, conn: Conn, tab: str, id: str | None = None):
    if tab not in SETTINGS_TABS:
        raise HTTPException(404)
    ctx = {"tab": tab}
    ctx.update(settings_tab_ctx(conn, tab, id))
    headers = {"HX-Push-Url": f"/settings?tab={tab}"} if is_htmx(request) else None
    return render(request, f"partials/settings_{tab}.html", ctx, headers=headers)


def _provider_or_404(provider_id: str):
    if provider_id not in providers.REGISTRY:
        raise HTTPException(404)
    return providers.REGISTRY[provider_id]


@app.post("/settings/account/{provider_id}/login", response_class=HTMLResponse)
def account_login(request: Request, conn: Conn, provider_id: str):
    _provider_or_404(provider_id).start_login()
    return render(request, "partials/settings_account.html", {"tab": "account", **account_ctx(conn)})


@app.post("/settings/account/{provider_id}/login/cancel", response_class=HTMLResponse)
def account_login_cancel(request: Request, conn: Conn, provider_id: str):
    _provider_or_404(provider_id).cancel_login()
    return render(request, "partials/settings_account.html", {"tab": "account", **account_ctx(conn, force=True)})


@app.post("/settings/account/{provider_id}/logout", response_class=HTMLResponse)
def account_logout(request: Request, conn: Conn, provider_id: str):
    _provider_or_404(provider_id).logout()
    return render(request, "partials/settings_account.html", {"tab": "account", **account_ctx(conn, force=True)})


@app.get("/settings/account/status", response_class=HTMLResponse)
def account_status(request: Request, conn: Conn, force: int = 0):
    if force:
        codex.models(force=True)
    return render(request, "partials/settings_account.html", {"tab": "account", **account_ctx(conn, force=bool(force))})


@app.put("/settings/account/defaults", response_class=HTMLResponse)
def account_defaults(request: Request, conn: Conn, default_choice: Annotated[str, Form()] = "",
                     default_effort: Annotated[str, Form()] = "medium"):
    try:
        providers.parse_choice(default_choice)
    except providers.ProviderError:
        raise HTTPException(422, "bad model choice")
    repo.set_setting(conn, "default_choice", default_choice.strip())
    repo.set_setting(conn, "default_effort", default_effort if default_effort in providers.ALL_EFFORTS else "medium")
    return render(request, "partials/settings_account.html", {"tab": "account", **account_ctx(conn), "saved": True})


@app.put("/settings/account/privacy", response_class=HTMLResponse)
def account_privacy(request: Request, conn: Conn, include_other_documents: Annotated[str | None, Form()] = None):
    repo.set_setting(conn, "include_other_documents", "1" if include_other_documents == "1" else "0")
    return render(request, "partials/settings_account.html", {"tab": "account", **account_ctx(conn), "privacy_saved": True})


# Rules ----------------------------------------------------------------------

@app.post("/settings/rules", response_class=HTMLResponse)
def rule_create(request: Request, conn: Conn, text: Annotated[str, Form()] = "", tag: Annotated[str, Form()] = ""):
    repo.create_rule(conn, text, tag)
    return render(request, "partials/settings_rules.html", {"tab": "rules", **rules_ctx(conn), "focus_add": True})


@app.put("/settings/rules/{rule_id}", response_class=HTMLResponse)
def rule_update(request: Request, conn: Conn, rule_id: str, text: Annotated[str | None, Form()] = None,
                tag: Annotated[str | None, Form()] = None):
    rule = repo.update_rule(conn, rule_id, text=text, tag=tag)
    if rule is None:
        raise HTTPException(404)
    return render(request, "partials/rule_row.html", {"r": rule})


@app.post("/settings/rules/{rule_id}/toggle", response_class=HTMLResponse)
def rule_toggle(request: Request, conn: Conn, rule_id: str):
    rule = repo.get_rule(conn, rule_id)
    if rule is None:
        raise HTTPException(404)
    rule = repo.update_rule(conn, rule_id, enabled=not rule["enabled"])
    return render(request, "partials/rule_row.html", {"r": rule})


@app.post("/settings/rules/{rule_id}/move", response_class=HTMLResponse)
def rule_move(request: Request, conn: Conn, rule_id: str, direction: Annotated[int, Form()] = 1):
    repo.move_rule(conn, rule_id, direction)
    return render(request, "partials/settings_rules.html", {"tab": "rules", **rules_ctx(conn)})


@app.delete("/settings/rules/{rule_id}", response_class=HTMLResponse)
def rule_delete(request: Request, conn: Conn, rule_id: str):
    repo.delete_rule(conn, rule_id)
    return render(request, "partials/settings_rules.html", {"tab": "rules", **rules_ctx(conn)})


@app.post("/settings/rules/import", response_class=HTMLResponse)
def rules_import(request: Request, conn: Conn, text: Annotated[str, Form()] = ""):
    added = repo.import_rules(conn, text)
    return render(request, "partials/settings_rules.html", {"tab": "rules", **rules_ctx(conn), "imported": added})


@app.get("/settings/rules/export")
def rules_export(conn: Conn):
    return PlainTextResponse(repo.export_rules(conn),
                             headers={"Content-Disposition": 'attachment; filename="rules.txt"'})


# Passes ---------------------------------------------------------------------

@app.post("/settings/passes", response_class=HTMLResponse)
def pass_create(request: Request, conn: Conn):
    p = repo.create_pass(conn)
    return render(request, "partials/settings_passes.html", {"tab": "passes", **passes_ctx(conn, p["id"])},
                  headers={"HX-Push-Url": f"/settings?tab=passes&id={p['id']}"})


@app.get("/settings/passes/{pass_id}", response_class=HTMLResponse)
def pass_form(request: Request, conn: Conn, pass_id: str):
    if repo.get_pass(conn, pass_id) is None:
        raise HTTPException(404)
    return render(request, "partials/settings_passes.html", {"tab": "passes", **passes_ctx(conn, pass_id)},
                  headers={"HX-Push-Url": f"/settings?tab=passes&id={pass_id}"})


@app.put("/settings/passes/{pass_id}", response_class=HTMLResponse)
def pass_update(request: Request, conn: Conn, pass_id: str, name: Annotated[str, Form()] = "",
                description: Annotated[str, Form()] = "", rule_mode: Annotated[str, Form()] = "all",
                rule_tag: Annotated[str, Form()] = "", instructions: Annotated[str, Form()] = "",
                allowed_kinds: Annotated[list[str], Form()] = [], general_findings: Annotated[str | None, Form()] = None,
                pin: Annotated[str, Form()] = "", effort: Annotated[str, Form()] = "",
                is_primary: Annotated[str | None, Form()] = None):
    if repo.get_pass(conn, pass_id) is None:
        raise HTTPException(404)
    kinds = ",".join(k for k in allowed_kinds if k in repo.KINDS) or "note"
    provider, model = ("", "")
    if pin.strip():
        try:
            provider, model = providers.parse_choice(pin)
        except providers.ProviderError:
            raise HTTPException(422, "bad model choice")
    repo.update_pass(conn, pass_id, name=name.strip() or "Untitled pass", description=description.strip(),
                     rule_mode=rule_mode if rule_mode in ("all", "tag", "none") else "all", rule_tag=rule_tag.strip(),
                     instructions=instructions.strip(), allowed_kinds=kinds, general_findings=int(bool(general_findings)),
                     provider=provider, model=model, effort=effort if effort in providers.ALL_EFFORTS else "",
                     is_primary=int(bool(is_primary)))
    return render(request, "partials/settings_passes.html", {"tab": "passes", **passes_ctx(conn, pass_id), "saved": True})


@app.post("/settings/passes/{pass_id}/duplicate", response_class=HTMLResponse)
def pass_duplicate(request: Request, conn: Conn, pass_id: str):
    p = repo.duplicate_pass(conn, pass_id)
    if p is None:
        raise HTTPException(404)
    return render(request, "partials/settings_passes.html", {"tab": "passes", **passes_ctx(conn, p["id"])})


@app.delete("/settings/passes/{pass_id}", response_class=HTMLResponse)
def pass_delete(request: Request, conn: Conn, pass_id: str):
    repo.delete_pass(conn, pass_id)
    return render(request, "partials/settings_passes.html", {"tab": "passes", **passes_ctx(conn)})


# ----------------------------------------------------------------------------
# Revisions
# ----------------------------------------------------------------------------

def revisions_ctx(conn: sqlite3.Connection, doc: sqlite3.Row) -> dict[str, Any]:
    revs = repo.list_revisions(conn, doc["id"])
    latest = repo.latest_revision(conn, doc["id"])
    dirty = latest is None or latest["content_json"] != doc["content_json"]
    return {"doc": doc, "revisions": revs, "dirty": dirty}


@app.get("/d/{doc_id}/revisions", response_class=HTMLResponse)
def revisions_panel(request: Request, conn: Conn, doc_id: str):
    doc = require_document(conn, doc_id)
    ctx = revisions_ctx(conn, doc)
    return render(request, "partials/revisions_panel.html" if is_htmx(request) else "revisions.html", ctx)


@app.post("/d/{doc_id}/revisions", response_class=HTMLResponse)
def create_snapshot(request: Request, conn: WriteConn, doc_id: str, base_version: Annotated[int, Form(ge=0)],
                    note: Annotated[str, Form()] = "",
                    is_major: Annotated[str | None, Form()] = None):
    doc = require_document(conn, doc_id)
    repo.check_version(doc, base_version)
    repo.snapshot(conn, doc_id, note=note, is_major=bool(is_major))
    return render(request, "partials/revisions_panel.html", revisions_ctx(conn, doc))


@app.post("/d/{doc_id}/revisions/{rev_id}/major", response_class=HTMLResponse)
def toggle_major(request: Request, conn: Conn, doc_id: str, rev_id: str):
    doc = require_document(conn, doc_id)
    rev = repo.get_revision(conn, doc_id, rev_id)
    if rev is None:
        raise HTTPException(404)
    rev = repo.set_major(conn, doc_id, rev_id, not rev["is_major"])
    return render(request, "partials/revision_row.html", {"doc": doc, "r": rev})


@app.put("/d/{doc_id}/revisions/{rev_id}/note", response_class=HTMLResponse)
def update_note(request: Request, conn: Conn, doc_id: str, rev_id: str, note: Annotated[str, Form()] = ""):
    doc = require_document(conn, doc_id)
    rev = repo.set_note(conn, doc_id, rev_id, note)
    if rev is None:
        raise HTTPException(404)
    return render(request, "partials/revision_row.html", {"doc": doc, "r": rev})


@app.post("/d/{doc_id}/revisions/{rev_id}/restore")
def restore(request: Request, conn: WriteConn, doc_id: str, rev_id: str,
            base_version: Annotated[int, Form(ge=0)]):
    require_document(conn, doc_id)
    try:
        repo.restore_revision(conn, doc_id, rev_id, expected_version=base_version)
    except KeyError:
        raise HTTPException(404)
    if is_htmx(request):
        return Response(status_code=204, headers={"HX-Redirect": f"/d/{doc_id}"})
    return RedirectResponse(f"/d/{doc_id}", status_code=303)


@app.get("/d/{doc_id}/r/{rev_id}", response_class=HTMLResponse)
def revision_page(request: Request, conn: Conn, doc_id: str, rev_id: str, against: str = "prev"):
    doc = require_document(conn, doc_id)
    rev = repo.get_revision(conn, doc_id, rev_id)
    if rev is None:
        raise HTTPException(404)
    revs = repo.list_revisions(conn, doc_id)
    prev = repo.get_revision_by_seq(conn, doc_id, rev["seq"] - 1)

    other_label = None
    other_text = None
    if against == "current":
        other_label, other_text = "current document", doc["content_text"]
    elif against == "prev" and prev is not None:
        other_label, other_text = f"revision {prev['seq']}", prev["content_text"]
    elif against not in ("prev", "current", "none"):
        other = repo.get_revision(conn, doc_id, against)
        if other is not None:
            other_label, other_text = f"revision {other['seq']}", other["content_text"]

    diff = stats = None
    if other_text is not None:
        diff = diff_html(other_text, rev["content_text"])
        stats = diff_stats(other_text, rev["content_text"])

    return render(request, "revision.html", {
        "doc": doc, "rev": rev, "revisions": revs, "prev": prev,
        "html": to_html(json.loads(rev["content_json"])),
        "against": against, "other_label": other_label, "diff": diff, "stats": stats,
    })
