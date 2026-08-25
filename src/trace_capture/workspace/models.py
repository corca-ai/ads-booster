from __future__ import annotations

from enum import StrEnum, unique
from pathlib import PurePosixPath
from typing import Annotated, ClassVar, NewType

from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from pydantic_core import PydanticCustomError

from trace_capture.transport.json_types import JsonObject
from trace_capture.transport.json_types import JsonObject as _JsonObject

WorkspaceId = NewType("WorkspaceId", str)
MemberId = NewType("MemberId", str)
ContextId = NewType("ContextId", str)
AssetId = NewType("AssetId", str)
PrivateSessionId = NewType("PrivateSessionId", str)


class FrozenModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)


@unique
class ContextKind(StrEnum):
    PERSONA = "persona"
    PROMOTION = "promotion"
    REFERENCE = "reference"
    RULE = "rule"


class WorkspaceRecord(FrozenModel):
    workspace_id: WorkspaceId
    name: str = Field(min_length=1, max_length=80)
    code_version: int = Field(ge=1)
    created_at: float
    updated_at: float


class ProvisionedWorkspace(FrozenModel):
    workspace: WorkspaceRecord
    access_code: str


class MemberRecord(FrozenModel):
    workspace_id: WorkspaceId
    member_id: MemberId
    display_name: str = Field(min_length=1, max_length=80)
    code_version: int = Field(ge=1)
    created_at: float
    updated_at: float


class ProvisionedMember(FrozenModel):
    member: MemberRecord
    invite_code: str


class ContextCreate(FrozenModel):
    kind: ContextKind
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=100_000)


class ContextRecord(FrozenModel):
    workspace_id: WorkspaceId
    context_id: ContextId
    kind: ContextKind
    title: str
    body: str
    revision: int = Field(ge=1)
    created_at: float
    updated_at: float


def _require_asset_relative_path(value: str) -> str:
    error_code = "unsafe_asset_path"
    error_message = "asset paths must be normalized relative paths below assets/"
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != value.strip()
        or "\\" in value
        or not value.startswith("assets/")
        or value != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PydanticCustomError(
            error_code,
            error_message,
        )
    return value


AssetRelativePath = Annotated[
    str,
    Field(min_length=1, max_length=1024),
    AfterValidator(_require_asset_relative_path),
]


class AssetCreate(FrozenModel):
    context_id: ContextId | None = None
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=120)
    relative_path: AssetRelativePath
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(ge=0)


class AssetRecord(FrozenModel):
    workspace_id: WorkspaceId
    asset_id: AssetId
    context_id: ContextId | None
    filename: str
    media_type: str
    relative_path: str
    sha256: str
    size_bytes: int
    created_at: float


class PrivateSessionCreate(FrozenModel):
    title: str = Field(min_length=1, max_length=80)
    history: tuple[JsonObject, ...]


class PrivateSessionRecord(FrozenModel):
    workspace_id: WorkspaceId
    member_id: MemberId
    session_id: PrivateSessionId
    title: str
    history: tuple[JsonObject, ...]
    revision: int = Field(ge=1)
    created_at: float
    updated_at: float


_ = PrivateSessionCreate.model_rebuild(_types_namespace={"JsonObject": _JsonObject})
_ = PrivateSessionRecord.model_rebuild(_types_namespace={"JsonObject": _JsonObject})
