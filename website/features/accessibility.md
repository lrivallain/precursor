---
title: Accessibility
---

# Accessibility

Precursor's **Settings → Appearance** tab has a **theme** toggle (light / dark /
system) and a **reading font** picker. The font choice applies to the whole
app — every pane, panel and message — so you can pick whatever renders text
most comfortably for you, including options designed for dyslexic or
low-vision readers.

<Screenshot src="/screenshots/accessibility.png" alt="Settings → Appearance showing the theme toggle and the reading-font picker with System default, OpenDyslexic, Atkinson Hyperlegible and Lexend options, each previewed in its own font" caption="Settings → Appearance — each option is previewed in its own font before you pick it." />

## Reading fonts

| Font | Good for |
| --- | --- |
| **System default** (Inter) | The regular app font — no override. |
| **OpenDyslexic** | Weighted lettering with distinct, asymmetric glyph shapes designed to reduce letter confusion (b/d, p/q) for dyslexic readers. |
| **Atkinson Hyperlegible** | Designed by the Braille Institute to maximize character-to-character distinction — a good fit for low vision. |
| **Lexend** | A reading-fluency typeface: independent studies found it measurably speeds up reading for low-proficiency and dyslexic readers, without the unconventional letterforms of OpenDyslexic. |

All three are bundled locally as `@font-face` assets (SIL Open Font License), so
switching fonts works fully offline and needs nothing installed on your OS —
see `frontend/public/fonts/LICENSE.md` for attribution.

The setting applies **instantly** and persists across reloads; it is stored
both in your browser (so it takes effect the moment you pick it) and in your
account settings (`font_family`), alongside the theme.

## Requesting another font

Suggestions for other accessibility-focused fonts (e.g. for low vision, ADHD,
or other reading differences) are welcome — please open an issue with a link
to the typeface and its license.
