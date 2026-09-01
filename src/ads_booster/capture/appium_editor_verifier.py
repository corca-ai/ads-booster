from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from html import unescape
from typing import TYPE_CHECKING, Final, Protocol
from urllib.parse import quote

import httpx2
from pydantic import ValidationError

from ads_booster.capture.appium_codex_validation import rendered_titles_are_credible
from ads_booster.capture.appium_endpoint import validate_appium_server_url
from ads_booster.capture.capture_safety import CaptureAdapterError
from ads_booster.capture.simctl_command import CommandRunner, SubprocessCommandRunner
from ads_booster.contracts.models import ContractModel
from ads_booster.transport.http import create_http_client

if TYPE_CHECKING:
    from ads_booster.capture.capture_safety import CaptureControl
    from ads_booster.providers.codex_cli import CodexAppiumReadyState


class AppiumEditorVerifier(Protocol):
    def verify(
        self,
        appium_server: str,
        ready: CodexAppiumReadyState,
        expected_titles: tuple[str, ...],
        control: CaptureControl,
    ) -> bool: ...

    def verify_process_binding(
        self,
        appium_server: str,
        session_id: str,
        expected_arguments: tuple[str, ...],
        control: CaptureControl,
    ) -> bool: ...


class _AppiumSourceResponse(ContractModel):
    value: str


_HTTP_OK: Final = 200
_HTTP_MULTIPLE_CHOICES: Final = 300
_SOURCE_READ_TIMEOUT_SECONDS: Final = 10.0
_WALLPAPER_EDITOR_IDENTIFIER: Final = "lockScreenWallpaperSave"
_APPLICATION_PROCESS_ID: Final = re.compile(r'processId="([1-9]\d*)"')
_PS_EXECUTABLE: Final = "/bin/ps"


@dataclass(frozen=True, slots=True)
class DefaultAppiumEditorVerifier:
    runner: CommandRunner = field(default_factory=SubprocessCommandRunner)

    def verify(
        self,
        appium_server: str,
        ready: CodexAppiumReadyState,
        expected_titles: tuple[str, ...],
        control: CaptureControl,
    ) -> bool:
        visible_source = self._read_source(appium_server, ready.session_id, control)
        if visible_source is None:
            return False
        if _WALLPAPER_EDITOR_IDENTIFIER not in visible_source:
            return False
        # Check the claim, not the request. Trace folds the rows that do not fit into a
        # "+N" badge, so looking for all twenty requested rows in the source fails on a
        # screen that is built correctly. What can be checked is that every row Codex says
        # it can see is really there, which is the claim the Save gate rests on.
        if not all(title in visible_source for title in ready.rendered_trace_item_titles):
            return False
        return rendered_titles_are_credible(ready.rendered_trace_item_titles, expected_titles)

    def verify_process_binding(
        self,
        appium_server: str,
        session_id: str,
        expected_arguments: tuple[str, ...],
        control: CaptureControl,
    ) -> bool:
        visible_source = self._read_source(appium_server, session_id, control)
        if visible_source is None:
            return False
        matched = _APPLICATION_PROCESS_ID.search(visible_source)
        if matched is None:
            return False
        timeout = min(control.remaining_seconds(), _SOURCE_READ_TIMEOUT_SECONDS)
        try:
            completed = self.runner.run(
                (_PS_EXECUTABLE, "-p", matched.group(1), "-ww", "-o", "command="),
                timeout,
            )
        except CaptureAdapterError:
            return False
        control.checkpoint()
        if completed.returncode != 0:
            return False
        try:
            process_command = tuple(shlex.split(completed.stdout))
        except ValueError:
            return False
        return _contains_contiguous_arguments(process_command, expected_arguments)

    @staticmethod
    def _read_source(
        appium_server: str,
        session_id: str,
        control: CaptureControl,
    ) -> str | None:
        server = validate_appium_server_url(appium_server).rstrip("/")
        timeout = min(control.remaining_seconds(), _SOURCE_READ_TIMEOUT_SECONDS)
        source_url = f"{server}/session/{quote(session_id, safe='')}/source"
        try:
            with create_http_client(read_timeout=timeout) as http:
                response = http.get(source_url, {})
        except httpx2.HTTPError:
            return None
        if not _HTTP_OK <= response.status_code < _HTTP_MULTIPLE_CHOICES:
            return None
        try:
            source = _AppiumSourceResponse.model_validate_json(response.content).value
        except ValidationError:
            return None
        return unescape(source)


DEFAULT_APPIUM_EDITOR_VERIFIER: Final = DefaultAppiumEditorVerifier()


def _contains_contiguous_arguments(
    process_command: tuple[str, ...],
    expected_arguments: tuple[str, ...],
) -> bool:
    if not expected_arguments or len(process_command) < len(expected_arguments):
        return False
    width = len(expected_arguments)
    return any(
        process_command[index : index + width] == expected_arguments
        for index in range(len(process_command) - width + 1)
    )


__all__ = [
    "DEFAULT_APPIUM_EDITOR_VERIFIER",
    "AppiumEditorVerifier",
    "DefaultAppiumEditorVerifier",
]
