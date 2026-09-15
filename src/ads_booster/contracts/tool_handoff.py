from __future__ import annotations

from pydantic import Field

from ads_booster.contracts.models import ContractModel


class ToolInputHandoff(ContractModel):
    question: str = Field(min_length=1, max_length=4000)
    expected_outcome: str = Field(min_length=1, max_length=2000)
