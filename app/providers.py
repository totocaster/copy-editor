"""Model providers. Each is a module with the same surface: status(), models(),
run_exec(), start_login(), login_state(), cancel_login(), logout()."""

from __future__ import annotations

from typing import Any

from . import claude_cli, codex
from .errors import ProviderError  # noqa: F401  (re-exported for callers)

REGISTRY = {codex.ID: codex, claude_cli.ID: claude_cli}
DEFAULT = codex.ID
ALL_EFFORTS = ["low", "medium", "high", "xhigh", "max", "ultra"]


def get(provider_id: str | None):
    mod = REGISTRY.get(provider_id or DEFAULT)
    if mod is None:
        raise ProviderError(f"Unknown provider: {provider_id}")
    return mod


def label(provider_id: str | None) -> str:
    mod = REGISTRY.get(provider_id or DEFAULT)
    return mod.LABEL if mod else str(provider_id)


def short_label(provider_id: str | None) -> str:
    return {"codex": "Codex", "claude": "Claude Code"}.get(provider_id or DEFAULT, str(provider_id))


def groups() -> list[dict[str, Any]]:
    """Providers with their sign-in state and current models, for pickers."""
    out = []
    for pid, mod in REGISTRY.items():
        st = mod.status()
        out.append({"id": pid, "label": mod.LABEL, "status": st, "models": mod.models() if st["installed"] else []})
    return out


def parse_choice(value: str) -> tuple[str, str]:
    """'provider:model' -> (provider, model); a bare model id means Codex."""
    value = (value or "").strip()
    if ":" in value:
        pid, model = value.split(":", 1)
    else:
        pid, model = DEFAULT, value
    if pid not in REGISTRY:
        raise ProviderError(f"Unknown provider: {pid}")
    return pid, model.strip()


def choice(provider_id: str, model: str) -> str:
    return f"{provider_id}:{model}" if model else ""


def efforts_for(provider_id: str, model: str) -> tuple[list[str], str]:
    for m in get(provider_id).models():
        if m["id"] == model:
            return list(m["efforts"]), m["default_effort"]
    return ["low", "medium", "high"], "medium"
