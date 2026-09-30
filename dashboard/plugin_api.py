# ruff: noqa: BLE001, PLW1510, S110
"""hermes-wallpaper plugin backend — Wallpaper Engine library + control.

Mounted at ``/api/plugins/hermes-wallpaper/`` by the Hermes dashboard plugin
system (``dashboard/manifest.json`` declares ``api=plugin_api.py``; the plugin
must be listed in ``plugins.enabled`` in config.yaml before the serve process
imports it — GHSA-mcfc-hp25-cjv7).

Design:
* Discovery and control live in ``wallpaper_core.py`` beside this package — one
  source of truth shared with the agent half's tools. This file is only the
  HTTP surface.
* **Read-only over user content.** Nothing here writes into Steam, WE's config,
  or workshop directories. The only writes are the plugin's own state file and
  invoking WE's documented ``-control`` command line.
* Images are handed to the renderer as ``data:`` URLs. The renderer cannot fetch
  a bare local path (no ``file:`` in the plugin surface) and ``ctx.rest`` is a
  JSON door, so base64 is the sanctioned hand-off — the same shape the bundled
  ``hermes-newswire`` plugin uses for favicons.
* Video is *not* base64'd: the renderer loads ``hermes-media://stream/<path>``,
  the app's own Range-capable local-media protocol, so seeking works and no copy
  is made. We only hand back the path.
* Paths returned to the renderer are the wallpaper's own files, discovered from
  the Steam/WE layout. The renderer never supplies a path — it supplies an
  ``entry_id`` that is looked up here. That is the boundary.
"""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# ``dashboard/plugin_api.py`` is loaded standalone by the web server
# (``importlib.util.spec_from_file_location`` with no package), so a bare
# ``import wallpaper_core`` would not resolve — the plugin directory is not on
# ``sys.path``. Load the shared core by path instead. This is a *separate*
# module instance from the agent half's, which is correct: they are different
# processes (or at least different registries) and share state only through the
# config file, never through module globals.
import importlib.util as _ilu

_CORE_PATH = Path(__file__).resolve().parent.parent / "wallpaper_core.py"
_spec = _ilu.spec_from_file_location("hermes_wallpaper_core_http", _CORE_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - static layout
    raise ImportError(f"cannot load wallpaper_core from {_CORE_PATH}")
core = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(core)

router = APIRouter()


# ── models ─────────────────────────────────────────────────────────────────


class ConfigPatch(BaseModel):
    """Partial config write — every field optional, unknown ids rejected."""

    selected_id: str | None = None
    enabled: bool | None = None
    surface_opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    scrim_strength: float | None = Field(default=None, ge=0.0, le=1.0)
    blur_px: int | None = Field(default=None, ge=0, le=60)
    outline_px: float | None = Field(default=None, ge=0.0, le=2.0)
    outline_alpha: float | None = Field(default=None, ge=0.0, le=1.0)
    quality: str | None = None  # balanced | powersaver | low
    monitor: int | None = Field(default=None, ge=0, le=7)


class ApplyRequest(BaseModel):
    entry_id: str
    monitor: int = Field(default=0, ge=0, le=7)


class ControlRequest(BaseModel):
    action: str
    monitor: int | None = Field(default=None, ge=0, le=7)


_DEFAULTS: dict[str, Any] = {
    "selected_id": None,
    "enabled": True,
    "surface_opacity": 0.72,
    "scrim_strength": 0.45,
    "blur_px": 0,
    "outline_px": 0.5,
    "outline_alpha": 0.85,
    "quality": "balanced",
    "monitor": 0,
}

_QUALITY = {"balanced", "powersaver", "low"}


# ── helpers ────────────────────────────────────────────────────────────────


def _config() -> dict[str, Any]:
    merged = dict(_DEFAULTS)
    merged.update(core.load_config())
    return merged


def _entry_or_404(entry_id: str) -> dict[str, Any]:
    entry = core.find_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"unknown wallpaper id {entry_id!r}")
    return entry


def _data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=404, detail=f"cannot read media: {exc}") from exc
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _resolve_child(entry: dict[str, Any], key: str) -> Path:
    raw = entry.get(key)
    if not raw:
        raise HTTPException(status_code=404, detail=f"wallpaper has no {key}")
    path = Path(str(raw))
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"{key} missing on disk: {path.name}")
    # Defence in depth: the file must live inside the wallpaper's own directory.
    try:
        path.resolve().relative_to(Path(str(entry["dir"])).resolve())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="media outside wallpaper directory") from exc
    return path


# ── routes ─────────────────────────────────────────────────────────────────


@router.get("/health")
def health() -> dict[str, Any]:
    return core.health()


@router.get("/library")
def library() -> dict[str, Any]:
    entries = core.scan_library()
    cfg = _config()
    return {
        "entries": entries,
        "count": len(entries),
        "selected_id": cfg.get("selected_id"),
        "config": cfg,
    }


@router.get("/config")
def get_config() -> dict[str, Any]:
    return _config()


@router.post("/config")
def patch_config(patch: ConfigPatch) -> dict[str, Any]:
    cfg = _config()
    updates = patch.model_dump(exclude_none=True)

    if "quality" in updates and updates["quality"] not in _QUALITY:
        raise HTTPException(status_code=422, detail=f"quality must be one of {sorted(_QUALITY)}")
    if "selected_id" in updates and updates["selected_id"] is not None:
        if core.find_entry(updates["selected_id"]) is None:
            raise HTTPException(status_code=404, detail=f"unknown wallpaper id {updates['selected_id']!r}")

    cfg.update(updates)
    return core.save_config(cfg)


@router.get("/image/{entry_id}")
def image(entry_id: str, which: str = "preview") -> dict[str, Any]:
    """Base64 data URL for a wallpaper's preview or its image media."""
    entry = _entry_or_404(entry_id)
    key = "media_file" if which == "media" else "preview_file"
    path = _resolve_child(entry, key)
    return {"entry_id": entry_id, "which": which, "data_url": _data_url(path)}


@router.get("/stream-url/{entry_id}")
def stream_url(entry_id: str) -> dict[str, Any]:
    """The renderer-facing URL for a wallpaper's video/media.

    Returns the app's own ``hermes-media://stream/`` URL — a Range-capable
    local-media protocol — so the <video> element can seek without the bytes
    ever passing through JSON. Only the path is returned; the renderer cannot
    name a file, only an entry id.
    """
    entry = _entry_or_404(entry_id)
    path = _resolve_child(entry, "media_file")
    from urllib.parse import quote

    url = f"hermes-media://stream/{quote(str(path), safe='')}"
    return {
        "entry_id": entry_id,
        "url": url,
        "mime": mimetypes.guess_type(path.name)[0] or "video/mp4",
        "size": path.stat().st_size,
    }


@router.post("/apply")
def apply_wallpaper(req: ApplyRequest) -> dict[str, Any]:
    entry = _entry_or_404(req.entry_id)
    result = core.apply_wallpaper(entry, monitor=req.monitor)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "Wallpaper Engine rejected the command")
    cfg = _config()
    cfg["selected_id"] = req.entry_id
    core.save_config(cfg)
    result["selected_id"] = req.entry_id
    return result


@router.post("/we/control")
def we_control(req: ControlRequest) -> dict[str, Any]:
    result = core.we_control(req.action, monitor=req.monitor)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error") or "Wallpaper Engine rejected the command")
    return result
