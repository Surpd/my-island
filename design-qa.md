# Design QA — Student and Teacher Campus

Source visual targets:

- Student master: `3-Фото-3.jpg` (592×1280), with landmark close-ups from `1-Фото-1.jpg`.
- Teacher master: `4-Фото-4.jpg` (592×1280), with placement/state direction from `5-Фото-5.jpg` and `6-Фото-6.jpg`.
- Implementation: Codex in-app browser captures at 390×844 CSS px, device scale factor 1.
- States checked: Student Campus, Student Information Focus, Teacher Campus, Teacher Schedule Focus, Teacher Schedule Full.

## Findings and iterations

- [Resolved P1] Student Information Focus initially exposed a solid-color right edge because its pan exceeded the enlarged raster bounds. Camera transforms now zoom from the landmark-specific origin and use only a small bounded pan. The repeated 390×844 capture keeps artwork across the whole viewport and the pavilion in view.
- [Resolved P2] The previous Student scene used a landscape master and could not preserve the reference geography in a phone crop. It now uses the clean vertical Student master.
- [Resolved P2] Primary destinations used generic line SVG glyphs. They now use six dedicated raster landmark assets derived from the supplied icon/close-up references.
- [Resolved P2] The top overlay repeated My Island branding and added a large welcome heading. Campus now keeps only the compact profile/admin actions.
- No remaining P0/P1/P2 visual findings in the checked states.

## Fidelity surfaces

- Fonts and typography: compact system text is used for readable controls; Georgia remains limited to sheet display headings, matching the editorial/premium reference tone. No large hero copy competes with the scene.
- Spacing and layout rhythm: both scenes fill 390×844; Today occupies about 184 px (22%) when collapsed; all persistent landmarks stay above it; hotspot capsules do not overlap or overflow.
- Colors and tokens: ivory, deep navy, warm terracotta/gold, and low-opacity shadows match the supplied world. Teacher treatment is calmer, while Student preserves the larger adventure scale.
- Image quality and assets: both master scenes are vertical 592×1280 JPEGs at roughly 300 KB; six 128×128 raster landmark icons replace generic destination glyphs. No primary destination uses Lucide.
- Copy and content: Student exposes exactly Schedule/Homework/Grades/Information. Teacher exposes Schedule/My Groups/Information plus conditional Homeroom. Today uses the real runtime date/time and existing API data.

## Interaction checks

- Today and contextual sheets respond continuously to pointer/touch drag; upward drag opens Full and downward drag returns to Peek.
- Focus hides all map labels, uses a landmark-specific camera target, replaces Today with a contextual sheet, and provides the correct “К острову” / “К кампусу” control.
- Student Information and Teacher Schedule Focus crops contain no blank raster edges.
- Teacher Schedule Full exposes the working day picker and real lesson rows.
- No horizontal overflow was visible at 390×844.

## Follow-up (P3)

- Add aligned day/night and event-plaza artwork variants when final assets exist.
- Consider dedicated high-resolution close-up rasters for the most data-heavy Focus destinations in the next UX pass.
- Re-check inside the final Telegram iOS shell after deployment to validate the host header’s exact safe-area contribution.

final result: passed
