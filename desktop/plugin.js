/**
 * hermes-wallpaper — Wallpaper Engine behind the Hermes Desktop chat.
 *
 * Unified agent+desktop package (electron/desktop-plugins-root.ts):
 *   SOURCE     ~/.hermes/plugins/hermes-wallpaper/desktop/plugin.js  (this file)
 *   app copy   ~/.hermes/desktop-plugins/hermes-wallpaper/plugin.js
 *              + .hermes-package.json marker (materialized by the app)
 *   backend    ~/.hermes/plugins/hermes-wallpaper/dashboard/plugin_api.py
 *              mounted at /api/plugins/hermes-wallpaper/ (plugins.enabled).
 *
 * Surfaces:
 *   - A fixed background layer: <video> for video wallpapers (streamed over the
 *     app's own hermes-media:// Range protocol), <img> for image/preview art.
 *   - Theme-translucent surfaces + a readability scrim, driven by CSS custom
 *     properties so every theme keeps working. No hardcoded colours.
 *   - Settings ▸ Appearance ▸ Wallpaper: picker + opacity/blur/quality controls.
 *
 * Plain ESM loaded uncompiled: UI is jsx() calls, NOT JSX syntax; only
 * @hermes/plugin-sdk, react, react/jsx-runtime resolve. The background layer is
 * therefore plain DOM (react-dom is not importable) and the settings card is
 * React through its own ROUTES_AREA page + SIDEBAR_NAV_AREA row.
 *
 * Media never leaves the machine: the backend hands back a local path or a
 * data: URL; nothing is uploaded, nothing calls a remote service.
 */

import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  GlyphSpinner,
  host,
  PALETTE_AREA,
  ROUTES_AREA,
  SegmentedControl,
  Separator,
  SIDEBAR_NAV_AREA,
  Switch,
  useQuery
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useRef, useState } from 'react'

const ID = 'hermes-wallpaper'
const PAGE_PATH = '/wallpaper'
const STYLE_ID = `${ID}-style`
const ROOT_ID = `${ID}-root`
const POLL_MS = 3000

// Captured in register(); the module is re-evaluated per load so this is safe.
let rest = null
let storageGet = null
let storageSet = null

// ── module-level change bus ──────────────────────────────────────────────
// The settings card (React) and the background layer (plain DOM) both live in
// this module; they coordinate through listeners rather than a shared store,
// because only one of them can use React state.
const listeners = new Set()
function notifyChanged() {
  for (const fn of listeners) {
    try {
      fn()
    } catch {
      /* a stale listener must never break the others */
    }
  }
}
function subscribeChanged(fn) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

const DEFAULTS = {
  selected_id: null,
  enabled: true,
  // 0 = text floats straight on the wallpaper (no bed). The app's own
  // Message Bubble lever owns the user bubble; we never paint message rows.
  surface_opacity: 0,
  scrim_strength: 0.25,
  blur_px: 0,
  outline_px: 0.5,
  outline_alpha: 0.85,
  quality: 'balanced',
  monitor: 0
}

const QUALITY_OPTIONS = [
  { id: 'balanced', label: 'Balanced' },
  { id: 'powersaver', label: 'Power saver' },
  { id: 'low', label: 'Static' }
]

// ── CSS: theme-translucent surfaces, never a hardcoded colour ────────────

function surfaceCss(cfg) {
  // `surface_opacity` is the TEXT BED: 0 = text floats straight on the
  // wallpaper (what "没有气泡" means), 1 = a solid reading bed. It deliberately
  // does NOT paint message rows — official Hermes has no assistant-message
  // bubble and neither do we.
  const bedPct = Math.round(clamp(cfg.surface_opacity, 0, 1) * 100)
  // Two dials, on purpose. WIDTH is quantised by the renderer — text-shadow
  // offsets snap to device pixels, so at 200% scaling 0.25px and 0.5px can
  // render identically and the slider feels stepped. ALPHA is continuous at
  // any value, so that is the dial that actually fine-tunes how prominent the
  // ring reads. Ring colour comes from --ui-bg-editor (light under dark text,
  // dark under light text) faded by the alpha.
  const o = clamp(cfg.outline_px, 0, 2)
  const a = clamp(cfg.outline_alpha, 0, 1)
  const ring =
    o > 0 && a > 0
      ? [
          `${-o}px ${-o}px 0 var(--hermes-wallpaper-ring)`,
          `${o}px ${-o}px 0 var(--hermes-wallpaper-ring)`,
          `${-o}px ${o}px 0 var(--hermes-wallpaper-ring)`,
          `${o}px ${o}px 0 var(--hermes-wallpaper-ring)`,
          `0 ${-o}px 0 var(--hermes-wallpaper-ring)`,
          `0 ${o}px 0 var(--hermes-wallpaper-ring)`,
          `${-o}px 0 0 var(--hermes-wallpaper-ring)`,
          `${o}px 0 0 var(--hermes-wallpaper-ring)`
        ].join(',\n    ')
      : 'none'
  return `
#${ROOT_ID} {
  position: fixed;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  pointer-events: none;
  /* Only a fallback for when no wallpaper is selected. The media below is
     painted OPAQUE over it — blending at reduced opacity is what put a milky
     haze over the wallpaper before. */
  background-color: var(--ui-bg-editor);
}
#${ROOT_ID} > * {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  /* Dim with brightness, never by fading toward the UI surface colour —
     fading toward a light surface is a white haze, not a dim. */
  opacity: 1;
  filter: brightness(${(1 - clamp(cfg.scrim_strength, 0, 1) * 0.35).toFixed(3)})${cfg.blur_px > 0 ? ` blur(${Math.round(cfg.blur_px)}px)` : ''};
}
html, body { background-color: transparent !important; }

/* Shell fields go transparent so the wallpaper reads through the chrome.
   Chat bubbles and code cards KEEP the app's own tokens — those are handled
   by the app's own Message Bubble lever and stay readable by design. */
:root {
  --ui-surface-background: transparent !important;
  --ui-chat-surface-background: transparent !important;
  --ui-editor-surface-background: transparent !important;
  --ui-widget-surface-background: transparent !important;
  --ui-terminal-surface-background: transparent !important;
  /* Deliberately NOT touched: --dt-* (the tooltip/popover fill — transparenting
     it leaves a black frame with unreadable text) and --ui-sidebar-surface-
     background (the app already renders the sidebar over the wallpaper; forcing
     a colour-mix fill paints it opaque again). */
  --hermes-wallpaper-bed: color-mix(in srgb, var(--ui-bg-editor) ${bedPct}%, transparent);
  --hermes-wallpaper-ring: color-mix(in srgb, var(--ui-bg-editor) ${Math.round(a * 100)}%, transparent);
}

/* Optional reading bed, and only behind the column — never per message. */
[data-slot="aui_thread-content"] {
  background-color: var(--hermes-wallpaper-bed);
}

/* Hard 1px outline, not a soft halo — this is the subtitle-industry technique
   (ASS has Outline and Shadow as separate params; a soft halo is the Shadow
   one and reads as fake 3D). Eight zero-blur text-shadows paint a crisp ring
   BEHIND the glyph.

   The ring colour is derived from --ui-bg-editor: light in a light theme (so
   it separates dark text from a dark wallpaper region), dark in a dark theme
   (separating light text from a bright region). It self-compensates — where
   the wallpaper already contrasts, the ring is invisible; where it doesn't,
   the ring carries the contrast. */
[data-slot="aui_thread-content"] .aui-md :where(p, li, h1, h2, h3, h4, h5, h6, blockquote, td, th, dt, dd),
[data-slot="aui_assistant-message-content"] > p,
[data-slot="aui_user-message-root"] p,
[data-slot="aui_system-message-root"] {
  color: var(--ui-text-primary);
  font-weight: 700;
  text-shadow: ${ring};
}
`
}

function clamp(value, min, max) {
  const n = Number(value)
  if (!Number.isFinite(n)) return min
  return Math.min(max, Math.max(min, n))
}

function ensureStyle() {
  let el = document.getElementById(STYLE_ID)
  if (!el) {
    el = document.createElement('style')
    el.id = STYLE_ID
    document.head.appendChild(el)
  }
  return el
}

function applyStyle(cfg) {
  ensureStyle().textContent = surfaceCss(cfg)
}

function ensureRoot() {
  let el = document.getElementById(ROOT_ID)
  if (!el) {
    el = document.createElement('div')
    el.id = ROOT_ID
    el.setAttribute('aria-hidden', 'true')
    document.body.appendChild(el)
  }
  return el
}

function teardown() {
  const root = document.getElementById(ROOT_ID)
  if (root) root.remove()
  const style = document.getElementById(STYLE_ID)
  if (style) style.remove()
}

// ── background layer: plain DOM, no react-dom available ──────────────────

let currentRenderKey = ''
// The live <video> element, so blur/focus handling (ctx-scoped) can pause it.
// Set on render, cleared on teardown — never a bare window listener, which
// would outlive a disable/reload.
let activeVideo = null

async function renderBackground(cfg) {
  const root = ensureRoot()
  applyStyle(cfg)

  const id = cfg.selected_id
  const renderKey = JSON.stringify([id, cfg.quality, cfg.enabled])
  if (renderKey === currentRenderKey) return
  currentRenderKey = renderKey

  root.replaceChildren()

  if (!cfg.enabled || !id) return

  // 'low' tier deliberately shows the static art instead of decoding video —
  // the one knob that meaningfully drops CPU/GPU on a laptop.
  let mode = 'preview'
  try {
    const lib = await libraryOnce()
    const entry = (lib.entries || []).find(e => e.id === id)
    if (entry) mode = entry.render_mode === 'video' && cfg.quality !== 'low' ? 'video' : entry.render_mode
  } catch {
    /* fall through to preview */
  }

  if (mode === 'video') {
    try {
      const info = await rest(`/stream-url/${encodeURIComponent(id)}`)
      const video = document.createElement('video')
      video.src = info.url
      video.autoplay = true
      video.loop = true
      video.muted = true
      video.playsInline = true
      video.setAttribute('aria-hidden', 'true')
      video.addEventListener('error', () => renderFallback(root, id))
      root.appendChild(video)
      await video.play().catch(() => {})
      activeVideo = video
      return
    } catch {
      /* fall through to preview */
    }
  }

  await renderFallback(root, id)
}

// ── library cache ────────────────────────────────────────────────────────
// /library walks the whole workshop tree (~110 dirs). It runs on demand and is
// cached briefly — the 3s config poll must never re-trigger a filesystem scan.

let libraryCache = null
let libraryCacheAt = 0
const LIBRARY_TTL_MS = 60_000

async function libraryOnce() {
  const now = Date.now()
  if (libraryCache && now - libraryCacheAt < LIBRARY_TTL_MS) return libraryCache
  libraryCache = await rest('/library')
  libraryCacheAt = now
  return libraryCache
}

async function renderFallback(root, id) {
  root.replaceChildren()
  const dataUrl = await fetchImageDataUrl(id, 'preview')
  if (!dataUrl) return
  const img = document.createElement('img')
  img.alt = ''
  img.setAttribute('aria-hidden', 'true')
  img.src = dataUrl
  root.appendChild(img)
}

// ── data URL fetch (images) ──────────────────────────────────────────────

async function fetchImageDataUrl(id, which) {
  try {
    const out = await rest(`/image/${encodeURIComponent(id)}?which=${which || 'preview'}`)
    return (out && out.data_url) || null
  } catch {
    return null
  }
}

// ── settings page (React) ─────────────────────────────────────────────────

function WallpaperPage() {
  return jsxs('div', {
    className: 'mx-auto flex w-full max-w-3xl flex-col gap-5 px-6 py-6',
    children: [
      jsxs('div', {
        className: 'flex flex-col gap-1',
        children: [
          jsx('h1', { className: 'text-xl font-semibold', children: 'Wallpaper Engine' }),
          jsx('p', {
            className: 'text-sm',
            style: { color: 'var(--ui-text-tertiary)' },
            children:
              'Your Wallpaper Engine library behind the chat. Video and image wallpapers render live; scene and web wallpapers use their preview art.'
          })
        ]
      }),
      jsx(WallpaperCard, {})
    ]
  })
}

function WallpaperCard() {
  const [selected, setSelected] = useState(storageGet ? storageGet('selected', null) : null)
  const [query, setQuery] = useState('')
  const [previewUrl, setPreviewUrl] = useState(null)

  const library = useQuery({
    queryKey: [ID, 'library'],
    queryFn: () => rest('/library'),
    staleTime: 30_000
  })

  const cfg = (library.data && library.data.config) || DEFAULTS
  const entries = (library.data && library.data.entries) || []
  const filtered = query
    ? entries.filter(e => String(e.title || '').toLowerCase().includes(query.toLowerCase()))
    : entries

  const activeId = selected || cfg.selected_id

  useEffect(() => {
    let cancelled = false
    if (!activeId) {
      setPreviewUrl(null)
      return () => {}
    }
    fetchImageDataUrl(activeId, 'preview').then(url => {
      if (!cancelled) setPreviewUrl(url)
    })
    return () => {
      cancelled = true
    }
  }, [activeId])

  const save = useCallback(
    async patch => {
      try {
        await rest('/config', { method: 'POST', body: patch })
        library.refetch()
        notifyChanged()
      } catch (err) {
        host.notifyError(`${ID}: ${String(err)}`)
      }
    },
    [library]
  )

  const choose = useCallback(
    async entry => {
      setSelected(entry.id)
      if (storageSet) storageSet('selected', entry.id)
      await save({ selected_id: entry.id, enabled: true })
    },
    [save]
  )

  if (library.isLoading) {
    return jsx('div', { className: 'p-4' }, jsx(GlyphSpinner, {}))
  }
  if (library.isError) {
    return jsx(ErrorState, {
      title: 'Wallpaper Engine unavailable',
      description: String(library.error || 'The plugin backend did not respond.')
    })
  }

  return jsxs('div', {
    className: 'flex flex-col gap-4 p-4',
    children: [
      jsxs('div', {
        className: 'flex items-start gap-4',
        children: [
          jsx('div', {
            className: 'h-28 w-48 shrink-0 overflow-hidden rounded-md border',
            style: { background: 'var(--ui-bg-tertiary)' },
            children: previewUrl
              ? jsx('img', {
                  src: previewUrl,
                  alt: '',
                  className: 'h-full w-full object-cover'
                })
              : jsx('div', {
                  className: 'flex h-full w-full items-center justify-center text-xs',
                  style: { color: 'var(--ui-text-tertiary)' },
                  children: 'No preview'
                })
          }),
          jsxs('div', {
            className: 'flex flex-1 flex-col gap-2',
            children: [
              jsxs('div', {
                className: 'flex items-center gap-2',
                children: [
                  jsx('span', { className: 'text-sm font-medium', children: 'Wallpaper Engine background' }),
                  jsx(Badge, {
                    children: `${entries.length} wallpapers`
                  })
                ]
              }),
              jsx('div', {
                className: 'flex items-center gap-2',
                children: jsx(Switch, {
                  checked: cfg.enabled !== false,
                  onCheckedChange: v => void save({ enabled: !!v })
                })
              }),
              jsx('div', {
                className: 'text-xs',
                style: { color: 'var(--ui-text-tertiary)' },
                children:
                  'Video and image wallpapers render live. Scene and web wallpapers use their preview art. To drop the bubble on your own messages, use Settings ▸ Appearance ▸ Message Bubble at 100.'
              })
            ]
          })
        ]
      }),
      jsx(Separator, {}),
      jsxs('div', {
        className: 'flex flex-col gap-3',
        children: [
          RangeRow({
            label: 'Text bed',
            hint: '0 = text floats directly on the wallpaper',
            value: cfg.surface_opacity,
            min: 0,
            max: 1,
            step: 0.05,
            onChange: v => void save({ surface_opacity: v })
          }),
          RangeRow({
            label: 'Wallpaper dim',
            hint: 'Dims the wallpaper behind the UI',
            value: cfg.scrim_strength,
            min: 0,
            max: 1,
            step: 0.05,
            onChange: v => void save({ scrim_strength: v })
          }),
          RangeRow({
            label: 'Blur',
            hint: 'Background blur in pixels',
            value: cfg.blur_px,
            min: 0,
            max: 60,
            step: 1,
            onChange: v => void save({ blur_px: Math.round(v) })
          }),
          RangeRow({
            label: 'Text outline width',
            hint: 'Ring thickness. 0 turns the outline off',
            value: cfg.outline_px,
            min: 0,
            max: 2,
            step: 0.05,
            onChange: v => void save({ outline_px: v })
          }),
          RangeRow({
            label: 'Text outline opacity',
            hint: 'The smooth dial — width snaps to screen pixels, this does not',
            value: cfg.outline_alpha,
            min: 0,
            max: 1,
            step: 0.01,
            onChange: v => void save({ outline_alpha: v })
          }),
          jsxs('div', {
            className: 'flex items-center justify-between gap-3',
            children: [
              jsx('span', { className: 'text-sm', children: 'Rendering' }),
              jsx(SegmentedControl, {
                value: cfg.quality || 'balanced',
                options: QUALITY_OPTIONS,
                onChange: v => void save({ quality: v })
              })
            ]
          })
        ]
      }),
      jsx(Separator, {}),
      jsxs('div', {
        className: 'flex flex-col gap-2',
        children: [
          jsx('div', {
            className: 'flex items-center gap-2',
            children: jsx('input', {
              type: 'search',
              placeholder: 'Filter by title…',
              value: query,
              onChange: e => setQuery(e.target.value),
              className: 'w-full rounded-md border px-2 py-1 text-sm',
              style: {
                background: 'var(--ui-bg-input)',
                color: 'var(--ui-text-primary)',
                borderColor: 'var(--ui-border)'
              }
            })
          }),
          filtered.length === 0
            ? jsx(EmptyState, { title: 'No wallpapers match', description: 'Try a different filter.' })
            : jsx('div', {
                className: 'flex max-h-72 flex-col gap-1 overflow-y-auto',
                children: filtered.slice(0, 200).map(entry =>
                  jsx(WallpaperRow, {
                    entry,
                    active: entry.id === activeId,
                    onChoose: choose
                  })
                )
              })
        ]
      }),
      jsxs('div', {
        className: 'flex items-center gap-2',
        children: [
          jsx(Button, {
            variant: 'outline',
            onClick: () => void control('pause'),
            children: 'Pause'
          }),
          jsx(Button, {
            variant: 'outline',
            onClick: () => void control('play'),
            children: 'Play'
          }),
          jsx(Button, {
            variant: 'outline',
            onClick: () => void control('nextWallpaper'),
            children: 'Next'
          })
        ]
      })
    ]
  })
}

function RangeRow({ label, hint, value, min, max, step, onChange }) {
  return jsxs('div', {
    className: 'flex flex-col gap-1',
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between',
        children: [
          jsx('span', { className: 'text-sm', children: label }),
          jsx('span', {
            className: 'text-xs',
            style: { color: 'var(--ui-text-tertiary)' },
            children: String(value)
          })
        ]
      }),
      jsx('input', {
        type: 'range',
        min: String(min),
        max: String(max),
        step: String(step),
        value: String(value),
        onChange: e => onChange(Number(e.target.value)),
        className: 'w-full'
      }),
      hint
        ? jsx('div', {
            className: 'text-xs',
            style: { color: 'var(--ui-text-tertiary)' },
            children: hint
          })
        : null
    ]
  })
}

function WallpaperRow({ entry, active, onChoose }) {
  return jsx('button', {
    type: 'button',
    onClick: () => void onChoose(entry),
    className: 'flex items-center gap-2 rounded-md border px-2 py-1 text-left text-sm',
    style: {
      background: active ? 'var(--ui-accent)' : 'var(--ui-bg-secondary)',
      color: active ? 'var(--ui-accent-foreground)' : 'var(--ui-text-primary)',
      borderColor: 'var(--ui-border)'
    },
    children: jsxs('span', {
      className: 'flex w-full items-center gap-2',
      children: [
        jsx('span', { className: 'truncate', children: entry.title }),
        jsx('span', {
          className: 'ml-auto shrink-0 text-[10px] uppercase',
          style: { opacity: 0.7 },
          children: entry.render_mode
        })
      ]
    })
  })
}

async function control(action) {
  try {
    await rest('/we/control', { method: 'POST', body: { action } })
    host.notify(`Wallpaper Engine: ${action}`)
  } catch (err) {
    host.notifyError(`${ID}: ${String(err)}`)
  }
}

// ── plugin ───────────────────────────────────────────────────────────────

export default {
  id: ID,
  name: 'Wallpaper Engine',
  description: 'Wallpaper Engine library as the chat background, with translucent surfaces and a picker in Appearance.',
  defaultEnabled: true,

  register(ctx) {
    rest = ctx.rest
    storageGet = (key, fallback) => ctx.storage.get(key, fallback)
    storageSet = (key, value) => ctx.storage.set(key, value)

    ensureStyle()
    ensureRoot()

    let disposed = false
    let lastKey = ''

    // Cheap config poll: /config is a JSON file read, not a rescan. The heavy
    // /library scan happens once when the settings card opens.
    const refresh = async () => {
      if (disposed) return
      try {
        const cfg = await rest('/config')
        const merged = { ...DEFAULTS, ...cfg }
        const key = JSON.stringify(merged)
        if (key !== lastKey) {
          lastKey = key
          await renderBackground(merged)
        }
      } catch {
        /* backend not up yet — the next tick retries */
      }
    }

    void refresh()
    const disposePoll = ctx.setInterval(() => void refresh(), POLL_MS)

    // Unfocused/minimised windows must not decode video. All listeners are
    // ctx-scoped so a disable/reload cannot leave them behind.
    const syncPlayback = () => {
      if (!activeVideo) return
      if (document.hidden) {
        activeVideo.pause()
      } else {
        activeVideo.play().catch(() => {})
      }
    }
    const disposeBlur = ctx.addEventListener(window, 'blur', () => {
      if (activeVideo) activeVideo.pause()
    })
    const disposeFocus = ctx.addEventListener(window, 'focus', syncPlayback)
    const disposeVisibility = ctx.addEventListener(document, 'visibilitychange', syncPlayback)

    const disposeBus = subscribeChanged(() => void refresh())

    ctx.onDispose(() => {
      disposed = true
      disposePoll()
      disposeBlur()
      disposeFocus()
      disposeVisibility()
      disposeBus()
      currentRenderKey = ''
      activeVideo = null
      teardown()
    })

    // A dedicated page + sidebar row, not the Appearance slot: the Appearance
    // extra slot only mounts on the top-level Appearance page, and that page is
    // unreachable from the nav (the parent row only toggles its disclosure —
    // it never navigates). A route is always reachable.
    ctx.registerMany([
      {
        id: 'route',
        area: ROUTES_AREA,
        data: { path: PAGE_PATH },
        render: () => jsx(WallpaperPage, {})
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 70,
        data: { codicon: 'device-camera', label: 'Wallpaper', path: PAGE_PATH }
      },
      {
        id: 'open',
        area: PALETTE_AREA,
        data: {
          id: `${ID}.open`,
          label: 'Wallpaper: Open Settings',
          keywords: ['wallpaper', 'background', 'wallpaper engine', '壁纸', '背景'],
          run: () => host.navigate(PAGE_PATH)
        }
      },
      {
        id: 'pause',
        area: PALETTE_AREA,
        data: {
          id: `${ID}.pause`,
          label: 'Wallpaper: Pause Wallpaper Engine',
          keywords: ['wallpaper', 'pause', 'wallpaper engine'],
          run: () => void control('pause')
        }
      },
      {
        id: 'play',
        area: PALETTE_AREA,
        data: {
          id: `${ID}.play`,
          label: 'Wallpaper: Resume Wallpaper Engine',
          keywords: ['wallpaper', 'play', 'resume', 'wallpaper engine'],
          run: () => void control('play')
        }
      }
    ])
  }
}
