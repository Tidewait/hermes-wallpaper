"""Wallpaper Engine discovery + control core for the ``hermes-wallpaper`` plugin.

Pure stdlib. Shared by the agent half (``__init__.py`` tools) and the dashboard
backend (``dashboard/plugin_api.py``) — one source of truth for what a
"wallpaper" is, where the libraries live, and how to talk to Wallpaper Engine.

Read-mostly by design: scanning never touches WE state. The only write is
``we_control``/``apply``, which invoke Wallpaper Engine's own documented
``-control`` command line (see
https://docs.wallpaperengine.io — "Control Wallpaper Engine from Command Prompt").
We never edit WE's own config, never modify workshop content.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

WORKSHOP_APP_ID = "431960"

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}

# Wallpaper Engine project types → how we render them in A-tier.
# scene / web / application degrade to their preview image (we-scene rendering
# is the B-tier extension point; see SKILL.md).
_RENDER_MODE = {
    "video": "video",
    "image": "image",
    "scene": "preview",
    "web": "preview",
    "application": "preview",
}


# ── hermes home / state ────────────────────────────────────────────────────


def get_hermes_home() -> Path:
    try:
        from hermes_cli import hermes_constants  # type: ignore

        return Path(hermes_constants.get_hermes_home())
    except Exception:  # noqa: BLE001 - plugin must never crash on a lookup
        return Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))


def state_dir() -> Path:
    return get_hermes_home() / "state" / "hermes-wallpaper"


def config_path() -> Path:
    return state_dir() / "config.json"


def load_config() -> dict[str, Any]:
    try:
        raw = config_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    state_dir().mkdir(parents=True, exist_ok=True)
    tmp = config_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(config_path())
    return cfg


# ── discovery ──────────────────────────────────────────────────────────────


def _reg_we_install_path() -> str:
    """``HKCU\\Software\\WallpaperEngine\\installPath`` → wallpaper64.exe path."""
    if os.name != "nt":
        return ""
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\WallpaperEngine") as key:
            value, _ = winreg.QueryValueEx(key, "installPath")
            return str(value or "")
    except Exception:  # noqa: BLE001 - missing key / non-Windows / no winreg
        return ""


def find_we_dir() -> Path | None:
    """Directory holding ``wallpaper64.exe`` (registry first, then common paths)."""
    candidates: list[Path] = []

    reg = _reg_we_install_path()
    if reg:
        candidates.append(Path(reg))

    for base in (Path("C:/Program Files (x86)"), Path("C:/Program Files"), Path("D:/"), Path("E:/")):
        candidates.extend(
            [
                base / "Steam" / "steamapps" / "common" / "wallpaper_engine",
                base / "Steam" / "steamapps" / "common" / "Wallpaper Engine",
                base / "steamapps" / "common" / "wallpaper_engine",
                base / "Wallpaper Engine",
            ]
        )

    seen: set[str] = set()
    for cand in candidates:
        key = str(cand).lower()
        if key in seen:
            continue
        seen.add(key)
        for exe in ("wallpaper64.exe", "wallpaper32.exe"):
            p = cand / exe if cand.suffix.lower() != ".exe" else cand
            if p.is_file():
                return p.parent if p.name.lower().endswith(".exe") else cand
        if cand.is_dir() and (cand / "wallpaper64.exe").is_file():
            return cand
    return None


def we_exe() -> Path | None:
    we_dir = find_we_dir()
    if not we_dir:
        return None
    for name in ("wallpaper64.exe", "wallpaper32.exe"):
        p = we_dir / name
        if p.is_file():
            return p
    return None


def _vdf_paths(vdf_file: Path) -> list[str]:
    """Pull every ``"path"`` value out of a Steam ``libraryfolders.vdf``."""
    try:
        text = vdf_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    # Steam escapes backslashes as \\ inside the VDF strings.
    out = re.findall(r'"path"\s*"([^"]+)"', text)
    return [p.replace("\\\\", "\\") for p in out]


def steam_library_roots() -> list[Path]:
    roots: list[Path] = []
    for base in (Path("C:/Program Files (x86)"), Path("C:/Program Files"), Path("D:/"), Path("E:/")):
        for steam in (base / "Steam", base / "steam"):
            vdf = steam / "steamapps" / "libraryfolders.vdf"
            if vdf.is_file():
                for raw in _vdf_paths(vdf):
                    p = Path(raw)
                    if p not in roots:
                        roots.append(p)
                if steam not in roots:
                    roots.append(steam)
    return roots


def workshop_roots() -> list[Path]:
    """Every ``…/steamapps/workshop/content/431960`` we can find."""
    out: list[Path] = []
    for lib in steam_library_roots():
        p = lib / "steamapps" / "workshop" / "content" / WORKSHOP_APP_ID
        if p.is_dir() and p not in out:
            out.append(p)
    return out


def local_project_roots() -> list[Path]:
    """WE's own wallpaper containers (user-authored + bundled samples).

    ``projects/`` itself is a *container* of containers (``defaultprojects``,
    ``myprojects``, ``templates``) — never a wallpaper — so we descend one more
    level. ``templates`` is skipped: those are editor scaffolds, not wallpapers.
    """
    we_dir = find_we_dir()
    if not we_dir:
        return []
    out: list[Path] = []
    for sub in ("projects/myprojects", "projects/defaultprojects"):
        p = we_dir / sub
        if p.is_dir():
            out.append(p)
    return out


def _read_project_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _first_by_ext(directory: Path, exts: set[str]) -> Path | None:
    try:
        entries = sorted(directory.iterdir())
    except OSError:
        return None
    for entry in entries:
        if entry.is_file() and entry.suffix.lower() in exts:
            return entry
    return None


def _build_entry(project_dir: Path, project_file: Path, source: str) -> dict[str, Any] | None:
    # A wallpaper is, by WE's own definition, a directory with a project.json.
    # Directories without one are partial downloads or debris — skip them
    # rather than surface a title-less dead entry.
    if not project_file.is_file():
        return None

    meta = _read_project_json(project_file)
    raw_type = str(meta.get("type") or "").strip().lower()
    title = str(meta.get("title") or project_dir.name).strip() or project_dir.name

    preview_rel = str(meta.get("preview") or "").strip()
    preview = project_dir / preview_rel if preview_rel else None
    if not preview or not preview.is_file():
        preview = _first_by_ext(project_dir, IMAGE_EXTS)

    media: Path | None = None
    if raw_type == "video" or raw_type == "image":
        file_rel = str(meta.get("file") or "").strip()
        if file_rel:
            cand = project_dir / file_rel
            if cand.is_file():
                media = cand
        if media is None:
            exts = VIDEO_EXTS if raw_type == "video" else IMAGE_EXTS
            media = _first_by_ext(project_dir, exts)

    # A wallpaper with no recognised type but a video file in it is a video.
    if not raw_type:
        if _first_by_ext(project_dir, VIDEO_EXTS):
            raw_type = "video"
            media = media or _first_by_ext(project_dir, VIDEO_EXTS)
        elif _first_by_ext(project_dir, IMAGE_EXTS):
            raw_type = "image"
            media = media or _first_by_ext(project_dir, IMAGE_EXTS)
        else:
            raw_type = "other"

    kind = raw_type if raw_type in _RENDER_MODE else "other"
    render_mode = _RENDER_MODE.get(kind, "preview")

    entry: dict[str, Any] = {
        "id": project_dir.name,
        "title": title,
        "type": kind,
        "type_raw": raw_type,
        "source": source,
        "render_mode": render_mode,
        "dir": str(project_dir),
        "project_file": str(project_file),
        "workshopid": meta.get("workshopid"),
        "tags": list(meta.get("tags") or [])[:8],
    }
    if media is not None:
        entry["media_file"] = str(media)
    if preview is not None:
        entry["preview_file"] = str(preview)
    return entry


def scan_library() -> list[dict[str, Any]]:
    """All discoverable wallpapers, workshop first then local projects."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for root in workshop_roots():
        try:
            dirs = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for d in dirs:
            if str(d).lower() in seen:
                continue
            seen.add(str(d).lower())
            entry = _build_entry(d, d / "project.json", "workshop")
            if entry:
                out.append(entry)

    for root in local_project_roots():
        try:
            dirs = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
        for d in dirs:
            if str(d).lower() in seen:
                continue
            seen.add(str(d).lower())
            entry = _build_entry(d, d / "project.json", "local")
            if entry:
                out.append(entry)

    out.sort(key=lambda e: (str(e.get("title") or "").lower(), e.get("id", "")))
    return out


def find_entry(entry_id: str, library: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    lib = library if library is not None else scan_library()
    for entry in lib:
        if entry.get("id") == entry_id:
            return entry
    return None


# ── Wallpaper Engine control ───────────────────────────────────────────────

_CONTROL_ACTIONS = {
    "pause",
    "play",
    "stop",
    "mute",
    "unmute",
    "nextWallpaper",
    "closeWallpaper",
}


def we_control(action: str, monitor: int | None = None) -> dict[str, Any]:
    """``wallpaper64.exe -control <action>``. WE must already be running."""
    exe = we_exe()
    if exe is None:
        return {"ok": False, "error": "Wallpaper Engine not found (set wallpaper_config paths)"}
    if action not in _CONTROL_ACTIONS:
        return {"ok": False, "error": f"unsupported action {action!r}; allowed: {sorted(_CONTROL_ACTIONS)}"}

    cmd = [str(exe), "-control", action]
    if monitor is not None:
        cmd += ["-monitor", str(int(monitor))]
    return _run(cmd)


def apply_wallpaper(entry: dict[str, Any], monitor: int = 0) -> dict[str, Any]:
    """Open a wallpaper on a monitor via WE's documented ``openWallpaper``.

    ``-file`` accepts a ``project.json``, a bare video file, or a Web
    wallpaper's HTML entry — we pass ``project_file`` when present (works for
    every type) and fall back to the media file.
    """
    exe = we_exe()
    if exe is None:
        return {"ok": False, "error": "Wallpaper Engine not found (set wallpaper_config paths)"}

    target = entry.get("project_file") or entry.get("media_file")
    if not target or not Path(str(target)).is_file():
        return {"ok": False, "error": f"no usable file for wallpaper {entry.get('id')!r}"}

    cmd = [str(exe), "-control", "openWallpaper", "-file", str(target), "-monitor", str(int(monitor))]
    return _run(cmd)


def _run(cmd: list[str]) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:  # noqa: BLE001 - surface, never raise into the agent
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    ok = proc.returncode == 0
    result: dict[str, Any] = {"ok": ok, "returncode": proc.returncode, "cmd": cmd[1:]}
    if out:
        result["stdout"] = out[:2000]
    if err:
        result["stderr"] = err[:2000]
    return result


def health() -> dict[str, Any]:
    lib = scan_library()
    by_type: dict[str, int] = {}
    for entry in lib:
        by_type[entry["type"]] = by_type.get(entry["type"], 0) + 1
    return {
        "ok": True,
        "we_dir": str(find_we_dir()) if find_we_dir() else None,
        "we_exe": str(we_exe()) if we_exe() else None,
        "workshop_roots": [str(p) for p in workshop_roots()],
        "count": len(lib),
        "by_type": by_type,
    }
