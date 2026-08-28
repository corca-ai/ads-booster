from __future__ import annotations


def codex_appium_prompt() -> str:
    return """Complete this Trace wallpaper job autonomously.

The current directory contains codex-appium-job.json with the complete non-secret goal, device,
plan, background, Appium endpoint, launch binding, export filenames, and available Python runtime.
Use the Appium, XCUITest, Simulator, and Trace installations already present on this Mac. Decide how
to inspect and operate the real UI, diagnose failures, revise your approach, and continue until the
goal is actually complete.

Do not install or upgrade software, use git, access non-loopback network, read credentials, or edit,
copy, fabricate, or replace the App Group export files. The completed outcome requires the requested
wallpaper to be saved, both native export files to be observed, every request-owned calendar to be
removed afterward, and the Appium session to be closed. Return only the requested JSON and report
status=completed only when all of those observable conditions hold."""
