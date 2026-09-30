# hermes-wallpaper

Wallpaper Engine behind the Hermes Desktop chat — your Steam workshop library
as a live background, with translucent surfaces and text that stays readable
over it.

A **unified Hermes plugin**: one folder ships an agent half (tools), a desktop
half (background + settings UI), and a dashboard backend (media routes).

```
Wallpaper Engine ──► plugin backend ──► desktop background layer
      │                    │                      │
   -control            media routes          translucent UI
   (official CLI)      (Range video,          + hard text outline
                        data: images)
```

## What it does

- **Scans your Wallpaper Engine library** — workshop items (`steamapps/workshop/content/431960`)
  plus WE's own `projects/` tree. On a real library: ~110 wallpapers, no config needed.
- **Video wallpapers play live** behind the chat, streamed over the app's own
  `hermes-media://` Range protocol — seeking works and no bytes pass through JSON.
  (A 225 MB source file plays fine.)
- **Image wallpapers** render at full size.
- **Scene / web wallpapers** use their preview art. Real scene rendering is the
  B-tier extension point (`wangkaxds/we-scene`), noted in the skill file.
- **Text stays readable** without covering the wallpaper: a hard 1px outline in
  the theme's contrasting colour — the subtitle-industry technique, not a soft
  halo. The ring is derived from `--ui-bg-editor`, so it is light under dark text
  and dark under light text and self-compensates across the image.
- **Drive Wallpaper Engine** from the agent: scan, list, set, configure, and
  pause / play / next / mute.

## Install

```bash
# clone into your Hermes plugins folder (default ~/.hermes/plugins/)
git clone https://github.com/<you>/hermes-wallpaper.git \
  "${HERMES_HOME:-$HOME/.hermes}/plugins/hermes-wallpaper"

hermes plugins enable hermes-wallpaper
```

Then restart `hermes serve` — plugin API routes are mounted at process start.
The desktop half hot-reloads; the backend half does not, so a config-field change
to `plugin_api.py` is invisible until that restart happens.

## Settings

`Ctrl+K` → **Wallpaper: Open Settings** (or the **Wallpaper** row in the sidebar).

| Control | Notes |
|---|---|
| **Text outline opacity** | The smooth dial — 0–100%, 1% steps. Fine-tune here. |
| **Text outline width** | Coarse. Renderer snaps `text-shadow` offsets to device pixels, so this is stepped by nature. |
| **Text bed** | 0 = text floats straight on the wallpaper. |
| **Wallpaper dim** | Darkens via `brightness()`, never by fading toward the UI colour. |
| **Blur** | Background blur, 0–60px. |
| **Rendering** | Balanced / Power saver (30fps) / Static (preview art). |

## Agent tools

| Tool | What it does |
|---|---|
| `wallpaper_scan` | Rescan the library; reports install path, workshop roots, type counts |
| `wallpaper_list` | List wallpapers, filter by type or title substring |
| `wallpaper_set` | Apply a wallpaper in Wallpaper Engine AND select it as the chat background |
| `wallpaper_config` | Read/change any setting above, or point at a non-default WE install |
| `wallpaper_we_control` | pause / play / stop / mute / unmute / nextWallpaper / closeWallpaper |

```
wallpaper_list(type="video")
wallpaper_set(title="Aeolian")
wallpaper_config(outline_alpha=0.35)
```

## Safety

- **Read-only over your content.** Nothing here writes into Steam, Wallpaper
  Engine's own config, or workshop directories.
- Wallpaper Engine is driven only through its documented
  [`-control` command line](https://docs.wallpaperengine.io).
- No secrets, no telemetry, no network calls. Media never leaves the machine.
- The renderer never names a file — it names an `entry_id` the backend resolves
  inside the wallpaper's own directory. Path traversal is rejected.

## Requirements

- Windows, Wallpaper Engine (tested on 2.8.42)
- Hermes Agent ≥ 0.19 with the Desktop app

## Layout

```
hermes-wallpaper/
├── plugin.yaml              agent manifest (tools declared)
├── __init__.py              agent half: 5 tools
├── wallpaper_core.py        discovery + WE control (shared)
├── desktop/plugin.js        desktop half: background + settings page
├── dashboard/
│   ├── manifest.json        {"api": "plugin_api.py"}
│   └── plugin_api.py        media + config routes
└── skills/hermes-wallpaper/SKILL.md   implementation notes & pitfalls
```

## License

MIT — see [LICENSE](LICENSE). Wallpaper Engine and all workshop content belong
to their respective owners; this repository contains code only, no wallpaper
material.
