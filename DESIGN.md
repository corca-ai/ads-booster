# Trace Workspace Design System

## Design read

Trace Workspace is a utilitarian marketing workbench. Its primary job is to make the live pipeline
legible: choose an account-scoped country/persona context, generate candidates, review caption and
topic, review the image, then leave approved work in `submitted` for manual publication.

`DESIGN_VARIANCE: 4`, `MOTION_INTENSITY: 2`, `VISUAL_DENSITY: 5`.

## Tokens

The canonical tokens live in `src/trace_capture/web/static/design-tokens.css`; root `tokens.css`
is the Hallmark-compatible entrypoint that imports that source of truth.

- Dark OKLCH surfaces create hierarchy without ornamental gradients or glow.
- Indigo accent is reserved for the primary generation action, focus, and submitted state.
- Body text stays at 16px, controls share a 44px minimum height, and muted copy remains readable.
- Motion is limited to press feedback, tab/state transitions, and loading skeletons. Reduced-motion
  users receive effectively instant state changes.

## Macrostructure: Workbench

The work surface has eight stable regions.

1. `workspace-toolbar` names the product and shows one live status line without an explanatory hero.
2. `account-console` keeps the logical account selector visible while market, schedule, automation,
   and account creation stay behind one settings disclosure.
3. `worker-console` keeps sanitized Mac availability visible and opens a separate protected manager
   for registration, replacement, and detailed health only when an operator asks for it.
4. `threads-console` keeps only the safe ON/OFF state visible and opens a distinct protected manager
   for OAuth profiles, default selection, and the auto-publish toggle.
5. `pipeline-summary` exposes compact counts for caption review, image work, and publication-ready
   results.
6. The two-tab rail separates candidate preparation from human review.
7. `generation-workbench` keeps the persona selector and four-candidate action together; full context,
   feedback learning, and manual registration use progressive disclosure.
8. The candidate list follows the generation action immediately and can be filtered by operational
   state without hiding the canonical total.

The Cloudflare build removes the entry form and opens directly into the last selected public logical
account scope. Account switching changes settings, context, candidates, and feedback together; it is
not an authorization boundary. The local product keeps its member entry flow. The shared template
exposes hosted-only controls only after the public Cloudflare session is confirmed.

## Context surface

- The selected logical account stays visible because D1 candidates, profiles, and Durable Object
  memory are isolated by that boundary; its raw account ID remains available in context details.
- The active country/persona is selected before generation. Audience, situation, tone, guidance,
  and reference IDs remain inspectable in one disclosure instead of occupying the default work path.
- Starter context is labeled honestly as a generic seed. Team operators can add, edit, or hide
  profiles; prior candidates retain immutable context snapshots.
- KR, JP, TW, US, DE, FR, and BR ship as starter markets with 16 total starter profiles. Adding
  another country remains data-driven through the packaged manifest and profiles. Generation
  fails visibly when country documents are missing instead of silently using the wrong country.

## Review and states

- Compact candidate rows show topic, source, country, date, status, and edit/delete controls. Detailed
  captions, context snapshots, and the decision journey stay on the review surface.
- One generation action creates four candidates grouped into two morning and two evening slots.
- A one-click approval records 5 points. Rejection expands inline to a 1–3 rating and explicit tags;
  three equal account/persona tags surface as a rule for the next generation.
- Any candidate, including `submitted`, can be edited or deleted in the hosted workspace. Editing
  invalidates approvals and image provenance and returns the candidate to caption review.
- Image approval ends at `submitted`; the UI never implies that Threads publishing occurred.
- Errors stay next to the action that failed, while changes already visible in the list use silent
  success plus the global status line.

## Responsive and accessibility constraints

- `html` and `body` use `overflow-x: clip`; layouts collapse at content-driven 40rem and 60rem
  breakpoints and remain usable at 320px.
- Interactive labels do not wrap; filter rails scroll inside their own bounds when needed.
- All inputs have visible labels, constant border width, a visible focus ring, and stable helper/error
  positions. Textareas resize vertically.
- Touch-reachable controls use at least a 44px block size, and status is never communicated by color
  alone.

## Progressive disclosure

- The default screen answers three questions only: which account, which persona, and what needs work.
- Account administration, full context provenance, feedback learning, manual candidate entry, and
  native capture mechanics are available but collapsed by default.
- Detailed Mac inventory and mutating controls stay behind `Mac 연결 관리`; the operator token is
  entered only inside that dialog and is cleared together with one-time code output on close.
- Threads profile identity, scopes, expiry, default selection, and the auto-publish toggle stay behind
  `Threads 연결 관리`. Candidate cards expose only a safe target label until operations are unlocked.
- Candidate rows avoid repeating caption and journey details already owned by the review tab.
