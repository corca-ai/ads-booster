# Testing and Verification

Status: Active
Last reviewed: 2026-08-30

## Focused checks

Choose the boundary that changed. Source tests are not installed-worker or hosted-runtime proof.

| Change | Command |
| --- | --- |
| v2 job and Appium adapter | `uv run pytest tests/capture/test_codex_appium_capture.py tests/capture/test_codex_appium_handshake.py tests/capture/test_appium_editor_verifier.py tests/providers/test_codex_cli_handshake.py tests/capture/test_appium_endpoint.py tests/capture/test_readiness.py` |
| background and native validation | `uv run pytest tests/marketing/test_background.py tests/marketing/test_native_capture.py` |
| hosted Codex candidate generation | `uv run pytest tests/marketing/test_hosted_generation.py tests/providers/test_codex_cli_generation.py` |
| Codex ImageGen lock-screen edit | `uv run pytest tests/capture/test_imagegen_editor.py tests/providers/test_codex_cli_imagegen.py` |
| inbox/barrier/recovery | `uv run pytest tests/marketing/test_worker_loop.py` |
| update and installation guard | `uv run pytest tests/marketing/test_worker_update.py tests/cli/test_installer.py` |
| CLI surface | `uv run pytest tests/cli/test_cli_compatibility.py`; `uv run trace-marketing --help`; `uv run trace-marketing worker --help` |

For changed Python paths, run the matching scoped Ruff, formatter, BasedPyright, and
`git diff --check`. Do not run the full suite or repository-wide static checks unless requested.

## Installed-worker proof

Use the managed executable, not `uv run`:

```bash
"$HOME/.local/share/trace-marketing/current/bin/trace-marketing" version --json
"$HOME/.local/share/trace-marketing/current/bin/trace-marketing" worker doctor
"$HOME/.local/share/trace-marketing/current/bin/trace-marketing" worker status
```

For a real job record task ID, callback receipt, PNG path/SHA-256, native manifest, and resulting
`image_awaiting_review` state. `doctor` proves prerequisites only. A manifest proves bindings
only. Human review alone passes visual correctness.

A regression test for a post-barrier defect must assert `unknown_side_effect` and no automatic
native re-execution. Never place credentials, raw Codex output, or private user data in evidence.
