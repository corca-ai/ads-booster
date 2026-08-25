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

The authenticated work surface has five stable regions.

1. `workspace-toolbar` states the product and shows one live status line.
2. `pipeline-summary` exposes counts for caption review, image work, and publication-ready results.
3. The two-tab rail separates candidate preparation from human review.
4. `generation-workbench` places the selected context and the generation action side by side on wide
   screens and in a single reading order on narrow screens.
5. The candidate list can be filtered by operational state without hiding the canonical total.

The Cloudflare build removes the entry form and opens directly into one public account scope. The
local product keeps its member entry flow. The shared template exposes hosted-only context controls
only after the public Cloudflare session is confirmed.

## Context surface

- The account ID is visible because D1 candidates, profiles, and Durable Object memory are isolated
  by that account boundary.
- The active country/persona is selected before generation. Audience, situation, tone, guidance,
  and reference IDs remain visible instead of being hidden in a prompt.
- Starter context is labeled honestly as a generic seed. Team operators can add, edit, or hide
  profiles; prior candidates retain immutable context snapshots.
- Adding another country is data-driven through the packaged manifest and profiles. Generation
  fails visibly when country documents are missing instead of silently using the wrong country.

## Review and states

- Candidate cards show source, country, context snapshot, date, status, and the three-step journey.
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
