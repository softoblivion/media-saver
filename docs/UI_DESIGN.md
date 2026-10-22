# UI design — Liquid Glass for Media Saver

Owner: ui worker. Scope: everything under `ui/`. The JS <-> Python contract is `docs/BRIDGE_API.md`; this document
describes the material, the design system and the motion of the UI that codes against it.

## 0. Liquid UI kit and the app

The design system (tokens, fonts, the four themes, the glass material, the button system, the hero pill, the
motion template, overlays, form controls, toasts, the background scene, the version pill and update popover) is
the shared Liquid UI kit, vendored in `ui/vendor/liquid_ui/` (`VENDORED.txt` names the kit version; its reference
is the kit's own documentation, cited below as "the kit's documentation", sections 3 to 8). Media Saver builds its
screen on top of it and overrides none of its identity: the look of the window is a kit theme, Dusk by default,
switched in Settings > Appearance. The kit is design only: the
update process is the Python self-updater (`docs/SELF_UPDATE.md`); the kit's update control renders `UpdateStatus`
and calls the bridge (`docs/BRIDGE_API.md`, "Update UI").

Rules:
- `ui/vendor/liquid_ui/` is a synced copy: never edit it here. A change is made in the kit's master and synced into
  this tree (the whole folder is replaced and `VENDORED.txt` rewritten); after a sync, check the mock in the browser
  (`index.html?mock=1`, section 7) and the app run from source.
- `css/theme.css` holds layout tokens only (`--titlebar-h`, `--cmp-w`). Identity tokens (`--accent` family,
  scene colours, glass tints) come from the kit's themes and are never overridden; app CSS that needs the accent
  uses the kit's tokens (`--accent`, `--accent-ink`, `--btn-glow`, `--focus-ring`, through `color-mix()` for
  tints). A missing kit feature is added to the kit, or kept as app code beside it, never patched into the copy.
- Buttons are the kit's `.btn` / `.icon-btn` / `.hero` as they are: the app places them and never restyles their
  size, fill or font (the kit's documentation, section 6). The only geometry rules in app CSS are layout: the
  icon-only collapse of the queue header buttons below 640 px of queue width, the hidden icons of the composer
  tools below 380 px of row width, and the hero pill's width (100 % of its slot, no minimum, so it can narrow to a
  circle).
- Stylesheet order (`index.html`): the kit's `tokens.css`, `themes.css`, `base.css`, `scene.css`, `glass.css`,
  `components.css`, then `css/theme.css`, `css/parts.css`, `css/app.css`. Modules import the kit by relative path
  (`../vendor/liquid_ui/js/<module>.js`); no app file names a font file.

| Part | Kit (`ui/vendor/liquid_ui/`) | App |
|---|---|---|
| Tokens, fonts | `css/tokens.css`, `fonts/` (Unbounded, Geist Mono); the kit's documentation, section 3 | `css/theme.css`: layout tokens only (`--titlebar-h` 46 px, `--cmp-w`) |
| Themes | `css/themes.css` (Dusk, Nova, Frost, Bloom), `js/theme.js` (`applyTheme`, `mountThemeControl`); section 4 | `index.html`: `data-theme="dusk"` and a head script that reads `localStorage['media-saver.theme']` before the first paint; `js/main.js`: applies `Settings.theme` on connect and on every `settings` event and writes that key; `js/settings.js`: the theme row in the Appearance group, saved through `set_settings({ theme })` |
| Reset, buttons, focus, scrollbars, spinner | `css/base.css`: the button system (plate, sizes l / regular / s / xs, kinds plain / ghost / tinted / danger, states); section 6 | — (placement only, see the rules above) |
| Hero pill | `.hero` with the `.pm-*` layers in `css/components.css`; section 6.1 | `#btn-download` (`js/composer.js`): the markup, `new Lens(btn, 'pill')`, `gel(btn, { amount: 0.8 })`, the five states, the width spring to a circle while sending, the `--p` level; `css/app.css`: width, `--hero-h: 52px`, the two-line progress layout below 380 px |
| Material | `js/glass.js` (Lens, light, gel, shake), `css/glass.css` | — |
| Motion template | `js/motion.js` (springs, `--d-1..--d-4`, `--ease-out` / `--ease-spring`, `enterRow` / `leaveRow` / `fadeIn` / `fadeOut`); section 8 | queue cards enter and leave with `enterRow` / `leaveRow`, the folder menu with `fadeIn` / `fadeOut`; app transitions use the `--d-*` tokens; the chip morph and the fly-out stay app motion built on the kit's springs |
| Overlays | `js/overlay.js`, `.scrim` `.sheet` `.popover` | `.pop-wide` (384 px update popover), engine popover colours (`css/parts.css`) |
| Controls | `js/controls.js` (Segmented, Switch, Stepper, Select), their CSS | segmented variants `seg-tb` / `seg-l` / `seg-q`; the per-card format toggle (`js/queue.js`) |
| Toasts | `js/toast.js`, `.toast` | position over the queue column; under the update popover while it is open (`css/parts.css`) |
| Icons | `js/icons.js` (`icon`, `registerIcons`) | `js/icons.js`: Media Saver's drawings registered into the kit's set (same names replace the kit's, e.g. `app` = the droplet), `kindGlyph`, `appGlyph` |
| Background | `js/background.js` (the `deep` treatment drawn from the theme tokens, `retint()` on a theme change), `css/scene.css`; section 7 | `js/scene.js`: `initBackground(root, { idleMs: 60000 })` with no colours of its own, the thumbnail ghosts layer |
| Version pill, update popover, update toasts, "Updates" settings rows | `js/update.js` (`mountUpdateControl`, `mountSettings`), `.ver-pill` `.pop-*` `.upd-*` `.set-*` | `js/update.js`: the mount (product name, restart warning, active-jobs check, `settings` events) |
| Settings sheet frame | `.sheet-head` `.sheet-scroll` `.set-group` `.set-card` `.set-row`, the theme row `.theme-row` | content (`js/settings.js`); path, template tokens, engine versions, sign-in (`css/parts.css`) |
| Everything else | — | title bar layout, engine chip, composer, queue, cards, clipboard pill, mock, bridge wrapper (`js/*.js`, `css/app.css`, `css/parts.css`) |

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

### 2.1 Lens (refracting surface) — kit `js/glass.js`, `css/glass.css`

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
| `pill` | engine chip, gear, the `Download` hero pill (the one lens among the buttons) | s/2 | 0.75 s | 0.6 | 1.7 | brightness 1.08 |
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

- At most ~12 refracting surfaces at once. Steady state: 5 (version pill, chip, gear, input capsule, queue header); plus toasts
  (max 3), the clipboard pill, one sheet or popover, and one droplet while a control is pressed.
- No glass on glass: controls inside the sheet are fills; the segmented droplet and switch thumbs become lenses only while
  pressed (they refract the labels / track beneath them, because a lens is a backdrop root).
- Backdrop roots: never put `opacity < 1`, `filter`, `mask` or `clip-path` on an ancestor of a lens or of a glass-lite
  card, or the glass loses the scene behind it. Entrance animations therefore fade only surfaces that are lenses
  themselves and only transform containers. The queue scroller has no mask for the same reason (cards scroll under
  the queue header lens instead of fading out).
- Tint only the primary action (`Download` while links are ready, `Sign in to Instagram`, `Update now`, `Add`,
  the update panel's `Download` / `Restart to update`): the theme's accent gradient (`.btn-tinted`, `.hero` in
  `ready`).
- Repeated controls inside cards (the per-item MP4 / MP3 toggle) are plain fills with a CSS thumb, never lenses.
- Legibility: white ink with alpha steps, a 1 px text shadow on glass, darker wells (`--well`) under inputs and long text.
- `prefers-reduced-transparency`: near-opaque tints. `prefers-contrast: more`: solid rim, no blend.

## 3. Design tokens — kit `css/tokens.css`, `css/themes.css`; app `css/theme.css`

The tokens are the kit's (the kit's documentation, section 3: fonts, type roles, colour, buttons, motion, shape,
space, depth, layout); Media Saver uses them and defines none of its own except the two layout tokens in
`css/theme.css` (`--titlebar-h` 46 px, `--cmp-w`, the composer column). What matters for the app code:

- Fonts: embedded in the kit (Unbounded for text and display, Geist Mono for paths, links, versions; no network
  at runtime). No app file names a font file; a face change arrives with a kit sync.
- Type: always a role (`--t-*` with its `--tr-*` tracking), never a raw size. Every number that changes in place
  (sizes, speeds, ETA, counters, durations, percentages) sets `font-variant-numeric: tabular-nums` after its
  `font` shorthand (the shorthand resets it). Canonical links, video IDs, paths, versions and codes are mono
  (`--ff-feat: var(--ff-mono)` on the element).
- Identity: a theme (section 4 of the kit's documentation). Media Saver's default is **Dusk** (cobalt scene,
  coral accent `#ff7a59`); Nova, Frost and Bloom are the other three, chosen in Settings > Appearance and stored
  as `Settings.theme`. App CSS never hard-codes an accent: tints come from `--accent`, `--accent-ink`,
  `--btn-glow` and `--focus-ring` through `color-mix()` (the clean-link chip, the drop overlay, the preview hint
  bars, the progress bar glow, the well's focus ring). Semantic colours are the same in every theme: `--mint`
  (success, done), `--amber` (duplicates, "already saved", another platform's link), `--rose` (errors); the MP3
  violet of badges and the per-card toggle thumb is an app kind colour (`css/app.css`). Deliberately no logo,
  mark or brand colour of Instagram, YouTube, TikTok or X anywhere (kind glyphs are original line icons: `reel`,
  `post`, `video`, `shorts`).
- Legibility on glass: ink alphas `--ink-1..4`, `--ink-shadow` on lenses, `--well` under inputs and long text.
- Durations: `--d-1` (hover fill, rim), `--d-2` (state fades), `--d-3` (layout fades, progress), `--d-4` (long
  reveals, icon turns with `--ease-spring`); no literal duration in app CSS except the decorative loops of the
  scene ghosts, the empty-state art and the shimmer / sheen loops.

## 4. Motion — kit `js/motion.js`

The motion template is the kit's (its documentation, section 8): one spring solver, the named springs (`snappy`,
`bouncy`, `jelly`, `press`, `smooth`, `expand`, `sheet` / `sheetClose`, `materialize`), WAAPI helpers
(`springEasing`, `springAnimate`, `whenDone`) and the shared entrances and exits (`enterRow`, `leaveRow`,
`fadeIn`, `fadeOut`). Media Saver adds no spring and no duration of its own; where it animates by hand (chip
morph, fly-out, counters, the hero pill's width) it uses the kit's springs by name.

Behaviours:
- Gel press (`gel(el)`): squish `scale(1+0.10k, 1-0.07k)` (k larger for small controls), release with an inverse
  velocity kick so the overshoot reads as jelly; the tint lightens while `.is-pressed`. The surface stays in place
  (no pointer follow). Uses the individual `scale` property so it composes with layout transforms.
- Segmented droplet: x and width springs; stretch `1 + |v|/2400` along the motion, thinner across it; lifts (scale +
  lensing) while pressed or dragged, can be dragged across options and snaps to the one under its centre.
- Switch (kit, 46 x 28 px track, 24 px thumb): the thumb grows to a 34 px droplet while pressed and turns into a lens
  over the track, travels with `bouncy`.
- Stepper (`Stepper` in the kit's `js/controls.js`, Settings "Simultaneous downloads"): the number rolls through a 28 px window
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
- `Download`: the kit's hero pill (`.hero`, the kit's documentation, section 6.1), a pill lens with the gel
  press. States set by `js/composer.js` through `data-state`: `empty` (neutral glass, dim ink, no action) ->
  `ready` (the theme's accent glass with glow and top streak, label "Download N Reels") -> `sending` (the width
  springs to a 52 px circle with the spinner while `enqueue` runs, `snappy`; back to the slot width with `bouncy`)
  -> `progress` (dark glass track, the liquid level `--p` 0..1 with "Downloading N of M" and the speed or
  percentage) -> `done` (mint glass, "Done" or "Done N of M") -> `empty`. The level moves by `translateX` only and
  carries its own sheen loop in the progress state; entering that state sets the level without travel
  (`.pm-instant` for one style flush), so a new batch never drains from the last batch's value. Below 380 px of
  action-row width the speed / percentage moves under the text (two lines, `css/app.css`).
- Link chips: each chip appears showing the original link, then its text width springs to the clean short form
  (`shortLink` in `js/format.js`, by platform first, then kind: Instagram `reel/<code>`, `p/<code>`; YouTube
  `watch?v=<id>`, `shorts/<id>`; TikTok `video/<id>`, `t/<code>` for a short link; X `status/<id>`) while the original
  blurs away; invalid chips shake once; chips of another platform appear without morph or shake, with a "Switch to
  <their platform>" action. A chip shrinks only as far as its note or action needs (`flex: 0 1 auto`, no fixed max
  width), so 19-digit TikTok and X ids stay whole in the 332 px composer. After `Download` the chips fly out toward
  the queue. A mode switch rebuilds the chips (the text is cleaned again for the new platform).
- Mode switch: cards of the other platforms get `[hidden]` (no rebuild, no refetch; 150 cards switch in ~3 ms), the
  first 8 cards of the new mode enter with the kit's `enterRow` stagger (40 ms per card).
- Preview: a pending card (queued, `previewed` false) shows a skeleton bar for the title and a sheen over the
  thumbnail; both are CSS loops bound to `.is-pending`, which drops when `previewed` turns true even if every field
  stayed null. The title then fades in from a 3 px blur.
- Queue: a new card enters with the kit's `enterRow` (16 px from below, `bouncy`) while the visible cards FLIP
  down; the first eight cards of a mode switch enter staggered the same way; a removed card leaves with `leaveRow`
  (fade and shrink, then its height collapses so the cards below slide up, then it is removed). The folder menu
  opens with `fadeIn` and closes with `fadeOut`.
- Toasts: drop from the top with scale and lensing ramp, FLIP-stacked, max 3.
- `prefers-reduced-motion`: springs become critically damped and short, background drift stops, no elastic wobble;
  the skeleton, sheens, hint bars, empty-state art and the version dot pulse hold still; the mode switch skips the
  card stagger.

## 5. Background — kit `js/background.js`, app `js/scene.js`

The kit's scene (`initBackground(root, { idleMs: 60000 })`, the kit's documentation, section 7) in the colours of
the active theme: the base gradient and the `deep` treatment (two soft fields of the theme's hue, one small field
of its accent, the floor pool) are read from the theme tokens, so `applyTheme()` retints the scene live and
`js/scene.js` passes no colour of its own (the six "lagoon" fields of 2.x are gone). On top: the kit's faint contour
and caustic lines (crisp lines are what makes the lensing visible), its rising specks and light sweep, then Media
Saver's layer of up to four recent job thumbnails as soft ghosts (`setGhosts`; downscaled to 18×32 and upscaled by
the compositor = cheap blur), and the kit's vignette and grain. Pointer parallax moves fields, lines and specks by a
few px. Everything pauses when the window is unfocused, the document hidden, after 60 s without input (`idleMs`),
or with reduced motion; `html.is-still` pauses the decorative CSS loops inside the app and the clipboard pill as
well.

## 6. Components and files

| File | Content |
|---|---|
| `index.html` | Static layout: `<html data-theme="dusk">` with the head script that applies the saved theme before the first paint, title bar (brand, mode slot, version pill `#ver-pill`, engine chip, gear, window buttons), composer (one `c-other` counter template that `js/composer.js` clones per platform; `#btn-download` as the kit's `.hero`), queue, overlay hosts (engine popover, update popover, settings sheet). |
| `vendor/liquid_ui/` | The Liquid UI kit (section 0): tokens, fonts, base, scene, material, components and their modules. Never edited here. |
| `css/theme.css`, `css/parts.css`, `css/app.css` | Layout tokens; Media Saver's own components (engine chip, window buttons, segmented variants, settings content, clipboard pill, ghosts, toast position); screen layout, the hero pill's placement. |
| `js/main.js` | Boot, the theme (`applyTheme(settings.theme)` on connect and on every `settings` event, `localStorage['media-saver.theme']` for the next first paint), title bar, mode switch (four options, Alt+1..Alt+4), window buttons, entrance, auth -> auto retry of Instagram `login_required` jobs. |
| `js/mode.js` | `PLATFORMS` (`instagram`, `youtube`, `tiktok`, `twitter`, in mode-switch order), `PLATFORM_NAME` (Instagram, YouTube, TikTok, X), `QUALITIES`, `QUALITY_KEY`; current mode, default format and per-platform quality (`settings.mode`, `format`, `quality_<platform>`): local apply at once, `set_settings` persist, revert on refusal, stale-echo guard, `onPref(key)` subscribers, `html[data-mode]`, `html[data-format]`. There is no "other platform" helper: a switch action always names the platform of the item in question. |
| `js/quality.js` | Quality control of the composer's spec row: one `Segmented` per platform (only the mode's one visible), hint line, disabled in MP3, the H.264 note on YouTube Max only. |
| `js/api.js` | `window.__msEvent`, `pywebviewready` wait (1.5 s), mock fallback, `call()` / `on()`. |
| `js/mock-api.js` | Full mock of BRIDGE_API (section 7 lists the URL flags). |
| `js/scene.js` | The kit's background in the theme's colours, the thumbnail ghosts (`setGhosts`); re-exports `activity` / `setFocused`. |
| `js/engine.js` | Engine status chip (dot / ring progress), hover card with versions, popover with components and `update_engine`. |
| `js/update.js` | Mounts the kit's update control (`mountUpdateControl`): version pill, update popover, update toasts; forwards `settings` events; `updateControl()` for the settings sheet's `mountSettings`, `appVersion()`. |
| `js/composer.js` | Input capsule, live clean preview for the mode, chips of other platforms with "Switch to <their platform>", counters (one "N <platform>" pill per other platform present), tools (three plain `.btn-s`), Ctrl+V anywhere, drag and drop (text, URLs, `.txt`), Ctrl+Enter, format toggle, the `Download` hero pill with its five states and the liquid level (one batch per platform). |
| `js/clipboard.js` | Clipboard prompt pill on start and on `window` focus when `clipboard_watch`; offers the mode's links, or else the links of one other platform (the one with the most fresh links) with "Add to <platform>". |
| `js/queue.js` | Queue header (counts of the mode, "Clear finished", the "Open folder" split button with its folder menu, `open_output_dir` / `choose_output_dir`, the "Videos now go to ..." toast on an `output_dir` change), job cards of every platform (others `[hidden]`), card shape (`tall`: Reels, Shorts, TikTok; `wide`: YouTube videos and X posts, an X post turns `tall` once its width / height or its thumbnail shows a vertical video), placeholder glyph from `kindGlyph`, author with `@` on Instagram, TikTok and X (handles), plain on YouTube (channel names), preview skeleton, per-item format toggle, actions by status, error row (sign-in for Instagram only), empty state per mode, `clear_finished(mode)`. |
| `js/settings.js` | Settings sheet (Appearance first: the kit's theme row, applied at once and saved as `theme`, restored from the confirmed value with a toast when refused; no quality row: quality lives in the main window; the Instagram group shows in Instagram mode only; the file name example uses a platform-typical author and id: Instagram `studio.lumen` / `DAbC123xYz_`, YouTube `Lumen Studio` / `dQ4w9WgXcQ0`, TikTok `studio.lumen` / `7106594312292453675`, X `studio_lumen` / `1577855540407197696`), the "Updates" group from the kit's `mountSettings`; its controls are the kit's Segmented / Switch / Stepper / Select. |
| `js/icons.js`, `js/format.js` | Original line icons registered into the kit's set (incl. `video`, `shorts`, `audio`, `swap`, `app`, `folderEdit`) and `kindGlyph(platform, kind)` (Instagram `reel` / `post`, YouTube `video` / `shorts`, TikTok `shorts`, X `video`), en-US plurals / item nouns / short links / sizes / ETA / relative time / link error notes (all by platform). |
| `assets/icon.svg`, `assets/icon-1024.png` | App icon: glass droplet with a down arrow (PNG rendered from the SVG with headless Edge). |

### 6.1 Placement decisions

- Mode switch (Instagram / YouTube / TikTok / X): a `Segmented` (`seg-tb` variant, 34 px) in the title bar right
  after the app name, options in the order of `PLATFORMS`. The mode scopes the whole window (composer, queue,
  clipboard prompt, settings), so it sits above both columns rather than inside the composer; it reads as the app's
  top-level navigation. It is a sibling of the drag region (`.tb-mode`, `flex: none`). Alt+1..Alt+4 switch modes
  (matched on `KeyboardEvent.code` `Digit1..4`, so keyboard layouts that type another character with Alt+digit
  still work); each option's tooltip names its shortcut. Width check at the 940 x 640 minimum window (four platforms,
  measured in the mock): the four full labels need 278 px (Instagram 88, YouTube 80, TikTok 67, X 37, plus the 6 px track
  padding); the switch ends at x = 428 and the right cluster (version pill, "Engine ready") starts at x = 587, so 159 px stay
  free, more than the longest chip text ("Updating 100%", about 10 px wider) and the version pill's update dot
  need. The labels are therefore not shortened and no compact variant exists; "X" is the service's own name, not an
  abbreviation.
- Format toggle (MP4 / MP3): a `Segmented` (`seg-l` variant, 52 px tall, matching the hero pill) at the left of
  the composer's action row; `Download` takes the rest of the row (`.cmp-btn-slot`), so its width spring measures
  the slot. The shortcut hint is not in the button: a muted line under it (`.cmp-kbd-hint`, `--t-micro`, `--ink-4`, keys as faint
  `kbd`), 5 px below, centred on the button; the slot is a column and the action row aligns to the top, so the
  format toggle stays level with the button. The line's height is always reserved and only its opacity changes
  (visible while `Download` is `ready`), so neither the button's width nor the row ever moves. The button carries
  `aria-keyshortcuts="Control+Enter"`; the hint is `aria-hidden`.
- Quality (per platform, Settings `quality_instagram` / `quality_youtube` / `quality_tiktok` / `quality_twitter`): a spec row (`#cmp-spec`, 34 px) directly
  above the action row: caption "QUALITY", a compact `Segmented` (`seg-q` variant, 28 px options) and a hint line at
  the right. With the format toggle and `Download` below it, it reads as "what you get" right before the press. Not in
  the composer header: the counters live there and can show four pills (ready, invalid, duplicates, other), which
  would collide with a three-option control at the 940 px minimum (composer content 332 px wide). At 940 x 640 the row
  fits the longest hint ("Best, up to 4K") in full; below 290 px of row width the hint hides (the option tooltips carry
  the same text). One `Segmented` per platform (four), only the current mode's one visible: each keeps its own value, and a
  mode switch swaps options and value with a small transform-only settle (no opacity: the droplet is a lens). Saved
  through `js/mode.js` like the format (optimistic, reverted with a toast when refused, stale echoes ignored). In MP3
  the control stays in place, dimmed (`.seg.is-disabled`: opacity 0.42, out of the tab order, no pointer), with the
  title "MP4 only" and the hint "MP4 only", so the layout never jumps. The settings sheet has no quality row; "H.264
  for editing" stays there.

  | Platform | Options (value: label) | Hint | Tooltip |
  |---|---|---|---|
  | Instagram | `low`: Low, `medium`: Medium, `max`: Max | "≤ 480p", "≤ 720p", "Best" | "Low: up to 480p, the smallest files", "Medium: up to 720p", "Max: the best the Reel has" |
  | YouTube | `360`: 360p, `720`: 720p, `max`: Max | "≤ 360p", "≤ 720p", "Best, up to 4K" | "360p: up to 360p, the smallest files", "720p: up to 720p", "Max: the best the video has, up to 4K", plus, while H.264 for editing is on, ". With H.264 for editing on, 1440p and 4K are converted to H.264, which takes minutes" |
  | TikTok | `low`: Low, `medium`: Medium, `max`: Max | "≤ 480p", "≤ 720p", "Best" | "Low: up to 480p, the smallest files", "Medium: up to 720p", "Max: the best the video has, up to 1080p" |
  | X | `low`: Low, `medium`: Medium, `max`: Max | "≤ 480p", "≤ 720p", "Best" | "Low: up to 480p, the smallest files", "Medium: up to 720p", "Max: the best the post has, up to 1080p" |

  The H.264 note is YouTube-only (TikTok HEVC sources are converted by the same "H.264 for editing" pass, but at
  1080p at most it takes seconds, so the tooltip does not warn).
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
  only 24 px (30 + 32 + 26 px of actions next to the counts). The Settings row "Downloads folder" (path + "Change…")
  stays. Whatever route changes `output_dir`, the `settings` event updates the title, the menu caption and the
  Settings row, and `js/queue.js` shows one toast "Videos now go to <folder name>"; the initial state never toasts.
- Per-item format: queued cards show a compact fill toggle (`.fmt-toggle`, 22 px, CSS thumb, radio semantics, arrow
  keys) in the badge row that calls `set_job_format(id, format)` optimistically and reverts with a toast when the
  backend answers `null`; once the job leaves `queued` it becomes a static badge (`MP4` neutral, `MP3` violet).
- Version pill (the kit's `.ver-pill`, `#ver-pill`): a small mono capsule lens `v2.0.0` left of the engine chip, so
  the two status controls of the app (engine, app) sit together and open popovers of the same family. Stages
  `available` / `downloading` / `ready` add a dot (accent; pulsing while downloading; mint when ready) and accent ink;
  errors of a background check stay quiet. Its tooltip names the stage ("Media Saver X.Y.Z is available",
  "Downloading Media Saver X.Y.Z", "Media Saver X.Y.Z is ready to install", else "Version and updates").
- Update panel: the kit's update popover in `#update-pop` (`pop-wide`, 384 px), a sibling of the engine popover that
  grows out of the version pill and opens left-aligned under it (kept inside the window): title "Media Saver", one
  sub-line, progress bar, a well with rows (Installed / Latest with size and date tags), release notes in a
  scrollable well, footer with "Checked <time>" and one action. Opening it refreshes the status
  (`update_status()`). While it is open it covers the toasts (they drop at the same place; the "ready" toast and
  the panel's action say the same).
- Settings "Appearance" group (first): the kit's theme row ("Theme", "Background colour and accent of the
  window", four swatches Dusk / Nova / Frost / Bloom, arrows move the choice). A click applies the theme at once
  (`applyTheme`: the root attribute, the scene retint) and saves `{ theme }`; the confirmed value from the answer
  or a `settings` event sets the row; a refusal shows the sentence as a toast and restores the confirmed theme.
  The persisted copy in `localStorage` only serves the first paint of the next launch; the bridge's value wins.
- Settings "Updates" group (between Engine and About): the kit's `mountSettings` rows: the status line with
  "Checked <date, time>" and the next action (Check for updates / Download / a percentage while downloading / Restart
  to update) and "Download updates automatically" (switch). They save their own keys and
  follow confirmed settings; the About group keeps the app name, version and "Open logs".

### 6.2 Update panel states (UpdateStatus.stage)

| Stage | Sub-line | Body | Action |
|---|---|---|---|
| `idle` | message or "Updates come from GitHub Releases" | Installed row | Check for updates |
| `checking` | message or "Checking for updates…" | indeterminate bar | Checking… (disabled) |
| `up_to_date` | message or "You have the latest version" | Installed row | Check for updates |
| `available` | "Version X is available" | Installed + Latest rows, release notes | Download (tinted) |
| `downloading` | "Downloading version X…" | determinate bar, "42 %" and "5.1 MB of 12.1 MB", rows, notes | Downloading… (disabled) |
| `ready` | "Version X is ready to install" | rows (Latest tagged "downloaded"), notes | Restart to update (tinted; disabled with the message as tooltip when `can_apply` is false; with active downloads a warning toast offers "Restart anyway") |
| `error` | the message (or "Could not check for updates" / "The download did not finish" when there is none) | the message in a rose box | Try again (repeats the interrupted step) |

Progress ticks only move the bar and the numbers, so the notes keep their scroll position. The first `available` of a
session (initial state or event) raises one toast "Media Saver X.Y.Z is available" with a "View" action (not raised
when the background download is on: the "ready" toast below follows instead).

Background download (`Settings.auto_download_updates`, default on; ARCHITECTURE 5.7 items 5-6). The engine starts
the download itself when a check reports `available`, once per `latest` (a repeated `available` of the same
version, or an `error` after the automatic download, never starts another one; "Try again" stays manual); the
page only renders the status (the mock does the same). On or off, stage `ready` raises one sticky toast per
`latest`: "Media Saver X.Y.Z is ready" with the action "Restart" (or "View" when `can_apply` is false, from source);
it stays until clicked. "Restart", the panel's and the settings row's "Restart to update" share one path: with jobs
probing, downloading or processing, the warning toast "Downloads in progress will stop when the app restarts" +
"Restart anyway" comes first. The app never restarts by itself. The panel shows the accent caption "New versions
download in the background" while the setting is on.

Finish sound (`Settings.sound_on_done`, default on; `js/sound.js`): `assets/sounds/download-done.wav` (0.75 s, 44.1
kHz stereo) plays when a job changes to `done` with a new file; not for "Already downloaded", not for restored
history; at most once per second, so a batch that finishes together sounds once.

Release notes Markdown (the kit's `renderMarkdown`): everything is HTML-escaped first; then `#` headings (levels 1
and 2 as titles, others as small caps labels), `-` / `*` / `+` bullets with continuation lines, blank-line
paragraphs, `**bold**`, `` `code` `` and `[text](https://...)` links opened through `open_url` (a `false` answer
shows "The link could not be opened."). Nothing else is interpreted.

Keyboard: Ctrl+Enter downloads, Ctrl+V anywhere pastes into the capsule, Esc closes the top overlay, arrows move the
segmented selection (mode, format, quality, settings) and the per-item format toggle, Alt+1..Alt+4 switch modes (Instagram, YouTube, TikTok, X), visible `:focus-visible` rings
everywhere.

## 7. Runtime notes

- Served by the shell from `http://127.0.0.1:<port>/` (ES modules do not load from `file://`). Dev preview:
  `python -m http.server 5173 --bind 127.0.0.1 --directory ui`, open `/?mock=1`. Mock URL flags (header of
  `js/mock-api.js`): `demo=1` (every state on every platform, MP3 jobs, queued jobs with pending previews; TikTok: one
  done job that took the H.264 pass and one queued short link; X: one done job and one `extractor` error "X changed
  something and the video could not be read. Try again later.", `can_login` false), `jobs=N` (N finished jobs spread
  over Instagram, YouTube, TikTok, X in turn), `mode=youtube|tiktok|twitter`, `format=mp3`, `update=available|uptodate|error`
  (launch check result; `error` fails the first check and finds 2.1.0 on "Try again"), `autodownload=0` (starts with
  "Download updates automatically" off), `hold=1` (queued jobs never
  start: previews and format toggles stay inspectable), `clip=0`, `fast=1`, `offline=1`.
- The page makes no network request: fonts, icons and images are local or `data:` URLs (checked in the browser's
  network log with the mock).
- Screenshots with a hidden or background browser pane: WAAPI animations pause there, so entrance and fade-ins hold
  their first frame; finish them first (`document.getAnimations().forEach(a => a.finish())` for the finite ones).
- Drag region: `.tb-drag` and the app name carry `pywebview-drag-region`; buttons are siblings, not children, because
  pywebview also drags from descendants of a drag element. Double-click on the drag area toggles maximize.
- Every lens owns one `<filter>` element; destroy lenses (`lens.destroy()`) when removing their element (toasts do).
- `mode`, `format` and `quality_<platform>` (instagram, youtube, tiktok, twitter) are written by `js/mode.js` only; while its own
  `set_settings` is in flight it ignores the `settings` echo for that key, so fast toggling never flips back.
  `js/settings.js` applies the same guard to its own controls (switches, segmented controls, the stepper, the
  browser select): while a save of a key is in flight, neither its answer nor a `settings` event moves that control;
  the answer of the last save sets it. The mock
  validates the quality values and picks the simulated resolution from the job's platform setting at start (Instagram
  480 / 720 / 1080; YouTube 360 / 720 / Max = 2160p for videos, which then always takes the H.264 pass while it is on,
  1080p for Shorts; TikTok 480 / 720 / 1080 vertical, HEVC source, so the H.264 pass runs while it is on; X 480 / 720 /
  1080 landscape, AVC source, no pass). The mock cleans TikTok and X links by the canonical forms of BRIDGE_API
  (CleanResult): TikTok `/@user/video/<id>` (kind `video`), `vm.` / `vt.tiktok.com/<code>` and `tiktok.com/t/<code>`
  (kind `share`, host kept); X `/<user>/status/<id>`, `/i/status/<id>`, `/i/web/status/<id>` (kind `post`); profiles,
  photo posts, other paths and `t.co` links are `unsupported_path`. Stage texts of TikTok and X jobs are the core's:
  "Fetching info", "Downloading video", "Downloading audio", "Merging tracks", "Converting to H.264", "Converting to
  MP3", "Done".
- A job without `platform` counts as Instagram, and a job without `previewed` counts as previewed (older payloads
  never show an endless skeleton).

## 9. Copy and locale rules

- Neutral, factual copy (workspace rule, `_workspace/CONVENTIONS.md`, "UI copy"): say what the control does or
  what happened, in as few words as that takes. No emotion, hooks, filler or salesmanship ("just", "already",
  "any junk", "magic", exclamation marks, "Media Saver strips the junk"). One line where one line does.
- The UI is English only (US). Buttons, labels and titles use sentence case: "Download", "Paste", "Clean links",
  "Copy clean links", "Sign in to Instagram", "Clear finished", "Open folder", "Change folder…", "Update now", "Check for updates",
  "Restart to update", "Try again". Platform and format names keep their own case: Instagram, YouTube, TikTok, X,
  Reels, Shorts, MP4, MP3, H.264, FFmpeg.
- Plurals go through `plural(n, [singular, plural])` in `js/format.js` ("1 link" / "2 links"); never hard-code a
  form. Item nouns go through `itemNoun(n, platform)` / `nItems(n, platform, format)`: Instagram "Reel" / "Reels",
  YouTube, TikTok and X "video" / "videos", and " as MP3" appended for MP3 ("Queued 3 Reels", "Queued 2 videos as
  MP3").
- Formats are en-US: decimal point, units B / KB / MB / GB and MB/s, "fps", relative time "just now", "5 min ago",
  "2 h ago", "yesterday", dates like "Oct 20" / "Oct 18, 2026". No `ru` / `ru-RU` locale arguments anywhere.
- Strings the core emits (stage text, error messages, UpdateStatus.message) are shown as received; the mock mirrors
  them: "Queued", "Fetching info", "Downloading video", "Downloading audio", "Merging tracks", "Converting to H.264",
  "Converting to MP3", "Already downloaded", "Done". A YouTube, TikTok or X `login_required` message comes from the
  core; the UI adds no sign-in button for those jobs (`can_login` is only ever true for Instagram).
- The product name is "Media Saver" everywhere (title bar, update panel, toasts, About). Only "Instagram",
  "YouTube", "TikTok" and "X" name the services; do not abbreviate them. "X" is the only name of the fourth service in
  UI copy, never "Twitter" (the id `twitter` stays in code, Settings keys and URLs only). An article before "X" or
  "Instagram" is "an" ("This is an X link"). No logos or marks of any platform.
- Mode-aware strings (the current mode decides; `composer.js` COPY, `queue.js` EMPTY_COPY):

  | Place | Instagram | YouTube |
  |---|---|---|
  | Input placeholder | "Paste Instagram Reels or post links, one or many. Other text is ignored." | "Paste YouTube video or Shorts links, one or many. Other text is ignored." |
  | Preview hint | "Clean links appear here as `instagram.com/reel/code`" | "Clean links appear here as `youtube.com/watch?v=id`" |
  | Ready button | "Download Reel" / "Download 3 Reels" | "Download video" / "Download 3 videos" |
  | Ready button, MP3 | "Download as MP3" / "Download 3 as MP3" | same |
  | Nothing pasted | "Paste Instagram links or press Ctrl+V" | "Paste YouTube links or press Ctrl+V" |
  | No usable link | "No Instagram Reels or post links found here" | "No YouTube video or Shorts links found here" |
  | Other-platform links | chip action "Switch to YouTube"; counter "N YouTube"; toast "These are YouTube links" + "Switch to YouTube" | the same with Instagram |
  | Chip notes | "not a Reel or post", "no video code", "not supported" | "not a video", "no video ID", "not supported" |
  | Empty queue | "No Reels yet" + "Paste links on the left or press Ctrl+V. Files are saved as MP4 or MP3 in the quality set above Download." | "No YouTube videos yet" + the same text |
  | Error row | "Sign in to Instagram" / "Retry signed in" when `can_login` | the core's message and "Log" only |
  | Settings | "Instagram" group shown | "Instagram" group hidden |
  | Quality control | Low / Medium / Max, hints "≤ 480p" / "≤ 720p" / "Best" | 360p / 720p / Max, hints "≤ 360p" / "≤ 720p" / "Best, up to 4K" |
  | Clipboard pill | "N links on the clipboard" + "Add" | the same; links of another platform only: "N Instagram links on the clipboard" + "Add to Instagram" |

  | Place | TikTok | X |
  |---|---|---|
  | Input placeholder | "Paste TikTok video links, full or short, one or many. Other text is ignored." | "Paste links to X posts with a video, one or many. Other text is ignored." |
  | Preview hint | "Clean links appear here as `tiktok.com/@user/video/id`" | "Clean links appear here as `x.com/user/status/id`" |
  | Ready button | "Download video" / "Download 3 videos" | the same |
  | Ready button, MP3 | "Download as MP3" / "Download 3 as MP3" | the same |
  | Progress (one job) | "Downloading video" | the same |
  | Nothing pasted | "Paste TikTok links or press Ctrl+V" | "Paste X links or press Ctrl+V" |
  | No usable link | "No TikTok video links found here" | "No X post links found here" |
  | Chip notes | "not a video" (photo post, profile, other path), "no video ID", "not supported" | "not a post" (profile, other path, `t.co` link), "no post ID", "not supported" |
  | Empty queue | "No TikTok videos yet" + the shared empty-queue text | "No X videos yet" + the same |
  | Error row | the core's message and "Log" only | the same |
  | Settings | "Instagram" group hidden | the same |
  | Quality control | Low / Medium / Max, hints "≤ 480p" / "≤ 720p" / "Best" | the same |
  | Clipboard pill | "N links on the clipboard" + "Add"; links of another platform only: "N TikTok links on the clipboard" + "Add to TikTok" | the same with "X" |

  Shared by all four modes: the chip note of a link of another platform is "<Platform> link"; its chip action is
  "Switch to <Platform>", where <Platform> is that chip's own platform. The counter row shows one pill per other
  platform present ("2 YouTube", "1 X"; the row wraps to a second line when ready, invalid, duplicates and two or more
  such pills meet in the 332 px composer at the 940 px minimum). `Download` with only links of other platforms shakes
  and toasts "This is a TikTok link" / "These are TikTok links" / "These are TikTok and X links" with the action
  "Switch to <the platform with the most links>". Valid chips note the cut: "−1 char", "−17 chars".

- Theme copy is the kit's: group "Appearance", row "Theme" with the hint "Background colour and accent of the
  window", names "Dusk", "Nova", "Frost", "Bloom".
- Update copy: toast "Media Saver X.Y.Z is available" (action "View"); About line "Downloads Instagram Reels, YouTube,
  TikTok and X videos as MP4 or MP3"; the "Updates" rows are the kit's copy: switch "Download updates automatically"
  (hint "Checked every 5 minutes; installing still needs a restart"; the check itself always runs and its period is
  fixed, so there is no switch or select for it); sticky toast "Media Saver X.Y.Z is ready"
  + "Restart"; restart warning (Media Saver's) "Downloads in progress will stop when the app restarts" + "Restart
  anyway"; panel caption "New versions download in the background". Switch "Sound
  when a download finishes" (hint "A short sound for each finished file").
- The runtime data folder is `media-saver_data\` (never `data\`) wherever copy or mock paths name it (mock log paths:
  `D:\Apps\MediaSaver\media-saver_data\logs\jobs\<id>.log`). The default output dir is the core's decision (the
  user's Videos folder + `Media-Saver`, BRIDGE_API Settings); the UI only shows `Settings.output_dir`. The mock's
  default is `C:\Users\user\Videos\Media-Saver`; its `choose_output_dir` alternates between `D:\Media\Saved videos`
  and that default, so the toast, the "Open folder" title and the Settings row visibly change on every try.
- Folder copy: menu caption "Saving to" + the path; items "Open folder", "Change folder…"; toast after a change
  "Videos now go to <last path segment>" (a drive root shows as `E:\`); the chevron's label "Downloads folder
  options" and title "Choose where videos are saved".
