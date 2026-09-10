from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from ads_booster.knowledge.batch_runtime import CurationBatchRuntime
from ads_booster.knowledge.curation import CurationRunner
from ads_booster.knowledge.maintenance_jobs import CanonicalJobProcessor

if TYPE_CHECKING:
    from ads_booster.bootstrap.lifecycle import InstalledKnowledgeRuntime
    from ads_booster.knowledge.curation_runtime import CurationBatchDecisionProvider


def install_curation_provider(
    installed: InstalledKnowledgeRuntime, provider: CurationBatchDecisionProvider
) -> None:
    processor = installed.runtime.jobs.processor
    batches = installed.runtime.curation_batches
    assert isinstance(processor, CanonicalJobProcessor)
    assert isinstance(batches, CurationBatchRuntime)
    configured = replace(
        processor,
        curation=CurationRunner(replace(processor.curation.dependencies, provider=provider)),
    )
    installed.runtime.jobs.processor = configured
    batches.jobs = configured
