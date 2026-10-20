# UI design — Liquid Glass for Reels Saver

Owner: ui worker. Scope: everything under `ui/`. The JS <-> Python contract is `docs/BRIDGE_API.md`; this document
describes the material, the design system and the motion of the UI that codes against it.

## 1. Sources and what was taken from each

| Source | Taken |
|---|---|
| Apple, WWDC25 "Meet Liquid Glass" (developer.apple.com/videos/play/wwdc2025/219) | Lensing instead of scattering; light that moves over the rim; larger surfaces are "thicker" (wider bezel, deeper shadow, more frost); materialize in / out by ramping the lensing, not by fading; controls morph and sheets grow out of the control that opened them; gel-like press that energizes with light under the finger; "no glass on glass" (controls on a glass surface use fills and vibrancy and lift into glass only while touched); tint only the primary action; the content layer is not glass. |
| kube.io, "Liquid Glass in the browser: refraction with CSS and SVG" | The physics: convex squircle height profile `y = (1 - (1 - x)^4)^(1/4)` across the bezel, surface normal from the derivative, Snell's law with n = 1.5, displacement magnitudes in a look-up table, map encoded as R = x shift, G = y shift with 128 = no shift, `feDisplacementMap scale = 2 * max displacement`, `backdrop-filter: url(#filter)` (Chromium only), maps must match the element size, animate `scale` instead of regenerating. |
| github.com/archisvaze/liquid-glass (MIT) | Parameter set per surface: bezel width, glass thickness, IOR, specular opacity, tint, inner / outer shadow; presets for pill / card / circle. No code copied. |
| github.com/shuding/liquid-glass (MIT) | Canvas pixel loop that writes a displacement map and normalises by the maximum shift; extra `contrast / brightness / saturate` after the url() filter. No code copied. |
| aave.com/design/building-glass-for-the-web | Four-fold symmetry (compute one quadrant, mirror the rest); regenerate the map only on shape change, never on movement; a lens over a highlighted copy keeps the selected label legible (used by the segmented droplet); spring instead of linear for toggle groups. |
| dev.to, "Liquid Glass on the web: 6 ways" | `saturate()` after blur so the glass is not grey; filter regions and backdrop roots clip and isolate effects; a blur-only fallback for many repeated surfaces. |

## 2. Material

### 2.1 Lens (refracting surface) — `js/glass.js`, `css/glass.css`

`new Lens(el, preset)` builds, per element, an SVG filter in `#lg-defs`:

```
feGaussianBlur(frost) -> feDisplacementMap(map, scale) -> feColorMatrix(saturate)
```

applied with `backdrop-filter: url(#lgN) <preset css>`. A `ResizeObserver` regenerates the map when the size or the
border radius changes; maps are cached (LRU, 80 entries) by `w x h | radius | bezel | thickness`; surfaces larger than
120 000 px get a half-resolution map (the field is smooth, `feImage` stretches it).

Map generation: rounded-rect distance to the edge and inward normal per pixel of the top-left quadrant; inside the bezel
`t = d / bezel`, displacement = LUT(t) (Snell trace through the squircle profile) along the inward normal; the outer half
pixel fades to neutral; the other quadrants are mirrored with flipped signs. Displacement points inward (convex lens),
so the backdrop is never sampled outside the element.

Presets (bezel / thickness may depend on the short side `s`):

| preset | used by | bezel | thickness | frost (px) | saturate | extra |
|---|---|---|---|---|---|---|
| `pill` | engine chip, gear | s/2 | 0.75 s | 0.6 | 1.7 | brightness 1.08 |
| `capsule` | queue header, toasts, clipboard pill | min(s/2, 22) | min(0.7 s, 30) | 1.2 | 1.7 | brightness 1.06 |
| `panel` | input capsule | 28 | 38 | 2.5 | 1.6 | brightness 1.04 |
| `sheet` | settings sheet, engine popover | 30 | 40 | 14 | 1.5 | brightness 0.92 |
| `droplet` | segmented indicator, switch thumbs (only while pressed) | s/2 | 0.9 s | 0 | 1.4 | brightness 1.12 |

Strength `0..1` (`lens.setStrength`, `lens.animateStrength`) scales displacement, frost and saturation together; 0 sets
`backdrop-filter: none`. Materialize in / out = strength spring, never an opacity fade of a parent.

CSS layers inside a lens (bottom -> top): refracted backdrop, tint (`--lens-tint` background), pointer glow
(`.lg-glow`, z-index -1, radial gradient at `--mx/--my`, `--hot` 0..1.8), content, specular rim (`.lg-rim`, 1.25 px ring
via mask-composite, conic gradient with a lit lobe at the light angle `--la` and a weaker counter-reflection at +180°,
`mix-blend-mode: plus-lighter`). Shadow: `--lens-shadow` (deeper for larger surfaces) plus an inner bloom.

Light: `--la` on `:root` drifts with the pointer across the window (`-48° + 40°·x - 16°·y`), the web analogue of
highlights that react to device motion. The hovered surface gets `--mx/--my/--hot` (one rAF per pointer move, no loop).

### 2.2 Cheap glass — `.glass-lite`

`backdrop-filter: blur(18px) saturate(1.7) brightness(1.04)` + the same rim and glow at lower intensity. Used for job
cards (any number), the engine hover card and the select menu.

### 2.3 Rules

- At most ~12 refracting surfaces at once. Steady state: 4 (chip, gear, input capsule, queue header); plus toasts
  (max 3), the clipboard pill, one sheet or popover, and one droplet while a control is pressed.
- No glass on glass: controls inside the sheet are fills; the segmented droplet and switch thumbs become lenses only while
  pressed (they refract the labels / track beneath them, because a lens is a backdrop root).
- Backdrop roots: never put `opacity < 1`, `filter`, `mask` or `clip-path` on an ancestor of a lens or of a glass-lite
  card, or the glass loses the scene behind it. Entrance animations therefore fade only surfaces that are lenses
  themselves and only transform containers. The queue scroller has no mask for the same reason (cards scroll under
  the queue header lens instead of fading out).
- Tint only the primary action (`Download`, `Sign in to Instagram`, `Update now`, `Add`): accent gradient.
- Legibility: white ink with alpha steps, a 1 px text shadow on glass, darker wells (`--well`) under inputs and long text.
- `prefers-reduced-transparency`: near-opaque tints. `prefers-contrast: more`: solid rim, no blend.

## 3. Design tokens — `css/tokens.css`

- Type: `Segoe UI Variable Display` (titles), `Segoe UI Variable Text` (body), `Cascadia Mono` (links, codes,
  versions). Sizes 11–26 px; titles 650 weight. SF Pro is not bundled (license).
- Ink: `--ink-1..4` = white at 0.96 / 0.74 / 0.54 / 0.34. Fills `--fill-1..4` = white at 0.05–0.2. Well
  `rgba(4,6,16,.32)`.
- Identity "lagoon": accent `#5d8dff`, accent-2 `#8a6cff`, gradient `#48a2ff -> #6a7bff -> #9c62ff`; mint `#3fe2c1`
  (success, done), amber `#ffc457` (duplicates, "already saved"), rose `#ff6482` (errors). Deliberately not the
  pink-orange gradient of Instagram; no Instagram logo or marks anywhere.
- Radii (concentric, inner = outer - padding): 6, 10, 14, 18, 22, 28, 34, pill.
- Spacing: 4 pt grid (`--s-1..8`).
- Shadows: `--shadow-s/m/l`, deeper under larger surfaces.

## 4. Motion — `js/motion.js`

One spring solver (semi-implicit Euler, 240 Hz sub-steps), parameters SwiftUI-style: `response` (s) and `damping`
fraction; `k = (2π/response)²`, `c = 4π·damping/response`. One shared rAF ticker runs only while a spring is moving and
stops when the document is hidden (springs jump to their targets). Compositor animations use WAAPI with a CSS `linear()`
easing sampled from the same spring (`springEasing`, `springAnimate`). `whenDone` guarantees DOM clean-up even when a
hidden window pauses the animation timeline.

| spring | response | damping | used for |
|---|---|---|---|
| snappy | 0.30 | 0.80 | lift of droplets, toggles |
| bouncy | 0.42 | 0.62 | droplet travel, toasts, card insert, button width |
| jelly | 0.38 | 0.42 | release wobble of a press, counters bump |
| press | 0.20 | 0.75 | squish while the pointer is down |
| smooth | 0.50 | 1.00 | progress bars, layout collapse |
| sheet / sheetClose | 0.52 / 0.34 | 0.80 / 1.00 | sheets and popovers |
| materialize | 0.45 | 0.92 | lensing strength in / out |

Behaviours:
- Gel press (`gel(el)`): squish `scale(1+0.10k, 1-0.07k)` (k larger for small controls), follows the pointer up to 7 px
  while pressed, release with an inverse velocity kick so the overshoot reads as jelly; glow `--hot` 1.8 while pressed.
  Uses the individual `scale` / `translate` properties so it composes with layout transforms.
- Segmented droplet: x and width springs; stretch `1 + |v|/2400` along the motion, thinner across it; lifts (scale +
  lensing) while pressed or dragged, can be dragged across options and snaps to the one under its centre.
- Switch: thumb grows to a 40 px droplet while pressed and turns into a lens over the track, travels with `bouncy`.
- Sheets / popovers (`Overlay`): clip-path inset springs from the origin control's rect and radius to the full rect,
  overshoot becomes a slight stretch, content fades in after 35 %, lensing ramps up, scrim follows. Esc / outside click
  closes back into the origin; focus returns there.
- `Download` morph: full-width pill -> 52 px circle with spinner while `enqueue` runs -> progress pill with liquid fill
  (sheen + meniscus) and "Downloading N of M · speed" -> mint "Done" -> back. Width by spring.
- Link chips: each chip appears showing the original link, then its text width springs to the clean `reel/<code>` form
  while the original blurs away; invalid chips shake once. After `Download` the chips fly out toward the queue.
- Queue: new cards drop in with a spring and the visible ones FLIP down; removed cards shrink, then the gap collapses.
- Toasts: drop from the top with scale and lensing ramp, FLIP-stacked, max 3.
- `prefers-reduced-motion`: springs become critically damped and short, background drift stops, no elastic wobble.

## 5. Background — `js/background.js`

Dark base, six radial colour fields (cobalt, violet, teal, rose, amber, sky) drifting on 46–76 s CSS loops, faint
topographic contour lines (crisp lines are what makes the lensing visible), up to four recent job thumbnails as soft
ghosts (downscaled to 18×32 and upscaled by the compositor = cheap blur), vignette and a static grain tile. All motion
is CSS on transforms. Drift pauses when the window is unfocused, the document hidden, after 60 s without input, or with
reduced motion; `html.is-still` pauses the decorative CSS loops inside the app as well.

## 6. Components and files

| File | Content |
|---|---|
| `index.html` | Static layout: title bar, composer, queue, overlay hosts. |
| `css/tokens.css`, `base.css`, `glass.css`, `components.css`, `app.css` | Tokens; reset + scene + buttons; material; components; screen layout. |
| `js/main.js` | Boot, title bar, window buttons, entrance, auth -> auto retry of `login_required` jobs. |
| `js/api.js` | `window.__reelsEvent`, `pywebviewready` wait (1.5 s), mock fallback, `call()` / `on()`. |
| `js/mock-api.js` | Full mock of BRIDGE_API (see header for URL flags `mock, demo, jobs, clip, fast, offline`). |
| `js/motion.js`, `js/glass.js`, `js/background.js` | Springs, material, scene. |
| `js/engine.js` | Engine status chip (dot / ring progress), hover card with versions, popover with components and `update_engine`. |
| `js/composer.js` | Input capsule, live clean preview, counters, tools, Ctrl+V anywhere, drag and drop (text, URLs, `.txt`), Ctrl+Enter, `Download` morph. |
| `js/clipboard.js` | Clipboard prompt pill on start and on `window` focus when `clipboard_watch`. |
| `js/queue.js` | Queue header, job cards, actions by status, error row with login, empty state art. |
| `js/settings.js`, `js/controls.js`, `js/overlay.js` | Settings sheet, Segmented / Switch / Stepper / Select, sheet & popover morph. |
| `js/toast.js`, `js/icons.js`, `js/format.js` | Toasts, original line icons, en-US plurals / sizes / ETA / relative time. |
| `assets/icon.svg`, `assets/icon-1024.png` | App icon: glass droplet with a down arrow (PNG rendered from the SVG with headless Edge). |

Keyboard: Ctrl+Enter downloads, Ctrl+V anywhere pastes into the capsule, Esc closes the top overlay, arrows move the
segmented selection, visible `:focus-visible` rings everywhere.

## 7. Runtime notes

- Served by the shell from `http://127.0.0.1:<port>/` (ES modules do not load from `file://`). Dev preview:
  `.claude/launch.json` -> `python -m http.server 5173 --directory ui`, open `/?mock=1` (`&demo=1` for every state,
  `&jobs=150` for the long list).
- Drag region: `.tb-drag` and the app name carry `pywebview-drag-region`; buttons are siblings, not children, because
  pywebview also drags from descendants of a drag element. Double-click on the drag area toggles maximize.
- Every lens owns one `<filter>` element; destroy lenses (`lens.destroy()`) when removing their element (toasts do).

## 9. Copy and locale rules

- The UI is English only (US). Buttons, labels and titles use sentence case: "Download", "Paste", "Clean links",
  "Copy clean links", "Sign in to Instagram", "Clear finished", "Open folder", "Update now".
- Plurals go through `plural(n, [singular, plural])` in `js/format.js` ("1 link" / "2 links"); never hard-code a form.
- Formats are en-US: decimal point, units B / KB / MB / GB and MB/s, "fps", relative time "just now", "5 min ago",
  "2 h ago", "yesterday", dates like "Oct 20". No `ru` / `ru-RU` locale arguments anywhere.
- Strings the core emits (stage text, error messages) are shown as received; the mock mirrors them: "Queued",
  "Fetching info", "Downloading video", "Merging tracks", "Converting to H.264", "Already downloaded", "Done".
- The product name is "Reels Saver". Only "Instagram" names the service; do not abbreviate it.
