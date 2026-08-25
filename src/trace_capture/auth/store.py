from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import override

from pydantic import ValidationError

from trace_capture.auth.models import OAuthCredential


class AuthStoreError(RuntimeError):
    message: str

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    @override
    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class AuthStore:
    path: Path

    @classmethod
    def default(cls) -> AuthStore:
        configured_home = os.environ.get("TRACE_AGENT_HOME")
        home = Path(configured_home) if configured_home else Path.home() / ".trace-agent"
        return cls(path=home / "auth.json")

    def load(self) -> OAuthCredential | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError) as error:
            msg = f"OAuth credential store could not be read: {self.path}"
            raise AuthStoreError(msg) from error
        try:
            return OAuthCredential.model_validate_json(raw)
        except ValidationError as error:
            msg = f"OAuth credential store is invalid: {self.path}"
            raise AuthStoreError(msg) from error

    def save(self, credential: OAuthCredential) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix="auth.",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary_path.chmod(0o600)
                _ = temporary.write(credential.model_dump_json())
                temporary.flush()
                os.fsync(temporary.fileno())
            _ = temporary_path.replace(self.path)
            self.path.chmod(0o600)
        except OSError as error:
            if temporary_path is not None:
                _ = temporary_path.unlink(missing_ok=True)
            msg = f"OAuth credential store could not be written: {self.path}"
            raise AuthStoreError(msg) from error

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as error:
            msg = f"OAuth credential store could not be cleared: {self.path}"
            raise AuthStoreError(msg) from error
