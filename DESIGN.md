# Trace Workspace Design System

## Design read

This is a local team workspace with a single high-stakes entry action. The surface is a restrained
dark operations product, not a dashboard that exposes every capability before membership is known.

`DESIGN_VARIANCE: 4`, `MOTION_INTENSITY: 2`, `VISUAL_DENSITY: 4`.

## Tokens

All color, type, spacing, radius, border, focus, and motion tokens are declared in
`src/trace_capture/web/static/design-tokens.css`. UI code uses those tokens rather than adding
raw color or spacing values.

- Canvas and raised surfaces establish hierarchy with tonal steps and `--color-border`.
- `--color-accent` is reserved for the selected tab, focus, and the one primary action.
- `--radius-sm` applies to controls; `--radius-md` applies to bounded entry and work surfaces.
- Motion is limited to the declared transform, color, and opacity transitions. Reduced motion
  removes non-essential transitions.

## Layout

The web workspace has two mutually exclusive states.

1. `workspace-entry` is a dedicated, centered entry screen. It contains only one composite access
   ID field, validation, the privacy note, and the `입장` action.
2. `workspace-main` appears only after server-side membership authentication. It is one bounded
   content canvas with a compact tab bar and the single primary action `새 자료 만들기`.

There is no persistent sidebar, context rail, brand mark, or workspace breadcrumb. Each tab is a
separate work surface, so the initial screen does not try to display preparation, queue, review,
and chat at once. At narrow widths both the entry form and work toolbar become one column.

Campaign creation asks only for campaign name, persona, promotion material, and optional image
references. The local capture target is fixed to the available iPhone 17 Pro on iOS 26.5, with its
configured Simulator UDID kept as an implementation default. The reference date is generated from
the current UTC time when the campaign is submitted.

## Primitives

### Member Access

- States: entry, focus, validation error, connecting, connected, unavailable.
- Inputs always have visible labels. The composite access ID is a password field and is never stored
  by the static UI.
- On success, the entry screen hides before the authenticated work canvas is revealed.

### Workspace Toolbar

- Contains semantic tabs, a text live status, and `새 자료 만들기`.
- The tab bar is keyboard-operable with the existing arrow-key behavior.
- It never repeats the login form or identity breadcrumb.

### Buttons and Feedback

- Buttons have primary, secondary, and quiet variants with visible focus and press feedback.
- Validation and request errors use a nearby `role="alert"`; live request progress remains text in
  a polite live region. Color never carries state alone.

### Member Invitation

- The owner-only `팀원 초대` action opens the existing command-dialog surface.
- The form has a visible member-name label, nearby validation feedback, and a polite result region.
- A generated member access ID is displayed once and can be copied without exposing the shared
  workspace access ID.

## Accessibility constraints

- Keyboard users can skip to the entry heading before login and to the work canvas afterward.
- Required fields report missing values inline and focus the first invalid field.
- Controls retain a focus ring from `--focus-width` and `--color-accent`.
- Long IDs and names must wrap without horizontal overflow at 375px.
