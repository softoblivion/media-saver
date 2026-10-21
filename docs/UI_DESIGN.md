# UI design — Liquid Glass for Media Saver

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
| `sheet` | settings sheet, engine popover, update popover | 30 | 40 | 14 | 1.5 | brightness 0.92 |
| `droplet` | segmented indicator (mode switch, format toggle, quality, settings), switch thumbs (only while pressed) | s/2 | 0.9 s | 0 | 1.4 | brightness 1.12 |

Strength `0..1` (`lens.setStrength`, `lens.animateStrength`) scales displacement, frost and saturation together; 0 sets
`backdrop-filter: none`. Materialize in / out = strength spring, never an opacity fade of a parent.

CSS layers inside a lens (bottom -> top): refracted backdrop, tint (`--lens-tint` background), press tint
(`.lens::before`, z-index -1, uniform `--press-tint` white 7.5 %, opacity 0 -> 1 while `.is-pressed`), content,
specular rim (`.lg-rim`, 1.25 px ring via mask-composite, conic gradient with a lit lobe at the light angle `--la` and a
weaker counter-reflection at +180°, `mix-blend-mode: plus-lighter`). Shadow: `--lens-shadow` (deeper for larger
surfaces) plus an inner bloom.

Light: `--la` on `:root` drifts with the pointer across the window (`-48° + 40°·x - 16°·y`), the web analogue of
highlights that react to device motion; it only turns the rim lobes. There is no per-surface hover light.

Removed in 2.0: the pointer-following glow (`.lg-glow`, `--mx/--my/--hot`, `--glow-r`, the `.hl` class and the
radial highlight on `Download`) and the pointer follow of the gel press. Nothing on a surface follows the pointer; a
press squishes the surface and lightens its tint evenly (`Download` uses the same rule on its `::before`).

### 2.2 Cheap glass — `.glass-lite`

`backdrop-filter: blur(18px) saturate(1.7) brightness(1.04)` + the same rim at lower intensity. Used for job cards (any
number), the engine hover card and the select menu.

### 2.3 Rules

- At most ~12 refracting surfaces at once. Steady state: 4 (chip, gear, input capsule, queue header); plus toasts
  (max 3), the clipboard pill, one sheet or popover, and one droplet while a control is pressed.
- No glass on glass: controls inside the sheet are fills; the segmented droplet and switch thumbs become lenses only while
  pressed (they refract the labels / track beneath them, because a lens is a backdrop root).
- Backdrop roots: never put `opacity < 1`, `filter`, `mask` or `clip-path` on an ancestor of a lens or of a glass-lite
  card, or the glass loses the scene behind it. Entrance animations therefore fade only surfaces that are lenses
  themselves and only transform containers. The queue scroller has no mask for the same reason (cards scroll under
  the queue header lens instead of fading out).
- Tint only the primary action (`Download`, `Sign in to Instagram`, `Update now`, `Add`, the update panel's
  `Download` / `Restart to update`): accent gradient.
- Repeated controls inside cards (the per-item MP4 / MP3 toggle) are plain fills with a CSS thumb, never lenses.
- Legibility: white ink with alpha steps, a 1 px text shadow on glass, darker wells (`--well`) under inputs and long text.
- `prefers-reduced-transparency`: near-opaque tints. `prefers-contrast: more`: solid rim, no blend.

## 3. Design tokens — `css/tokens.css`

- Fonts, embedded in `ui/assets/fonts` (no network at runtime; `@font-face` in `tokens.css`, `font-display: block`,
  preloaded from `index.html`):

  | File | Face | Version, source | Licence | Size |
  |---|---|---|---|---|
  | `InterVariable.woff2` | Inter, variable `wght` 100–900 + `opsz` 14–32, upright only | 4.1, official release zip of github.com/rsms/inter (`web/InterVariable.woff2`) | SIL OFL 1.1, `LICENSE-Inter.txt` | 352 240 B |
  | `GeistMono-Variable.woff2` | Geist Mono, variable `wght` 100–900 | 1.8.1, official release zip of github.com/vercel/geist-font (`GeistMono/webfonts/GeistMono[wght].woff2`) | SIL OFL 1.1, `LICENSE-GeistMono.txt` | 71 236 B |

  Geist Mono instead of JetBrains Mono: its release ships a variable woff2 (JetBrains Mono 2.304 has only static
  woff2 or a variable TTF) and its proportions sit closer to Inter. No italics are used, so no italic file is shipped;
  `font-synthesis: none` keeps the browser from faking one. Stacks: `--font-text` / `--font-display` = InterVariable,
  `system-ui`; `--font-mono` = Geist Mono, `ui-monospace`, Cascadia Mono, Consolas. Segoe UI is no longer primary.
- Optical size: `font-optical-sizing: auto`, so Inter picks its display cut for titles by itself.
- Features: Inter `cv05` (lowercase l with a tail: I / l / 1 stay apart in names and titles) and `ss03` (round
  quotes and commas); mono: `liga 0, calt 0`. The `font` shorthand resets `font-feature-settings`, so the features
  travel as the inherited custom property `--ff-feat` (default `--ff-text`; mono elements set `--ff-feat:
  var(--ff-mono)`) and one rule in `base.css` (`* { font-feature-settings: var(--ff-feat) !important }`) applies them.
- Type roles (`--t-*` = weight size / line-height family, `--tr-*` = tracking from Inter's dynamic metrics,
  `-0.0223 + 0.185·e^(-0.1745·size)` em). Use a role, never a raw size:

  | Role | Spec | Tracking | Used for |
  |---|---|---|---|
  | `--t-display` | 650 24/1.15 | -0.019em | sheet titles |
  | `--t-title` | 630 20/1.2 | -0.017em | composer, queue, empty state titles |
  | `--t-headline` | 630 15/1.3 | -0.009em | popover titles, the `Download` button |
  | `--t-card` | 560 14/1.3 | -0.006em | job card titles, setting labels, component names |
  | `--t-body` | 450 13.5/1.45 | -0.005em | body, the links input, toasts, empty text |
  | `--t-callout` | 450 13/1.4 | -0.003em | secondary lines, popover sub-lines, error text, release notes |
  | `--t-control` / `--t-control-s` | 560 13 / 12.5 | -0.003em | buttons, segmented options |
  | `--t-meta` | 470 12/1.35 | 0 | sizes, speeds, ETA, times, counters (with `tabular-nums`) |
  | `--t-caption` | 500 11.5/1.3 | +0.003em | group labels (uppercase +0.06em), footnotes |
  | `--t-micro` | 560 10.5/1.4 | +0.01em | badges, tags, kbd |
  | `--t-mono` / `-s` / `-xs` | 450 12.5 / 450 12 / 500 11 | 0 | paths and templates / link chips, versions / the version label |

  Every number that changes in place (sizes, speeds, ETA, counters, durations, percentages) sets
  `font-variant-numeric: tabular-nums` after its `font` shorthand (the shorthand resets it). Canonical links, video
  IDs, paths, versions and codes are mono.
- Legibility on glass is unchanged: ink alphas, `--ink-shadow` (1 px, 30 % black) on lenses.
- Ink: `--ink-1..4` = white at 0.96 / 0.74 / 0.54 / 0.36. Fills `--fill-1..4` = white at 0.05–0.2. Well
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
- Gel press (`gel(el)`): squish `scale(1+0.10k, 1-0.07k)` (k larger for small controls), release with an inverse
  velocity kick so the overshoot reads as jelly; the tint lightens while `.is-pressed`. The surface stays in place
  (no pointer follow). Uses the individual `scale` property so it composes with layout transforms.
- Segmented droplet: x and width springs; stretch `1 + |v|/2400` along the motion, thinner across it; lifts (scale +
  lensing) while pressed or dragged, can be dragged across options and snaps to the one under its centre.
- Switch: thumb grows to a 40 px droplet while pressed and turns into a lens over the track, travels with `bouncy`.
- Stepper (`Stepper` in `js/controls.js`, Settings "Simultaneous downloads"): the number rolls through a 30 px window
  (`.step-val`: `overflow: hidden`, `contain: paint`, a mask that softens the top and bottom 24 %). The roll is a WAAPI
  pair (outgoing up and out, faded by half the travel; incoming in from below, or the reverse for a decrease) with a
  `linear()` easing from a critically damped spring (`response 0.26`, `damping 1`, about 0.4 s): no overshoot, so the
  old digit never swings back into view. At most one outgoing and one incoming `.step-num` exist: a new step first
  removes the previous outgoing span, cancels the running animations and lets the previous incoming span leave from
  its current position and opacity; `whenDone` removes the outgoing span. At rest one span remains, with no inline
  transform or opacity, in `tabular-nums`. Reduced motion swaps the text without travel. Keys on either button:
  arrows and +/- step, Home / End jump to the limits; a button that turns disabled under focus hands focus to the
  other one. (Earlier builds: the roll used a `bouncy` spring and picked the first span in the window as the outgoing
  one, so a second click before rest left the previous incoming digit on screen for good: the doubled, smeared
  number.) Settings saves of the stepper ignore older echoes while a save of the same key is in flight (section 7).
- Sheets / popovers (`Overlay`): clip-path inset springs from the origin control's rect and radius to the full rect,
  overshoot becomes a slight stretch, content fades in after 35 %, lensing ramps up, scrim follows. Esc / outside click
  closes back into the origin; focus returns there.
- `Download` morph: full-width pill -> 52 px circle with spinner while `enqueue` runs -> progress pill with a liquid
  level and "Downloading N of M · speed" -> mint "Done" -> back. Width by spring.
  Liquid level (reworked in 2.0 after the first build showed a hard bright bar with a halo at the edge): `.pm-fill` is
  a static clip with the button's own pill shape (`overflow: hidden`, `border-radius: inherit`) and never moves;
  `.pm-liquid` inside it is wider than the button by its soft edge (`--pm-edge`, 56 px), masked from opaque to clear
  over that edge, and moves by `transform: translateX(calc((var(--p) - 1) * 100%))` only (0.9 s ease-out, no
  overshoot). So the whole edge sits left of the button at 0, the edge's middle is at the centre at 50 % and the
  opaque body is flush with the right end at 100 %; there is no meniscus element and nothing renders outside the clip.
  A faint sheen (white 13 %, `pm-sheen`, 3.6 s) drifts through the filled part only; its animation is declared only
  under `[data-state="progress"]`, so no loop runs in any other state (and none with reduced motion). The button's
  top streak fades out in the progress state (it read as a second pill over the empty track). Entering the progress
  state sets the level without travel (`.pm-instant` for one style flush), so a new batch never drains from the last
  batch's value. Below 380 px of action-row width the speed / percentage moves under the text (two lines).
- Link chips: each chip appears showing the original link, then its text width springs to the clean short form
  (`reel/<code>`, `watch?v=<id>`, `shorts/<id>`) while the original blurs away; invalid chips shake once; chips of the
  other platform appear without morph or shake. After `Download` the chips fly out toward the queue. A mode switch
  rebuilds the chips (the text is cleaned again for the new platform).
- Mode switch: cards of the other platform get `[hidden]` (no rebuild, no refetch; 150 cards switch in ~3 ms), the
  first 8 cards of the new mode settle in with a 28 ms stagger.
- Preview: a pending card (queued, `previewed` false) shows a skeleton bar for the title and a sheen over the
  thumbnail; both are CSS loops bound to `.is-pending`, which drops when `previewed` turns true even if every field
  stayed null. The title then fades in from a 3 px blur.
- Queue: new cards drop in with a spring and the visible ones FLIP down; removed cards shrink, then the gap collapses.
- Toasts: drop from the top with scale and lensing ramp, FLIP-stacked, max 3.
- `prefers-reduced-motion`: springs become critically damped and short, background drift stops, no elastic wobble;
  the skeleton, sheens, hint bars, empty-state art and the version dot pulse hold still; the mode switch skips the
  card stagger.

## 5. Background — `js/background.js`

Dark base, six radial colour fields (cobalt, violet, teal, rose, amber, sky) drifting on 46–76 s CSS loops, faint
topographic contour lines (crisp lines are what makes the lensing visible), up to four recent job thumbnails as soft
ghosts (downscaled to 18×32 and upscaled by the compositor = cheap blur), vignette and a static grain tile. All motion
is CSS on transforms. Drift pauses when the window is unfocused, the document hidden, after 60 s without input, or with
reduced motion; `html.is-still` pauses the decorative CSS loops inside the app as well.

## 6. Components and files

| File | Content |
|---|---|
| `index.html` | Static layout: title bar (brand, mode slot, version label, engine chip, gear, window buttons), composer, queue, overlay hosts (engine popover, update popover, settings sheet). |
| `css/tokens.css`, `base.css`, `glass.css`, `components.css`, `app.css` | Fonts, type roles and tokens; reset + feature rule + scene + buttons; material; components; screen layout. |
| `assets/fonts/` | `InterVariable.woff2`, `GeistMono-Variable.woff2`, `LICENSE-Inter.txt`, `LICENSE-GeistMono.txt` (section 3). |
| `js/main.js` | Boot, title bar, mode switch, window buttons, entrance, auth -> auto retry of Instagram `login_required` jobs. |
| `js/mode.js` | Current mode, default format and per-platform quality (`settings.mode`, `format`, `quality_instagram`, `quality_youtube`): local apply at once, `set_settings` persist, revert on refusal, stale-echo guard, `onPref(key)` subscribers, `html[data-mode]`, `html[data-format]`. |
| `js/quality.js` | Quality control of the composer's spec row: one `Segmented` per platform (only the mode's one visible), hint line, disabled in MP3, the H.264 note on YouTube Max. |
| `js/api.js` | `window.__msEvent`, `pywebviewready` wait (1.5 s), mock fallback, `call()` / `on()`. |
| `js/mock-api.js` | Full mock of BRIDGE_API (section 7 lists the URL flags). |
| `js/motion.js`, `js/glass.js`, `js/background.js` | Springs, material, scene. |
| `js/engine.js` | Engine status chip (dot / ring progress), hover card with versions, popover with components and `update_engine`. |
| `js/update.js` | Version label, the update popover (`check_updates`, `download_update`, `apply_update`, event `update`), the one "available" toast, safe Markdown for release notes. |
| `js/composer.js` | Input capsule, live clean preview for the mode, other-platform chips with "Switch to ...", counters, tools, Ctrl+V anywhere, drag and drop (text, URLs, `.txt`), Ctrl+Enter, format toggle, `Download` morph with the liquid level (one batch per platform). |
| `js/clipboard.js` | Clipboard prompt pill on start and on `window` focus when `clipboard_watch`; offers the mode's links, or the other platform's links with "Add to <platform>". |
| `js/queue.js` | Queue header (counts of the mode, "Clear finished", the "Open folder" split button with its folder menu, `open_output_dir` / `choose_output_dir`, the "Videos now go to ..." toast on an `output_dir` change), job cards of every platform (others `[hidden]`), preview skeleton, per-item format toggle, actions by status, error row (sign-in for Instagram only), empty state per mode, `clear_finished(mode)`. |
| `js/settings.js`, `js/controls.js`, `js/overlay.js` | Settings sheet (no quality row: quality lives in the main window), Segmented (with `setDisabled`, `setOptionTitle`) / Switch / Stepper / Select / `formatToggleHtml`, sheet & popover morph. |
| `js/toast.js`, `js/icons.js`, `js/format.js` | Toasts, original line icons (incl. `video`, `shorts`, `audio`, `swap`, `app`, `folderEdit`), en-US plurals / item nouns / short links / sizes / ETA / relative time / link error notes. |
| `assets/icon.svg`, `assets/icon-1024.png` | App icon: glass droplet with a down arrow (PNG rendered from the SVG with headless Edge). |

### 6.1 Placement decisions

- Mode switch (Instagram / YouTube): a `Segmented` (`seg-tb` variant, 34 px) in the title bar right after the app
  name. The mode scopes the whole window (composer, queue, clipboard prompt, settings), so it sits above both columns
  rather than inside the composer; it reads as the app's top-level navigation, and on the left it never collides with
  the right cluster at the minimum width of 940 px. It is a sibling of the drag region. Alt+1 / Alt+2 switch modes.
- Format toggle (MP4 / MP3): a `Segmented` (`seg-l` variant, 52 px tall, matching the primary button) at the left of
  the composer's action row; `Download` takes the rest of the row (`.cmp-btn-slot`), so its morph measures the slot.
  Below 380 px of row width the `Ctrl+Enter` hint hides.
- Quality (per platform, Settings `quality_instagram` / `quality_youtube`): a spec row (`#cmp-spec`, 34 px) directly
  above the action row: caption "QUALITY", a compact `Segmented` (`seg-q` variant, 28 px options) and a hint line at
  the right. With the format toggle and `Download` below it, it reads as "what you get" right before the press. Not in
  the composer header: the counters live there and can show four pills (ready, invalid, duplicates, other), which
  would collide with a three-option control at the 940 px minimum (composer content 332 px wide). At 940 x 640 the row
  fits the longest hint ("Best, up to 4K") in full; below 290 px of row width the hint hides (the option tooltips carry
  the same text). One `Segmented` per platform, only the current mode's one visible: each keeps its own value, and a
  mode switch swaps options and value with a small transform-only settle (no opacity: the droplet is a lens). Saved
  through `js/mode.js` like the format (optimistic, reverted with a toast when refused, stale echoes ignored). In MP3
  the control stays in place, dimmed (`.seg.is-disabled`: opacity 0.42, out of the tab order, no pointer), with the
  title "MP4 only" and the hint "MP4 only", so the layout never jumps. The settings sheet has no quality row; "H.264
  for editing" stays there.

  | Platform | Options (value: label) | Hint | Tooltip |
  |---|---|---|---|
  | Instagram | `low`: Low, `medium`: Medium, `max`: Max | "≤ 480p", "≤ 720p", "Best" | "Low: up to 480p, the smallest files", "Medium: up to 720p", "Max: the best the Reel has" |
  | YouTube | `360`: 360p, `720`: 720p, `max`: Max | "≤ 360p", "≤ 720p", "Best, up to 4K" | "360p: up to 360p, the smallest files", "720p: up to 720p", "Max: the best the video has, up to 4K", plus, while H.264 for editing is on, ". With H.264 for editing on, 1440p and 4K are converted to H.264, which takes minutes" |
- Queue header row (`#q-head`, a capsule lens): title "Queue" and the counts of the mode on the left; on the right
  the ghost button "Clear finished" and the downloads folder split button (`#folder-split`): the main half "Open
  folder" (`open_output_dir()`, `title` = the current `output_dir`) and a 28 px chevron half ("Downloads folder
  options", `aria-haspopup="menu"`, a hairline between the halves; the pill squishes as one gel). The chevron opens
  `#folder-menu` (`.split-menu glass-lite`, 272 px, right-aligned 8 px under the header, transform-only drop-in): a
  caption "Saving to" with the full path in mono (a longer path loses its start, not the folder name), then two items
  "Open folder" and "Change folder…" (`choose_output_dir()`, the native dialog; the backend saves and emits
  `settings`). Arrow keys move between items, ArrowDown / ArrowUp on the chevron open the menu, Esc closes it and
  returns focus to the chevron, Tab or an outside click closes it. A split button rather than a second icon button:
  one control keeps the "where do my videos go" job in one place, the main action stays one click, and below 640 px
  of queue width (always the case at the 940 px minimum window) the buttons drop their labels, so the header gains
  only 24 px (34 + 32 + 24 px of actions next to the counts). The Settings row "Downloads folder" (path + "Change…")
  stays. Whatever route changes `output_dir`, the `settings` event updates the title, the menu caption and the
  Settings row, and `js/queue.js` shows one toast "Videos now go to <folder name>"; the initial state never toasts.
- Per-item format: queued cards show a compact fill toggle (`.fmt-toggle`, 22 px, CSS thumb, radio semantics, arrow
  keys) in the badge row that calls `set_job_format(id, format)` optimistically and reverts with a toast when the
  backend answers `null`; once the job leaves `queued` it becomes a static badge (`MP4` neutral, `MP3` violet).
- Version label: a quiet mono text button `v2.0.0` left of the engine chip, so the two status controls of the app
  (engine, app) sit together and open popovers of the same family. Stages `available` / `downloading` / `ready` add a
  dot (accent; pulsing while downloading; mint when ready) and accent ink; errors of a background check stay quiet.
- Update panel: a sibling popover of the engine popover (`#update-pop`, `pop-wide` 384 px) that grows out of the
  version label, with the same anatomy: title "Media Saver", one sub-line, progress bar, a well with rows
  (Installed / Latest with size and date tags), release notes in a scrollable well, footer with "Checked <time>" and one
  action. A separate popover keeps the engine popover (which the user likes as it is) unchanged and gives the notes
  room. The settings "Check for updates" button folds the sheet back into the gear and opens this panel, starting a
  check when the stage allows one.

### 6.2 Update panel states (UpdateStatus.stage)

| Stage | Sub-line | Body | Action |
|---|---|---|---|
| `idle` | message or "Updates come from GitHub Releases" | Installed row | Check for updates |
| `checking` | message or "Checking for updates…" | indeterminate bar | Checking… (disabled) |
| `up_to_date` | message or "You have the latest version" | Installed row | Check for updates |
| `available` | "Version X is available" | Installed + Latest rows, release notes | Download (tinted) |
| `downloading` | "Downloading version X…" | determinate bar, "42 %" and "5.1 MB of 12.1 MB", rows, notes | Downloading… (disabled) |
| `ready` | "Version X is ready to install" | rows (Latest tagged "downloaded"), notes | Restart to update (tinted; disabled with the message as tooltip when `can_apply` is false; with active downloads a warning toast offers "Restart anyway") |
| `error` | "Could not check for updates" / "The download did not finish" | the message in a rose box | Try again (repeats the interrupted step) |

Progress ticks only move the bar and the numbers, so the notes keep their scroll position. The first `available` of a
session (initial state or event) raises one toast "Media Saver X.Y.Z is available" with a "View" action.

Release notes Markdown: everything is HTML-escaped first; then `#` headings (level 1 as a title, others as small caps
labels), `-` / `*` / `+` bullets with continuation lines, blank-line paragraphs, `**bold**`, `` `code` `` and
`[text](https://...)` links opened through `open_url`. Nothing else is interpreted.

Keyboard: Ctrl+Enter downloads, Ctrl+V anywhere pastes into the capsule, Esc closes the top overlay, arrows move the
segmented selection (mode, format, quality, settings) and the per-item format toggle, Alt+1 / Alt+2 switch modes, visible `:focus-visible` rings
everywhere.

## 7. Runtime notes

- Served by the shell from `http://127.0.0.1:<port>/` (ES modules do not load from `file://`). Dev preview:
  `python -m http.server 5173 --bind 127.0.0.1 --directory ui`, open `/?mock=1`. Mock URL flags (header of
  `js/mock-api.js`): `demo=1` (every state on both platforms, MP3 jobs, queued jobs with pending previews),
  `jobs=N` (N finished jobs, every third YouTube), `mode=youtube`, `format=mp3`, `update=available|uptodate|error`
  (launch check result; `error` fails the first check and finds 2.1.0 on "Try again"), `hold=1` (queued jobs never
  start: previews and format toggles stay inspectable), `clip=0`, `fast=1`, `offline=1`.
- The page makes no network request: fonts, icons and images are local or `data:` URLs (checked in the browser's
  network log with the mock).
- Screenshots with a hidden or background browser pane: WAAPI animations pause there, so entrance and fade-ins hold
  their first frame; finish them first (`document.getAnimations().forEach(a => a.finish())` for the finite ones).
- Drag region: `.tb-drag` and the app name carry `pywebview-drag-region`; buttons are siblings, not children, because
  pywebview also drags from descendants of a drag element. Double-click on the drag area toggles maximize.
- Every lens owns one `<filter>` element; destroy lenses (`lens.destroy()`) when removing their element (toasts do).
- `mode`, `format`, `quality_instagram` and `quality_youtube` are written by `js/mode.js` only; while its own
  `set_settings` is in flight it ignores the `settings` echo for that key, so fast toggling never flips back.
  `js/settings.js` applies the same guard to its own controls (switches, segmented controls, the stepper, the
  browser select): while a save of a key is in flight, neither its answer nor a `settings` event moves that control;
  the answer of the last save sets it. The mock
  validates the quality values and picks the simulated resolution from the job's platform setting at start (Instagram
  480 / 720 / 1080; YouTube 360 / 720 / Max = 2160p for videos, which then always takes the H.264 pass while it is on,
  1080p for Shorts).
- A job without `platform` counts as Instagram, and a job without `previewed` counts as previewed (older payloads
  never show an endless skeleton).

## 9. Copy and locale rules

- The UI is English only (US). Buttons, labels and titles use sentence case: "Download", "Paste", "Clean links",
  "Copy clean links", "Sign in to Instagram", "Clear finished", "Open folder", "Change folder…", "Update now", "Check for updates",
  "Restart to update", "Try again". Platform and format names keep their own case: Instagram, YouTube, Reels, Shorts,
  MP4, MP3, H.264, FFmpeg.
- Plurals go through `plural(n, [singular, plural])` in `js/format.js` ("1 link" / "2 links"); never hard-code a
  form. Item nouns go through `itemNoun(n, platform)` / `nItems(n, platform, format)`: Instagram "Reel" / "Reels",
  YouTube "video" / "videos", and " as MP3" appended for MP3 ("Queued 3 Reels", "Queued 2 videos as MP3").
- Formats are en-US: decimal point, units B / KB / MB / GB and MB/s, "fps", relative time "just now", "5 min ago",
  "2 h ago", "yesterday", dates like "Oct 20" / "Oct 18, 2026". No `ru` / `ru-RU` locale arguments anywhere.
- Strings the core emits (stage text, error messages, UpdateStatus.message) are shown as received; the mock mirrors
  them: "Queued", "Fetching info", "Downloading video", "Downloading audio", "Merging tracks", "Converting to H.264",
  "Converting to MP3", "Already downloaded", "Done". A YouTube `login_required` message comes from the core; the UI
  adds no sign-in button for YouTube jobs.
- The product name is "Media Saver" everywhere (title bar, update panel, toasts, About). Only "Instagram" and
  "YouTube" name the services; do not abbreviate them. No Instagram or YouTube logos or marks.
- Mode-aware strings (the current mode decides; `composer.js` COPY, `queue.js` EMPTY_COPY):

  | Place | Instagram | YouTube |
  |---|---|---|
  | Input placeholder | "Paste Reels links, one by one or a whole batch, with any junk around them. Ctrl+V works anywhere in the window." | "Paste YouTube links to videos or Shorts, one by one or a whole batch, with any junk around them. Ctrl+V works anywhere in the window." |
  | Preview hint | "... just `instagram.com/reel/code`, no tails like `?igsh=`" | "... just `youtube.com/watch?v=id` or `/shorts/id`, no tails like `&t=` or `?si=`" |
  | Ready button | "Download Reel" / "Download 3 Reels" | "Download video" / "Download 3 videos" |
  | Ready button, MP3 | "Download as MP3" / "Download 3 as MP3" | same |
  | Nothing pasted | "Paste Reels links. Ctrl+V works anywhere in the window" | "Paste YouTube links. Ctrl+V works anywhere in the window" |
  | No usable link | "No Instagram Reels or post links found here" | "No YouTube video or Shorts links found here" |
  | Other-platform links | chip action "Switch to YouTube"; counter "N YouTube"; toast "These are YouTube links" + "Switch to YouTube" | the same with Instagram |
  | Chip notes | "not a Reel or post", "no video code", "not supported" | "not a video", "no video ID", "not supported" |
  | Empty queue | "No Reels yet" + paste hint | "No YouTube videos yet" + paste hint |
  | Error row | "Sign in to Instagram" / "Retry signed in" when `can_login` | the core's message and "Log" only |
  | Settings | "Instagram" group shown | "Instagram" group hidden |
  | Quality control | Low / Medium / Max, hints "≤ 480p" / "≤ 720p" / "Best" | 360p / 720p / Max, hints "≤ 360p" / "≤ 720p" / "Best, up to 4K" |
  | Empty queue text | "... as MP4 or MP3, in the quality you pick above Download." | the same |
  | Clipboard pill | "N links on the clipboard" + "Add" | the same; links of the other platform only: "N Instagram links on the clipboard" + "Add to Instagram" |

- Update copy: toast "Media Saver X.Y.Z is available" (action "View"); About line "Downloads Instagram Reels and
  YouTube videos as MP4 or MP3"; switch "Check for updates on launch".
- The runtime data folder is `media-saver_data\` (never `data\`) wherever copy or mock paths name it (mock log paths:
  `D:\Apps\MediaSaver\media-saver_data\logs\jobs\<id>.log`). The default output dir is the core's decision (the
  user's Videos folder + `Media-Saver`, BRIDGE_API Settings); the UI only shows `Settings.output_dir`. The mock's
  default is `C:\Users\user\Videos\Media-Saver`; its `choose_output_dir` alternates between `D:\Media\Saved videos`
  and that default, so the toast, the "Open folder" title and the Settings row visibly change on every try.
- Folder copy: menu caption "Saving to" + the path; items "Open folder", "Change folder…"; toast after a change
  "Videos now go to <last path segment>" (a drive root shows as `E:\`); the chevron's label "Downloads folder
  options" and title "Choose where videos are saved".
