# Failure-Handling Principles

Treat failures, bugs, test failures, and review findings as signals of structural problems rather
than isolated symptoms. Handle each failure in the following order.

## 1. Identify the direct cause

- Find and fix the direct cause of the current failure.
- Do not use a temporary workaround that only hides the symptom.

## 2. Search for the same pattern

- Search other code, modules, and paths with the same structure for the same defect.
- After fixing one instance, inspect its sibling cases.

## 3. Inspect the structure that permits recurrence

Determine why the failure could happen again. Check whether:

- responsibility or ownership is unclear;
- shared logic is duplicated across owners;
- callers must repeat validation;
- an interface or abstraction permits invalid use; or
- tests, types, lint rules, CI, or a shared abstraction can prevent recurrence.

Prefer removing the structure that produces repeated failures over fixing each instance separately.

## 4. Record the disposition

For every discovered issue, state one of the following in the result or follow-up work:

- fix it now;
- track it as follow-up work; or
- record why it will not be fixed.

## Before completion

Do not stop merely because the direct failure is fixed. Confirm that:

- the same issue does not exist elsewhere;
- similar fixes are not already repeated across the codebase;
- compile-time checks, tests, types, shared abstractions, or validation can prevent recurrence; and
- the change did not leave the faulty owner or structure intact while only treating a symptom.
