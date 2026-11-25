---
name: Omnific
description: Any motion, frozen and checked. A black photo studio where every physical event is shown as its own strobe exposure.
colors:
  bg: "#0B0C10"
  surface: "#14151B"
  surface-2: "#1C1E26"
  line: "#2A2C35"
  fg: "#F2F2EE"
  muted: "#A3A5AE"
  accent: "#FF4D8A"
  accent-fg: "#0B0C10"
  flash: "#FFD23D"
  flash-fg: "#14110A"
  data: "#3DE0FF"
  ok: "#5BE49B"
  bad: "#FF6B6B"
  warn: "#FFB547"
  gel-violet: "#A88BFF"
  gel-orange: "#FF8A4C"
  studio: "#0E0F14"
  bg-light: "#F4F5F7"
  surface-light: "#FFFFFF"
  surface-2-light: "#ECEEF2"
  line-light: "#D9DCE3"
  fg-light: "#0B0C10"
  muted-light: "#555A66"
  accent-light: "#C2185B"
  data-light: "#007C91"
  ok-light: "#1A7F4B"
  bad-light: "#C62828"
  warn-light: "#9A5B00"
  gel-amber-light: "#A66500"
  gel-violet-light: "#6A4BD6"
  gel-orange-light: "#D1491A"
  studio-light: "#E9EBEF"
typography:
  display:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "4.25rem"
    fontWeight: 800
    lineHeight: 0.95
    letterSpacing: "-0.04em"
  headline:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "3rem"
    fontWeight: 800
    lineHeight: 1
    letterSpacing: "-0.04em"
  title:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.25rem"
    fontWeight: 700
    lineHeight: 1.375
    letterSpacing: "-0.025em"
  lead:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "1.125rem"
    fontWeight: 400
    lineHeight: 1.55
  body:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    fontFeature: "\"ss01\""
  label:
    fontFamily: "Archivo, ui-sans-serif, system-ui, sans-serif"
    fontSize: "0.75rem"
    fontWeight: 600
    lineHeight: 1.33
  numeric:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.4
    fontFeature: "\"tnum\""
  numeric-axis:
    fontFamily: "JetBrains Mono, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.4
    fontFeature: "\"tnum\""
rounded:
  sm: "4px"
  md: "6px"
  lg: "8px"
  xl: "12px"
  2xl: "16px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "20px"
  xl: "32px"
  2xl: "64px"
  gutter: "16px"
  gutter-sm: "24px"
  container: "1280px"
components:
  button-flash:
    backgroundColor: "{colors.flash}"
    textColor: "{colors.flash-fg}"
    typography: "{typography.body}"
    rounded: "{rounded.xl}"
    padding: "0 20px"
    height: "44px"
  button-flash-compact:
    backgroundColor: "{colors.flash}"
    textColor: "{colors.flash-fg}"
    rounded: "{rounded.md}"
    padding: "0 16px"
    height: "40px"
  button-outline:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.fg}"
    rounded: "{rounded.lg}"
    padding: "0 16px"
    height: "40px"
  button-play:
    backgroundColor: "{colors.flash}"
    textColor: "{colors.flash-fg}"
    rounded: "{rounded.full}"
    size: "40px"
  control-pill:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    rounded: "{rounded.full}"
    padding: "0 14px"
    height: "36px"
  strobe-toggle-on:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.accent-fg}"
    typography: "{typography.label}"
    rounded: "{rounded.lg}"
    padding: "6px 12px"
  nav-link:
    textColor: "{colors.muted}"
    rounded: "{rounded.md}"
    padding: "6px 12px"
  nav-link-active:
    backgroundColor: "{colors.surface-2}"
    textColor: "{colors.fg}"
    rounded: "{rounded.md}"
    padding: "6px 12px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.xl}"
    padding: "16px"
  prompt-bar:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.fg}"
    rounded: "{rounded.2xl}"
    padding: "8px"
  input-field:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  badge-analytic:
    textColor: "{colors.ok}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
  badge-validated:
    textColor: "{colors.data}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
  badge-heuristic:
    textColor: "{colors.warn}"
    typography: "{typography.label}"
    rounded: "{rounded.full}"
    padding: "2px 10px"
---

# Design System: Omnific

## Overview

**Creative North Star: "The Strobe Study"**

Omnific is a photographer's studio after Harold Edgerton: a black ground, graphite surfaces seamed by hairlines, flash-white type, and gel-coloured light that does real work. Every physical event is shown as its own multiple exposure: demo and library thumbnails are drawn from the simulation's own playback data as fading gel dots, the 3D viewer can strobe each moving object into translucent ghosts, and the build diagram fires one gel per finished stage so it fills with colour as the job nears ready. The light theme is the contact sheet: paper ground, ink type, the same gels deepened until they hold contrast on white.

The interface is an instrument, not a brochure. Density is moderate: one display headline on the home page, then working surfaces (prompt bar, viewer, transport, charts, panels) that sit flat on the ground and are separated by 1px seams rather than shadows. Archivo carries everything at tight tracking; JetBrains Mono appears only where a number, unit or measurement must line up. The rejected category default is the white page with a centred prompt box, an indigo accent and three equal demo cards.

**Key Characteristics:**
- Black studio ground (dark is the native world); paper contact sheet in light.
- Three gels with fixed jobs: magenta for current/links/focus, yellow for the one primary action, cyan for data.
- Exposures, not decorations: every strobe trail is computed from real playback.
- Hairline seams, flat surfaces, no glass.
- Mono only for numbers.

## Colors

A near-black studio lit by three saturated gels, with three further gels held in reserve for extra chart series and strobe objects.

### Primary
- **Magenta Gel** (accent; `accent-light` on the contact sheet): the current thing. Links, the active nav and tab underline, the focus ring, text selection (at 28% alpha), the caret, the "frozen" word in the hero, the checked toggle, the Strobe toggle when on, the current build stage. First chart series and first strobe object.

### Secondary
- **Yellow Flash** (flash, with flash-fg ink on it): the primary action and nothing else. Run simulation, Play, Accept, Publish, Upload. It is the same in both themes.

### Tertiary
- **Cyan Gel** (data; `data-light` on paper): data. The "Validated numerical" fidelity badge, the second chart series and second strobe object.
- **Reserve gels** (amber in light, violet, orange; c3 to c6 in CSS): chart series 3 to 6 and further strobe objects, in that order. In dark, series 3 is the yellow flash itself.

### Neutral
- **Studio Black** (bg): page ground and header.
- **Graphite** (surface) and **Raised Graphite** (surface-2): cards, prompt bar, transport, badges' backing; surface-2 for the active nav pill, hover wash and the todo state of build nodes.
- **Seam** (line): every border, the 1px gaps of the contact-strip grid, scrollbars, unfilled progress segments.
- **Flash White** (fg) and **Dim Grey** (muted): primary and secondary text.
- **Studio Backdrop** (studio): the 3D viewer and strobe thumbnail background, a touch lifted from bg so the frame reads.
- **Status** (ok, bad, warn): fidelity and document states, errors, "default" answer markers.

### Named Rules
**The Three Gels Rule.** Magenta marks what is current, yellow marks the one thing to do, cyan marks data. A gel never borrows another's job.

**The One Flash Rule.** At most one yellow-filled control per region at a time. Secondary actions are outlined on graphite.

**The Exposure Blend Rule.** Every exposure is the full gel colour, fading with age from 0.9 (newest) to 0.35 (oldest). Strobe thumbnails add light on the black studio (`mix-blend-mode: plus-lighter`) and overprint like ink on the contact sheet (`multiply`), with older discs smaller and the final trail exposure at full strength. 3D ghosts in the viewer use normal blending in both themes, never additive, so the gel hue holds over light scene geometry.

## Typography

**Display Font:** Archivo (with ui-sans-serif, system-ui)
**Body Font:** Archivo, stylistic set ss01 on
**Label/Mono Font:** JetBrains Mono (with ui-monospace), numbers only

**Character:** A wide-shouldered grotesque set heavy and tight for headlines, quiet and plain for UI, with a programmer's mono reserved for readouts so measurements look measured.

### Hierarchy
- **Display** (800, 2.6rem mobile to 4.25rem, 0.95, -0.04em): the home headline only.
- **Headline** (800, 2.4rem mobile to 3rem, 1, -0.04em): page titles (Public library, Documents).
- **Title** (600 to 700, 1.125 to 1.25rem, tight tracking): section heads ("Ready to explore"), the lead demo title, the simulation title. Panel heads are 0.875rem bold, tight.
- **Lead** (400, 1.125rem): the one intro paragraph under the display, max about 42rem.
- **Body** (400, 0.875rem to 1rem): panels, forms, controls.
- **Label** (600, 0.75rem): badges, chips, viewer controls. Never uppercase, never tracked out.
- **Numeric** (JetBrains Mono 13px; 11px for axes and scales, tabular figures): time readout, speed, chart values, percentages, parameter values, filenames.

### Named Rules
**The Mono Means Measured Rule.** JetBrains Mono appears only on numbers, units, measurements, code and filenames, always with tabular figures. Prose and labels stay in Archivo.

## Layout

A single 1280px container with a 16px gutter (24px from 640px up), under a sticky 1px-seamed header holding the wordmark left and nav right. The home page stacks headline, lead, a prompt bar about 900px wide (max 56rem), quiet controls under it, then the demos 64px below. Demos are a contact strip: one lead exposure spanning three rows at 2:1 width beside three compact rows, separated by 1px seam gaps rather than gutters; it collapses to a single column under 1024px. The job page puts the 3D viewer first (48vh, 60vh from 1024px, sticky on desktop) with transport and decision panels under it, and tabs with charts in a two-column grid on the right. Rhythm: 8px between controls, 16px inside cards, 20px for the build diagram, 32px between hero blocks, 64px between page sections.

## Elevation & Depth

Flat by construction. Surfaces sit on the ground and are separated by hairline seams and the bg to surface to surface-2 step. Shadows are rare and diffuse, used only where something floats over content. The one animated depth is the build-glow on the current stage.

### Shadow Vocabulary
- **Prompt lift** (`box-shadow: 0 18px 50px -30px rgb(0 0 0 / 0.6)`): the home prompt bar only, so the one input reads as the place to start.
- **Popover** (`box-shadow: 0 18px 40px -20px rgb(0 0 0 / 0.6)`): dropdown panels (the Documents picker).
- **Overlay chip** (Tailwind shadow-sm): controls floating on the 3D viewer.
- **Build glow** (`0 0 0 0..4px` plus a soft drop in the current gel at 45%, 1.6s loop): the current build stage, reduced-motion off.

### Named Rules
**The Seam Not Shadow Rule.** Separate surfaces with a 1px seam; reach for a shadow only when an element floats over other content. No glass, no backdrop blur.

## Shapes

Gently rounded instrument panels. Cards, charts and panels are 12px; the prompt bar, contact strip, build diagram and transport are 16px; buttons and fields 6 to 12px; badges, pills, chips and the play button are fully round. The build loop is grouped by a dashed seam. The recurring silhouette is the exposure dot: circles growing from 3.5px to 9.5px radius along a path, echoed in the three-dot wordmark mark and the drawn strobe icon.

## Components

### Buttons
- **Shape:** 12px on the hero Run button, 6px in panels, fully round for Play.
- **Primary:** yellow flash fill, flash-fg ink, bold 0.875rem, 44px tall in the prompt bar, 40px in panels; a trailing arrow icon on Run.
- **Hover / Focus:** `brightness(1.05)`; press scales to 0.98 (0.95 on Play); focus is the global 2px magenta outline at 2px offset. Disabled drops to surface-2 with muted text or 50% opacity.
- **Secondary:** graphite or transparent fill, 1px seam, the border strengthens to fg at 40% on hover.

### Chips
- **Control pill:** 36px, fully round, graphite with a seam and muted text, brightens to fg on hover; holds an icon and label (attachments, documents, the clarify switch).
- **Example chips:** borderless, xs muted text, surface-2 wash on hover.
- **Switch:** 28 by 16px track in line, magenta when on.

### Cards / Containers
- **Corner Style:** 12px (16px for the larger composite panels).
- **Background:** graphite surface on the studio ground.
- **Shadow Strategy:** none; see Elevation.
- **Border:** 1px seam.
- **Internal Padding:** 16px (12px for charts).
- **Decision panel:** a yellow tint (flash at 7%) with a flash border at 60%, the only tinted card.

### Inputs / Fields
- **Style:** studio-black fill inside graphite panels, 1px seam, 6px radius, 8 by 12px padding. Search uses graphite, 12px radius and a leading icon.
- **Focus:** border shifts to magenta at 70%; caret is magenta.
- **Error:** a bad-tinted alert box (bad at 10% fill, 50% border) with role="alert".

### Navigation
- **Style:** 0.875rem medium links, 6 by 12px, 6px radius; muted at rest, fg on hover; the current page sits on a surface-2 pill. "New simulation" hides under 640px. Job tabs use a 2px underline in magenta on a seam rule.

### Fidelity Badge
Fully round, 1px border at 60% and a 10% fill in the status colour: Analytic in ok green, Validated numerical in data cyan, Heuristic in warn amber. Its tooltip states what the fidelity means.

### Strobe Exposure (signature)
A multiple exposure of the simulation drawn from its own playback: 11 exposures per moving object on the studio backdrop (the svg carries the studio ground itself, so it fills its cell), objects coloured magenta, cyan, yellow in order, link lines in fg at 55%, older dots fainter and smaller, the last at full strength. Still scenes fall back to a long-exposure streak. In the viewer, the Strobe toggle (magenta fill when on) draws 8 full-gel ghosts per moving object (opacity 0.9 newest to 0.35 oldest, normal blending in both themes) and re-frames the camera around the whole exposure.

### Build Progress (signature)
The pipeline as a diagram: segmented 6px progress bar, then stage nodes joined by arrows, with the revision loop grouped by a dashed seam. Todo nodes are muted on surface-2 at 60%; the current node has a magenta border, a 14% magenta wash and the build glow; each finished node fills with its gel (cyan, yellow, magenta in turn) with gel-ink text and fires once (`flash-fire`, 0.9s, brightness 1.9 to 1). Failed nodes go bad red.

### Transport
A 16px-radius graphite bar: a round yellow Play, a round outlined Restart, a magenta range slider, then the mono time readout and a mono speed select.

### Icons
One drawn set on a 24px grid, 1.75 stroke, round caps and joins, currentColor, decorative unless labelled.

## Do's and Don'ts

### Do:
- **Do** keep each gel on its job: magenta for current, links and focus; yellow for the one primary action; cyan for data.
- **Do** draw any thumbnail or motion preview as a strobe exposure computed from real playback, never a stock image or a fake trail.
- **Do** set numbers, units and readouts in JetBrains Mono with tabular figures.
- **Do** separate surfaces with 1px seams on graphite and keep surfaces flat.
- **Do** draw exposures in full gel colour fading by age (0.9 to 0.35): plus-lighter on dark and multiply on light for 2D thumbnails, normal blending for 3D ghosts in both themes; hide build-glow and flash-fire under reduced motion.
- **Do** define every new colour as an "r g b" token pair for dark and light in globals.css and expose it through tailwind.config.ts.

### Don't:
- **Don't** put more than one yellow-filled control in a region.
- **Don't** use glass, backdrop blur or heavy drop shadows on resting surfaces.
- **Don't** set prose or labels in mono, or set labels in uppercase with wide tracking.
- **Don't** return to the category default: white page, centred prompt box, indigo accent, three equal demo cards.
- **Don't** hard-code colours in components; use the tokens (including `--gel-ink` for text on a gel fill).
