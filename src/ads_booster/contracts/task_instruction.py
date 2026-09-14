from typing import Annotated

from pydantic import Field

from ads_booster.contracts.agent_run import BoundedId  # noqa: TC001
from ads_booster.contracts.models import ContractModel


class TaskInstruction(ContractModel):
    source_event_id: BoundedId
    text: Annotated[str, Field(min_length=1, max_length=20_000)]
