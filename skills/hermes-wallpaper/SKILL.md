---
name: hermes-wallpaper
description: "Use when working on the hermes-wallpaper plugin (Wallpaper Engine as the Hermes Desktop background) — its data flow, the desktop-plugin constraints that shaped it, and the media-handling traps."
---

# hermes-wallpaper — implementation notes

A unified plugin: agent tools + a desktop background layer + a dashboard
backend, one folder at `~/.hermes/plugins/hermes-wallpaper/`.

## The one thing to understand

`ctx.rest` is a **JSON door** — it goes through `window.hermesDesktop.api` (IPC),
returns parsed JSON, and cannot feed an `<img>` or `<video>` element. So media
takes one of exactly two paths:

| media | path | why |
|---|---|---|
| image / preview art | backend returns a `data:` URL over `ctx.rest` | the renderer cannot name a local file, and there is no plugin-facing binary door |
| video | backend returns `hermes-media://stream/<encoded-path>`; the element loads that URL directly | the app's own custom Electron protocol — Range-capable, so seeking works, and no bytes pass through JSON |

`hermes-media://stream/` only accepts streamable extensions (mp4/webm/mov/mkv/
avi + audio). Images get a 415 there — hence the data-URL route.

## Desktop plugin constraints (they are not negotiable)

- **No JSX on disk plugins.** `jsx()` / `jsxs()` from `react/jsx-runtime` only.
- **Exactly three import specifiers resolve:** `@hermes/plugin-sdk`, `react`,
  `react/jsx-runtime`. `react-dom` is NOT one of them → no `createRoot`, so a
  background layer has to be plain DOM (`createElement` + `appendChild`).
- **Never hardcode colours.** Derive translucency by wrapping the app's own
  token in `color-mix(in srgb, var(--ui-bg-editor) N%, transparent)` — that
  survives every theme without naming a colour.
- **Bare globals leak.** `window.setInterval`, `window.addEventListener` and an
  appended `<style>` all outlive a disable/reload. Use `ctx.setInterval` /
  `ctx.addEventListener` / `ctx.onDispose`.
- `APPEARANCE_AREAS.extra` is the seam for settings-page controls.

## Package shape

```
~/.hermes/plugins/hermes-wallpaper/
├── plugin.yaml            kind: standalone
├── __init__.py            agent half: register(ctx) → ctx.register_tool(...)
├── wallpaper_core.py      discovery + WE control (shared)
├── desktop/plugin.js      desktop half (copied to ~/.hermes/desktop-plugins/)
└── dashboard/
    ├── manifest.json      {"name": "...", "api": "plugin_api.py"}
    └── plugin_api.py      mounted at /api/plugins/hermes-wallpaper/
```

## Traps hit on this build

- **`dashboard/plugin_api.py` is loaded standalone** (`spec_from_file_location`,
  no package) — a bare `import wallpaper_core` fails because the plugin dir is
  not on `sys.path`. Load the sibling by path with `importlib`. The agent half,
  by contrast, IS a package (`hermes_plugins.<slug>`), so `from . import x` works
  there. Two different import contexts in one folder.
- **`projects/` under the WE install is a container of containers**
  (`defaultprojects`, `myprojects`, `templates`) — scanning it directly yields
  three fake "wallpapers" named after the containers. Descend one level and skip
  `templates`. A wallpaper is a directory **with a `project.json`**; directories
  without one are partial downloads and must be skipped.
- **Never let the 3s config poll rescan the library.** `/library` walks ~110
  workshop dirs. Poll `/config` (a JSON file read); cache `/library` with a TTL.
- **Text over a wallpaper needs a HARD outline, never a soft halo.** A
  `text-shadow` with blur, tinted from `--ui-bg-editor`, paints a WHITE rim in a
  light theme — it reads as fake 3D and blurs the type. The subtitle-industry
  answer is eight zero-blur `text-shadow` segments forming a crisp 1px ring
  painted behind the glyph (ASS exposes `Outline` and `Shadow` as *separate*
  params for exactly this reason). Derive the ring from `--ui-bg-editor` so it
  is light under dark text and dark under light text — it self-compensates:
  invisible where the wallpaper already contrasts, carrying contrast where it
  does not. Hardcoding white/black breaks the other theme.
- **`--dt-*` is the tooltip/popover fill.** Transparenting it leaves a black
  frame with unreadable text on hover. And **`--ui-sidebar-surface-background`
  is already transparent in this app** — forcing a `color-mix` fill there paints
  the sidebar opaque and kills the wallpaper behind it. Neutralise only the
  shell surface tokens: `--ui-surface-background`, `--ui-chat-surface-background`,
  `--ui-editor-surface-background`, `--ui-widget-surface-background`,
  `--ui-terminal-surface-background`.
- **Dim a wallpaper with `brightness()`, never by fading `opacity`** toward the
  UI surface colour — fading toward a light surface is a white haze over the
  image, not a dim.
- **The desktop half hot-reloads; the backend half does NOT.** `plugin_api.py`
  is imported once when `hermes serve` starts, so adding a config field there is
  invisible until that process restarts — and pydantic silently IGNORES unknown
  fields on `POST /config`, so the UI slider appears to be un-drivable (it moves,
  the write is dropped, the next refetch snaps it back) with no error anywhere.
  If a plugin control "won't stick", compare `plugin_api.py`'s mtime against the
  serve process start time before debugging the React side.
- **`text-shadow` offsets snap to device pixels, so outline WIDTH is a stepped
  dial** — at 200% scaling 0.25px and 0.5px can render identically and the
  control feels quantised. ALPHA renders continuously at any value, so expose
  opacity as the fine-tuning dial and width as the coarse one. Measuring a
  sub-pixel "thin ring" by width alone will always feel wrong to the user.
- **`APPEARANCE_AREAS.extra` is a dead slot in this app.** It only mounts when
  `subpage === undefined` (the top-level Appearance page), and the nav's parent
  row never navigates — clicking it only toggles the disclosure of its children,
  so the top-level page is unreachable and no plugin card can ever appear there.
  Use `ROUTES_AREA` + `SIDEBAR_NAV_AREA` + `PALETTE_AREA` for a settings UI, the
  way `hermes-newswire` does.
- **`SegmentedControl` takes `onChange` and `options[].id`** — not `onValueChange`
  / `options[].value`. The wrong names render blank pills that silently do
  nothing on click. `Switch` takes Radix `checked` / `onCheckedChange`;
  `Button` takes `variant` (incl. `'outline'`); `EmptyState`/`ErrorState` take
  `{ title, description }`.
- WE is driven only through its documented CLI
  (`wallpaper64.exe -control <action>`), never by editing its config. Scanning
  and control are verified working on this machine (WE 2.8.42).

## B-tier extension point

Scene wallpapers (the majority of any library) currently render as their
preview art. Real scene rendering needs `wangkaxds/we-scene` (MIT — scene.pkg
parser + HLSL→GLSL transpiler + WebGL2 pipeline), which cannot be imported
under the three-specifier rule. The route is to serve it from the plugin
backend and mount it through the SDK's `SandboxedFrame` (http(s)/data only,
`allow-scripts` posture). Do that as its own task.
