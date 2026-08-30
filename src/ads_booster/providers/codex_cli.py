from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from ads_booster.transport.json_types import JsonObject, JsonValue

if TYPE_CHECKING:
    from collections.abc import Callable

_DEFAULT_TIMEOUT_SECONDS: Final = 180.0
_JSON_OBJECT: TypeAdapter[JsonObject] = TypeAdapter(JsonObject)
_APPIUM_RECEIPT_NAME: Final = "codex-appium-invocation.json"
_GENERATION_RECEIPT_NAME: Final = "codex-generation-invocation.json"
_IMAGE_EDIT_RECEIPT_NAME: Final = "codex-imagegen-invocation.json"
_APPIUM_READY_NAME: Final = "codex-appium-ready.json"
_APPIUM_READY_VERIFIED_NAME: Final = "codex-appium-ready-verified.json"
_APPIUM_SAVED_NAME: Final = "codex-appium-saved.json"
_APPIUM_COLLECTED_NAME: Final = "codex-appium-collected.json"
_APPIUM_PROFILE: Final = "trace-appium"
_APPIUM_ALREADY_INVOKED: Final = "codex_appium_job_already_invoked"
_APPIUM_RECEIPT_UNAVAILABLE: Final = "codex_appium_job_receipt_unavailable"
_GENERATION_ALREADY_INVOKED: Final = "codex_generation_job_already_invoked"
_GENERATION_RECEIPT_UNAVAILABLE: Final = "codex_generation_job_receipt_unavailable"
_IMAGE_EDIT_ALREADY_INVOKED: Final = "codex_image_edit_job_already_invoked"
_IMAGE_EDIT_RECEIPT_UNAVAILABLE: Final = "codex_image_edit_job_receipt_unavailable"
_APPIUM_MARKER_LIMIT_BYTES: Final = 64 * 1024
_APPIUM_MARKER_POLL_SECONDS: Final = 0.01
_APPIUM_READY_ATTEMPTS: Final = 2
_PRIVATE_FILE_MODE: Final = 0o600
_APPIUM_COLLECTED_UNAVAILABLE: Final = "codex_appium_job_collected_marker_unavailable"
_APPIUM_READY_VERIFIED_UNAVAILABLE: Final = "codex_appium_job_ready_verified_marker_unavailable"
_APPIUM_CONFIG: Final = (
    "features.network_proxy=true",
    'default_permissions="trace-appium"',
    'permissions.trace-appium.extends=":workspace"',
    "permissions.trace-appium.network.enabled=true",
    'permissions.trace-appium.network.mode="full"',
    "permissions.trace-appium.network.allow_local_binding=false",
    'permissions.trace-appium.network.domains={"127.0.0.1"="allow",localhost="allow","::1"="allow"}',
)


def _strict_output_schema(schema: JsonObject) -> JsonObject:
    strict_schema = deepcopy(schema)
    _require_schema_properties(strict_schema)
    return strict_schema


def _require_schema_properties(value: JsonValue) -> None:
    match value:
        case dict() as item:
            properties = item.get("properties")
            if isinstance(properties, dict):
                item["required"] = list(properties)
            for nested in item.values():
                _require_schema_properties(nested)
        case list() as items:
            for item in items:
                _require_schema_properties(item)
        case _:
            return


def _write_private_json(path: Path, payload: JsonObject) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
        os.link(temporary, path, follow_symlinks=False)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _replace_private_json(path: Path, payload: JsonObject) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, _PRIVATE_FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
        _ = temporary.replace(path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


class CodexCliError(RuntimeError):
    """A sanitized failure at the official Codex CLI process boundary."""


class CodexAppiumSavedState(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trace.codex-appium-saved.v1"] = Field(alias="schema")
    session_id: str = Field(min_length=1, max_length=200)
    created_calendar_titles: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = (
        Field(min_length=1, max_length=8)
    )


class CodexAppiumReadyState(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["trace.codex-appium-ready.v1"] = Field(alias="schema")
    session_id: str = Field(min_length=1, max_length=200)
    created_calendar_titles: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = (
        Field(min_length=1, max_length=8)
    )
    rendered_trace_item_titles: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = (
        Field(max_length=8)
    )


@dataclass(frozen=True, slots=True)
class CodexAppiumJobCallbacks:
    on_ready: Callable[[CodexAppiumReadyState], bool]
    on_saved: Callable[[CodexAppiumSavedState], bool]


@dataclass(frozen=True, slots=True)
class _StructuredTurn:
    prompt: str
    schema: JsonObject
    workspace: Path
    timeout_seconds: float
    error_prefix: str


@dataclass(frozen=True, slots=True)
class CodexCli:
    executable: Path
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    model: str | None = None

    def run_appium_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
        callbacks: CodexAppiumJobCallbacks,
    ) -> JsonObject:
        self._record_appium_invocation(workspace)
        return self._run_appium_structured(
            _StructuredTurn(
                prompt=prompt,
                schema=schema,
                workspace=workspace,
                timeout_seconds=timeout_seconds,
                error_prefix="codex_appium_job",
            ),
            callbacks,
        )

    def run_generation_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        self._record_generation_invocation(workspace)
        turn = _StructuredTurn(
            prompt=prompt,
            schema=schema,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
            error_prefix="codex_generation_job",
        )
        if not turn.workspace.is_dir():
            message = f"{turn.error_prefix}_workspace_unavailable"
            raise CodexCliError(message)
        with tempfile.TemporaryDirectory(prefix="trace-codex-") as directory:
            root = Path(directory)
            schema_path = root / "output.schema.json"
            output_path = root / "output.json"
            _ = schema_path.write_text(
                json.dumps(
                    _strict_output_schema(turn.schema), ensure_ascii=False, separators=(",", ":")
                ),
                encoding="utf-8",
            )
            command = self._structured_command(turn, schema_path, output_path)
            try:
                completed = subprocess.run(  # noqa: S603
                    command,
                    input=turn.prompt,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=turn.timeout_seconds,
                )
            except OSError as error:
                message = f"{turn.error_prefix}_unavailable"
                raise CodexCliError(message) from error
            except subprocess.TimeoutExpired as error:
                message = f"{turn.error_prefix}_timed_out"
                raise CodexCliError(message) from error
            if completed.returncode != 0:
                message = f"{turn.error_prefix}_failed:{completed.returncode}"
                raise CodexCliError(message)
            return self._read_structured_output(turn, output_path)

    def run_image_edit_job(
        self,
        prompt: str,
        schema: JsonObject,
        *,
        image: Path,
        workspace: Path,
        timeout_seconds: float,
    ) -> JsonObject:
        """Run one structured Codex image-edit turn with the supplied source image."""
        self._record_image_edit_invocation(workspace)
        turn = _StructuredTurn(
            prompt=prompt,
            schema=schema,
            workspace=workspace,
            timeout_seconds=timeout_seconds,
            error_prefix="codex_image_edit_job",
        )
        if not turn.workspace.is_dir():
            message = f"{turn.error_prefix}_workspace_unavailable"
            raise CodexCliError(message)
        if not image.is_file():
            message = f"{turn.error_prefix}_image_unavailable"
            raise CodexCliError(message)
        with tempfile.TemporaryDirectory(prefix="trace-codex-image-") as directory:
            root = Path(directory)
            schema_path = root / "output.schema.json"
            output_path = root / "output.json"
            _ = schema_path.write_text(
                json.dumps(
                    _strict_output_schema(turn.schema), ensure_ascii=False, separators=(",", ":")
                ),
                encoding="utf-8",
            )
            command = self._image_edit_command(turn, schema_path, output_path, image)
            try:
                completed = subprocess.run(  # noqa: S603
                    command,
                    input=turn.prompt,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=turn.timeout_seconds,
                )
            except OSError as error:
                message = f"{turn.error_prefix}_unavailable"
                raise CodexCliError(message) from error
            except subprocess.TimeoutExpired as error:
                message = f"{turn.error_prefix}_timed_out"
                raise CodexCliError(message) from error
            if completed.returncode != 0:
                message = f"{turn.error_prefix}_failed:{completed.returncode}"
                raise CodexCliError(message)
            return self._read_structured_output(turn, output_path)

    @staticmethod
    def _record_appium_invocation(workspace: Path) -> None:
        receipt = workspace / _APPIUM_RECEIPT_NAME
        payload: JsonObject = {
            "schema": "trace.codex-appium-invocation.v1",
            "profile": _APPIUM_PROFILE,
            "invocation_count": 1,
        }
        try:
            _write_private_json(receipt, payload)
        except FileExistsError as error:
            raise CodexCliError(_APPIUM_ALREADY_INVOKED) from error
        except OSError as error:
            raise CodexCliError(_APPIUM_RECEIPT_UNAVAILABLE) from error

    @staticmethod
    def _record_generation_invocation(workspace: Path) -> None:
        receipt = workspace / _GENERATION_RECEIPT_NAME
        payload: JsonObject = {
            "schema": "trace.codex-generation-invocation.v1",
            "invocation_count": 1,
        }
        try:
            _write_private_json(receipt, payload)
        except FileExistsError as error:
            raise CodexCliError(_GENERATION_ALREADY_INVOKED) from error
        except OSError as error:
            raise CodexCliError(_GENERATION_RECEIPT_UNAVAILABLE) from error

    @staticmethod
    def _record_image_edit_invocation(workspace: Path) -> None:
        receipt = workspace / _IMAGE_EDIT_RECEIPT_NAME
        payload: JsonObject = {
            "schema": "trace.codex-imagegen-invocation.v1",
            "invocation_count": 1,
        }
        try:
            _write_private_json(receipt, payload)
        except FileExistsError as error:
            raise CodexCliError(_IMAGE_EDIT_ALREADY_INVOKED) from error
        except OSError as error:
            raise CodexCliError(_IMAGE_EDIT_RECEIPT_UNAVAILABLE) from error

    def _run_appium_structured(
        self,
        turn: _StructuredTurn,
        callbacks: CodexAppiumJobCallbacks,
    ) -> JsonObject:
        if not turn.workspace.is_dir():
            message = f"{turn.error_prefix}_workspace_unavailable"
            raise CodexCliError(message)
        with tempfile.TemporaryDirectory(prefix="trace-codex-") as directory:
            root = Path(directory)
            schema_path = root / "output.schema.json"
            output_path = root / "output.json"
            _ = schema_path.write_text(
                json.dumps(turn.schema, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            command = self._structured_command(turn, schema_path, output_path)
            for config in _APPIUM_CONFIG:
                command.extend(("-c", config))
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="trace-codex") as executor:
                future = executor.submit(
                    subprocess.run,
                    command,
                    input=turn.prompt,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=turn.timeout_seconds,
                )
                completed = self._coordinate_appium_handshake(turn, future, callbacks)
            if completed.returncode != 0:
                message = f"{turn.error_prefix}_failed:{completed.returncode}"
                raise CodexCliError(message)
            return self._read_structured_output(turn, output_path)

    def _structured_command(
        self,
        turn: _StructuredTurn,
        schema_path: Path,
        output_path: Path,
    ) -> list[str]:
        command = [str(self.executable), "exec"]
        command.extend(
            (
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--cd",
                str(turn.workspace.resolve()),
            )
        )
        if self.model is not None:
            command.extend(("--model", self.model))
        command.append("-")
        return command

    def _image_edit_command(
        self,
        turn: _StructuredTurn,
        schema_path: Path,
        output_path: Path,
        image: Path,
    ) -> list[str]:
        command = [
            str(self.executable),
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--image",
            str(image.resolve()),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
            "--cd",
            str(turn.workspace.resolve()),
        ]
        if self.model is not None:
            command.extend(("--model", self.model))
        command.append("-")
        return command

    @staticmethod
    def _read_structured_output(turn: _StructuredTurn, output_path: Path) -> JsonObject:
        try:
            return _JSON_OBJECT.validate_json(output_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as error:
            message = f"{turn.error_prefix}_output_invalid"
            raise CodexCliError(message) from error

    def _coordinate_appium_handshake(
        self,
        turn: _StructuredTurn,
        future: Future[subprocess.CompletedProcess[str]],
        callbacks: CodexAppiumJobCallbacks,
    ) -> subprocess.CompletedProcess[str]:
        ready: CodexAppiumReadyState | None = None
        for attempt in range(1, _APPIUM_READY_ATTEMPTS + 1):
            ready = self._wait_for_ready_marker(turn, future, previous=ready)
            self._require_running(future, f"{turn.error_prefix}_exited_before_ready_ack")
            ready_verified = callbacks.on_ready(ready)
            self._require_running(future, f"{turn.error_prefix}_exited_before_ready_ack")
            retry_allowed = not ready_verified and attempt < _APPIUM_READY_ATTEMPTS
            self._write_ready_verified_marker(
                turn.workspace,
                ready,
                ready_verified,
                attempt=attempt,
                retry_allowed=retry_allowed,
            )
            if ready_verified:
                self._coordinate_saved_handshake(turn, future, callbacks, ready)
                break
            if not retry_allowed:
                break
        return self._completed_process(turn, future)

    def _coordinate_saved_handshake(
        self,
        turn: _StructuredTurn,
        future: Future[subprocess.CompletedProcess[str]],
        callbacks: CodexAppiumJobCallbacks,
        ready: CodexAppiumReadyState,
    ) -> None:
        saved = self._wait_for_saved_marker(turn, future)
        if not self._saved_matches_ready(saved, ready):
            message = f"{turn.error_prefix}_saved_marker_mismatch"
            raise CodexCliError(message)
        self._require_running(future, f"{turn.error_prefix}_exited_before_collection")
        collection_succeeded = callbacks.on_saved(saved)
        self._require_running(
            future,
            f"{turn.error_prefix}_exited_before_collection_ack",
        )
        self._write_collected_marker(turn.workspace, saved, collection_succeeded)

    @staticmethod
    def _require_running(
        future: Future[subprocess.CompletedProcess[str]],
        message: str,
    ) -> None:
        if future.done():
            raise CodexCliError(message)

    def _wait_for_ready_marker(
        self,
        turn: _StructuredTurn,
        future: Future[subprocess.CompletedProcess[str]],
        previous: CodexAppiumReadyState | None = None,
    ) -> CodexAppiumReadyState:
        ready_path = turn.workspace / _APPIUM_READY_NAME
        while True:
            try:
                ready = self._read_ready_marker(ready_path, turn.error_prefix)
                if previous is None or ready != previous:
                    return ready
            except FileNotFoundError:
                pass
            if future.done():
                completed = self._completed_process(turn, future)
                if completed.returncode != 0:
                    message = f"{turn.error_prefix}_failed:{completed.returncode}"
                    raise CodexCliError(message) from None
                message = f"{turn.error_prefix}_ready_marker_missing"
                raise CodexCliError(message) from None
            time.sleep(_APPIUM_MARKER_POLL_SECONDS)

    @staticmethod
    def _read_ready_marker(path: Path, error_prefix: str) -> CodexAppiumReadyState:
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
                or metadata.st_size > _APPIUM_MARKER_LIMIT_BYTES
            ):
                message = f"{error_prefix}_ready_marker_invalid"
                raise CodexCliError(message)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                return CodexAppiumReadyState.model_validate_json(stream.read())
        except FileNotFoundError:
            raise
        except (OSError, UnicodeError, ValidationError) as error:
            message = f"{error_prefix}_ready_marker_invalid"
            raise CodexCliError(message) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _wait_for_saved_marker(
        self,
        turn: _StructuredTurn,
        future: Future[subprocess.CompletedProcess[str]],
    ) -> CodexAppiumSavedState:
        saved_path = turn.workspace / _APPIUM_SAVED_NAME
        while True:
            try:
                return self._read_saved_marker(saved_path, turn.error_prefix)
            except FileNotFoundError:
                if future.done():
                    completed = self._completed_process(turn, future)
                    if completed.returncode != 0:
                        message = f"{turn.error_prefix}_failed:{completed.returncode}"
                        raise CodexCliError(message) from None
                    message = f"{turn.error_prefix}_saved_marker_missing"
                    raise CodexCliError(message) from None
                time.sleep(_APPIUM_MARKER_POLL_SECONDS)

    @staticmethod
    def _read_saved_marker(path: Path, error_prefix: str) -> CodexAppiumSavedState:
        descriptor = -1
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE
                or metadata.st_size > _APPIUM_MARKER_LIMIT_BYTES
            ):
                message = f"{error_prefix}_saved_marker_invalid"
                raise CodexCliError(message)
            with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
                descriptor = -1
                return CodexAppiumSavedState.model_validate_json(stream.read())
        except FileNotFoundError:
            raise
        except (OSError, UnicodeError, ValidationError) as error:
            message = f"{error_prefix}_saved_marker_invalid"
            raise CodexCliError(message) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _write_collected_marker(
        workspace: Path,
        saved: CodexAppiumSavedState,
        collection_succeeded: bool,
    ) -> None:
        path = workspace / _APPIUM_COLLECTED_NAME
        payload: JsonObject = {
            "schema": "trace.codex-appium-collected.v1",
            "session_id": saved.session_id,
            "created_calendar_titles": list(saved.created_calendar_titles),
            "collection_succeeded": collection_succeeded,
        }
        try:
            _write_private_json(path, payload)
        except OSError as error:
            raise CodexCliError(_APPIUM_COLLECTED_UNAVAILABLE) from error

    @staticmethod
    def _write_ready_verified_marker(
        workspace: Path,
        ready: CodexAppiumReadyState,
        ready_verified: bool,
        *,
        attempt: int,
        retry_allowed: bool,
    ) -> None:
        path = workspace / _APPIUM_READY_VERIFIED_NAME
        payload: JsonObject = {
            "schema": "trace.codex-appium-ready-verified.v1",
            "session_id": ready.session_id,
            "created_calendar_titles": list(ready.created_calendar_titles),
            "rendered_trace_item_titles": list(ready.rendered_trace_item_titles),
            "ready_verified": ready_verified,
            "attempt": attempt,
            "retry_allowed": retry_allowed,
            "failure_code": None if ready_verified else "ready_verification_failed",
        }
        try:
            _replace_private_json(path, payload)
        except OSError as error:
            raise CodexCliError(_APPIUM_READY_VERIFIED_UNAVAILABLE) from error

    @staticmethod
    def _saved_matches_ready(
        saved: CodexAppiumSavedState,
        ready: CodexAppiumReadyState,
    ) -> bool:
        return (
            saved.session_id == ready.session_id
            and saved.created_calendar_titles == ready.created_calendar_titles
        )

    @staticmethod
    def _completed_process(
        turn: _StructuredTurn,
        future: Future[subprocess.CompletedProcess[str]],
    ) -> subprocess.CompletedProcess[str]:
        try:
            return future.result()
        except OSError as error:
            message = f"{turn.error_prefix}_unavailable"
            raise CodexCliError(message) from error
        except subprocess.TimeoutExpired as error:
            message = f"{turn.error_prefix}_timed_out"
            raise CodexCliError(message) from error


def resolve_codex_executable() -> Path | None:
    """Resolve an explicit operator override or the current user's PATH entry."""
    configured = os.environ.get("TRACE_CODEX_BIN")
    resolved = shutil.which(configured or "codex")
    return None if resolved is None else Path(resolved).expanduser().resolve()
