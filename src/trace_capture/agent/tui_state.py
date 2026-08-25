from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class TuiOperation(StrEnum):
    READY = "READY"
    THINKING = "THINKING"
    AUTHENTICATING = "AUTHENTICATING"
    LOADING_MODELS = "LOADING MODELS"
    SELECTING_SESSION = "SELECTING SESSION"
    WAITING_FOR_APPROVAL = "WAITING FOR APPROVAL"
    COMPACTING_CONTEXT = "COMPACTING CONTEXT"
    FLUSHING_MEMORY = "FLUSHING MEMORY"
    RECOVERING_AFTER_OVERFLOW = "RECOVERING AFTER OVERFLOW"
    CANCELLING = "CANCELLING"
    ERROR = "ERROR"


_BUSY_OPERATIONS: Final[frozenset[TuiOperation]] = frozenset(
    {
        TuiOperation.THINKING,
        TuiOperation.AUTHENTICATING,
        TuiOperation.LOADING_MODELS,
        TuiOperation.SELECTING_SESSION,
        TuiOperation.WAITING_FOR_APPROVAL,
        TuiOperation.COMPACTING_CONTEXT,
        TuiOperation.FLUSHING_MEMORY,
        TuiOperation.RECOVERING_AFTER_OVERFLOW,
        TuiOperation.CANCELLING,
    }
)
_OPERATIONS_BY_LABEL: Final[dict[str, TuiOperation]] = {
    operation.value: operation for operation in TuiOperation
}


def operation_for_label(label: str) -> TuiOperation:
    return _OPERATIONS_BY_LABEL.get(label, TuiOperation.ERROR)


@dataclass(frozen=True, slots=True)
class TuiState:
    operation: TuiOperation = TuiOperation.READY
    detail: str = ""

    @property
    def busy(self) -> bool:
        return self.operation in _BUSY_OPERATIONS

    def with_status(self, label: str, detail: str = "") -> TuiState:
        return TuiState(operation_for_label(label), detail)
