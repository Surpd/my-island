# Design QA

## Latest targeted spatial shell pass — 7 September 2026

- Scope: shared Telegram top-safe layout and Campus ↔ Focus camera only.
- Browser: 390 × 844 CSS px; local approved-role data. Native chrome was a development-only simulation (47px device inset + 56px Telegram inset), not a physical iPhone session.
- Evidence: inline browser frames captured in this task for Teacher Groups at 175/350/490/699ms; reverse frames at 310/390ms; Student Information at 175/350/490ms. These are paused frames of the actual Web Animations timeline, not generated pictures. Filesystem screenshot export was not available through the capture surface.
- Motion: 700ms acceleration/defocus/swap/settle, maximum optical blur during the 322–378ms raster overlap. Both layers remain mounted for the reverse move; reduced-motion preference uses a short stationary dissolve.
- Earlier P1: API insets alone did not prevent controls moving into chrome. Found `.scene-app.scrollTop = 109.6` from automatic focus scrolling inside `overflow:hidden`. Fixed with a non-scrolling `overflow:clip` scene. Final forward/reverse measurement: scene scroll 0; profile and back top 115px, below the 103px simulated system region.
- Global layout: one bridge lifecycle from Home covers direct Admin entry, Student, Teacher, authentication and Profile. Device + content insets form the reserved band; a shared 12px breathing gap follows it. Admin scrolls inside the remaining viewport.
- Admin loading and loaded Overview inspected under the simulated chrome. No visible Admin controls above the 115px boundary.
- Fonts, labels, imagery, functional content and IA retained. Master framing unchanged after removing an initial overscan experiment.
- Checks: format, lint, TypeScript and production build passed. No exhaustive section walkthrough, per usage constraint.
- Limit: native Telegram iOS gesture ownership and perceived motion at real device frame rate cannot be certified by this browser fixture.

Latest targeted final result: passed

## Previous product refinement evidence

- Source visual truth path: `C:\Users\mitya\.codex\codex-remote-attachments\01a07727-cd3a-7de2-b6a4-096bea0bdbcd\3018D251-CE0C-4884-AE50-99CFAD39D38A\1-Фото-1.jpg`
- Implementation: `http://localhost:4173/` captured in the Codex in-app browser (the CUA screenshot is an inline artifact; this browser surface does not expose a filesystem path).
- Viewports: Student/Teacher `390 × 844` CSS px; Admin `1280 × 800` CSS px; device density normalized by the browser surface.
- States: Teacher Campus, Schedule Focus/Partial/Full; Student Campus, Homework Focus/Partial, Today Full, Profile; Admin Overview desktop/mobile.

## Full-view comparison evidence

The implementation keeps the approved scene-first composition, warm ivory/glass surfaces, navy/coastal palette, architectural landmark anchors, and small attached labels. The reference is a wide concept board rather than a matching phone state, so comparison focused on its label/icon hierarchy and visual language rather than pixel coordinates.

## Focused-region comparison evidence

The landmark labels and sheet headers were inspected at `390 × 844`. Labels use 32 px architectural assets with a compact attached text plate; contextual sheet headers use the larger landmark state. Focus close-ups remain sharp and the master scene now completes more of its zoom before the longer crossfade.

## Required fidelity surfaces

- Typography: Georgia display headings remain consistent; compact UI labels retain readable optical weight and truncate safely.
- Spacing/layout: Campus maintains a roughly 22% Today partial sheet; full sheets respect Telegram content-safe top/bottom insets.
- Colors/tokens: ivory, navy, teal, gold, and restrained coral states match the approved concept.
- Image quality: existing Campus and dedicated Focus rasters are preserved; no new or substitute artwork was introduced.
- Copy/content: prototype instructions and backend/admin jargon were removed from user-facing Student/Teacher states.

## Comparison history

1. Earlier P1: contextual partial sheet contained prototype instructions instead of product data. Fixed with destination-specific live summaries; post-fix Student Homework and Teacher Schedule captures show real empty/next-item states.
2. Earlier P1: sheet header tap competed with pointer drag and did not reliably reach Full. Fixed by resolving tap/drag in the shared pointer-up state machine; post-fix Teacher Schedule and Student Today reached `is-full` by tap and drag.
3. Earlier P2: quiet Today rendered redundant cards. Fixed by collapsing to one summary when there is no lesson/task.
4. Earlier P2: Telegram-safe positioning only consumed vertical insets. Fixed by plumbing all safe/content-safe inset sides into scene controls, back controls, Profile, sheets, and Preview banner.

## Findings

No actionable P0/P1/P2 visual mismatch remains in the representative states. Real Telegram chrome geometry still requires the production-device smoke check because the local browser cannot synthesize Telegram's native overlay.

## Follow-up polish

- P3: additional long-name combinations can be checked during the user's manual production walkthrough.

final result: passed
