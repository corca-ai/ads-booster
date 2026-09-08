from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, ClassVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import PydanticCustomError

Identifier = Annotated[
    str,
    Field(min_length=1, max_length=80, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$"),
]
CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
Locale = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$")]


def require_safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        error_type = "unsafe_relative_path"
        error_message = "file paths must stay inside their declared root"
        raise PydanticCustomError(
            error_type,
            error_message,
        )
    return value


RelativePath = Annotated[
    str,
    Field(min_length=1, max_length=240),
    AfterValidator(require_safe_relative_path),
]


class ContractModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True, extra="forbid")


Sha256Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
