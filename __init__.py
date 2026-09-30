"""hermes-wallpaper — agent half: Wallpaper Engine tools.

The desktop half (``desktop/plugin.js``) paints the wallpaper behind the chat;
this half lets the agent drive it. Both read the same config file through
``wallpaper_core`` — the agent writes it here, the desktop half reads it over
``ctx.rest``, so "set the background" and "change the wallpaper" are one action.

Read-mostly: nothing here writes into Steam, Wallpaper Engine's own config, or
workshop content. ``wallpaper_set`` and ``wallpaper_we_control`` only invoke
Wallpaper Engine's documented ``-control`` command line.
"""

from __future__ import annotations

from typing import Any

from tools.registry import tool_error, tool_result

from . import wallpaper_core as core

TOOLSET = "wallpaper"

# Keep the entry payload small — the full library is 100+ rows with paths the
# model does not need. Titles + ids + types are what a picker conversation uses.
_LIST_FIELDS = ("id", "title", "type", "render_mode", "source", "workshopid")


def _brief(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: entry.get(k) for k in _LIST_FIELDS}


def _match_entry(query: str) -> dict[str, Any] | None:
    """Resolve a wallpaper by id, then by exact, then case-insensitive substring title."""
    q = (query or "").strip()
    if not q:
        return None
    lib = core.scan_library()
    entry = core.find_entry(q, lib)
    if entry:
        return entry
    for e in lib:
        if str(e.get("title") or "").strip() == q:
            return e
    lowered = q.lower()
    hits = [e for e in lib if lowered in str(e.get("title") or "").lower()]
    return hits[0] if len(hits) == 1 else None


def _handle_wallpaper_scan(args: dict, **kw) -> str:
    health = core.health()
    if not health.get("we_exe"):
        return tool_error(
            "Wallpaper Engine is not installed or not discoverable. "
            "Set the install path with wallpaper_config(we_dir=...).",
            health=health,
        )
    return tool_result(health)


def _handle_wallpaper_list(args: dict, **kw) -> str:
    want_type = str(args.get("type") or "").strip().lower()
    query = str(args.get("query") or "").strip().lower()
    limit = max(1, min(int(args.get("limit") or 50), 200))

    lib = core.scan_library()
    if want_type:
        lib = [e for e in lib if e.get("type") == want_type]
    if query:
        lib = [e for e in lib if query in str(e.get("title") or "").lower()]

    selected = core.load_config().get("selected_id")
    return tool_result(
        {
            "count": len(lib),
            "shown": min(len(lib), limit),
            "selected_id": selected,
            "entries": [_brief(e) for e in lib[:limit]],
        }
    )


def _handle_wallpaper_set(args: dict, **kw) -> str:
    ref = str(args.get("entry_id") or args.get("title") or args.get("query") or "").strip()
    if not ref:
        return tool_error("entry_id or title is required")
    monitor = int(args.get("monitor") or 0)

    entry = _match_entry(ref)
    if entry is None:
        return tool_error(f"no unique wallpaper matches {ref!r}; use wallpaper_list to see ids")

    # Two effects, one call: WE's desktop wallpaper AND the Hermes background.
    applied = core.apply_wallpaper(entry, monitor=monitor)
    if not applied.get("ok"):
        return tool_error(applied.get("error") or "Wallpaper Engine rejected openWallpaper", entry=_brief(entry))

    cfg = core.load_config()
    cfg["selected_id"] = entry["id"]
    cfg["enabled"] = True
    core.save_config(cfg)

    return tool_result(
        {
            "ok": True,
            "monitor": monitor,
            "entry": _brief(entry),
            "note": "Desktop wallpaper set via Wallpaper Engine; Hermes background follows on the next config poll.",
        }
    )


def _handle_wallpaper_config(args: dict, **kw) -> str:
    cfg = core.load_config()
    changed: dict[str, Any] = {}

    for key in ("enabled", "selected_id", "surface_opacity", "scrim_strength", "blur_px", "outline_px", "outline_alpha", "quality", "monitor"):
        if key in args and args[key] is not None:
            cfg[key] = args[key]
            changed[key] = args[key]

    # Allow pointing at a non-default install without editing WE or the registry.
    if args.get("we_dir"):
        import os

        os.environ["HERMES_WALLPAPER_WE_DIR"] = str(args["we_dir"])
        changed["we_dir"] = str(args["we_dir"])

    if changed:
        core.save_config(cfg)

    return tool_result(
        {
            "ok": True,
            "changed": changed,
            "config": cfg,
            "we_exe": str(core.we_exe()) if core.we_exe() else None,
        }
    )


def _handle_wallpaper_we_control(args: dict, **kw) -> str:
    action = str(args.get("action") or "").strip()
    if not action:
        return tool_error("action is required (pause|play|stop|mute|unmute|nextWallpaper|closeWallpaper)")
    monitor = args.get("monitor")
    result = core.we_control(action, monitor=int(monitor) if monitor is not None else None)
    if not result.get("ok"):
        return tool_error(result.get("error") or "Wallpaper Engine rejected the command")
    return tool_result(result)


def _check_available() -> bool:
    # Always expose the tools: when WE is missing they are how the agent
    # diagnoses the install rather than silently doing nothing.
    return True


_TOOLS: list[tuple[str, dict[str, Any], Any, str]] = [
    (
        "wallpaper_scan",
        {
            "name": "wallpaper_scan",
            "description": "Rescan the Wallpaper Engine library and report the install path, workshop roots, wallpaper count and type distribution.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        _handle_wallpaper_scan,
        "\U0001f50d",
    ),
    (
        "wallpaper_list",
        {
            "name": "wallpaper_list",
            "description": "List Wallpaper Engine wallpapers. Filter by type (video|image|scene|web) or a title substring. Returns ids, titles and types.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "description": "Filter: video, image, scene or web."},
                    "query": {"type": "string", "description": "Case-insensitive title substring."},
                    "limit": {"type": "integer", "description": "Max rows (default 50, max 200)."},
                },
                "required": [],
            },
        },
        _handle_wallpaper_list,
        "\U0001f5bc\ufe0f",
    ),
    (
        "wallpaper_set",
        {
            "name": "wallpaper_set",
            "description": "Set a wallpaper: applies it in Wallpaper Engine on a monitor AND selects it as the Hermes Desktop background. Match by id or an unambiguous title.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_id": {"type": "string", "description": "Wallpaper id from wallpaper_list."},
                    "title": {"type": "string", "description": "Alternative to entry_id: an unambiguous title match."},
                    "monitor": {"type": "integer", "description": "Monitor index, default 0."},
                },
                "required": [],
            },
        },
        _handle_wallpaper_set,
        "\U0001f3a8",
    ),
    (
        "wallpaper_config",
        {
            "name": "wallpaper_config",
            "description": "Read or change wallpaper settings: surface opacity, scrim strength, blur, quality tier, enabled, selected wallpaper, monitor, or the Wallpaper Engine install path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "description": "Toggle the Hermes background layer."},
                    "selected_id": {"type": "string", "description": "Select a wallpaper without touching the desktop."},
                    "surface_opacity": {"type": "number", "description": "0..1, UI surface opacity over the wallpaper."},
                    "scrim_strength": {"type": "number", "description": "0..1, darkening scrim for readability."},
                    "blur_px": {"type": "integer", "description": "Background blur in px, 0..60."},
                    "outline_px": {"type": "number", "description": "Text outline width in px, 0..2. 0 turns the outline off."},
                    "outline_alpha": {"type": "number", "description": "Text outline opacity, 0..1. This is the smooth dial — outline width is quantised to device pixels."},
                    "quality": {"type": "string", "description": "balanced | powersaver | low."},
                    "monitor": {"type": "integer", "description": "Default monitor index."},
                    "we_dir": {"type": "string", "description": "Wallpaper Engine install directory override."},
                },
                "required": [],
            },
        },
        _handle_wallpaper_config,
        "⚙\ufe0f",
    ),
    (
        "wallpaper_we_control",
        {
            "name": "wallpaper_we_control",
            "description": "Control Wallpaper Engine playback: pause, play, stop, mute, unmute, nextWallpaper or closeWallpaper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "pause|play|stop|mute|unmute|nextWallpaper|closeWallpaper"},
                    "monitor": {"type": "integer", "description": "Optional monitor index."},
                },
                "required": ["action"],
            },
        },
        _handle_wallpaper_we_control,
        "\u23ef\ufe0f",
    ),
]


def register(ctx) -> None:
    """Register the Wallpaper Engine tools (called once by the plugin loader)."""
    for name, schema, handler, emoji in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=schema,
            handler=handler,
            description=schema.get("description", ""),
            check_fn=_check_available,
            emoji=emoji,
        )
