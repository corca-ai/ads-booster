"""The only place candidate generation names the durable Agent kernel.

There are three coupling points to the execution kernel and each gets one module:
`candidate_batch.py` hands a candidate batch to an Agent run, `background_seam.py` puts
the judged background behind the Trace connector's fetcher seam, and `image_stage.py`
triggers native composition and carries a human image decision back to the run.

Nothing outside this package imports `ads_booster.agent.runs` or
`ads_booster.connectors`; `tests/architecture/test_kernel_isolation.py` enforces that. The
execution kernel is expected to be replaced, and these three modules are the blast radius.
"""

from ads_booster.candidate_generation.kernel.background_seam import (
    build_judged_codex_trace_runner,
    build_judged_trace_runner,
    judged_background_fetchers,
)
from ads_booster.candidate_generation.kernel.candidate_batch import (
    CandidateAgent,
    CandidateAgentPort,
    CandidateGenerator,
    build_kernel_candidate_generator,
)
from ads_booster.candidate_generation.kernel.image_stage import (
    AgentRunImageReview,
    CandidateImageRunner,
    build_image_review,
)

__all__ = [
    "AgentRunImageReview",
    "CandidateAgent",
    "CandidateAgentPort",
    "CandidateGenerator",
    "CandidateImageRunner",
    "build_image_review",
    "build_judged_codex_trace_runner",
    "build_judged_trace_runner",
    "build_kernel_candidate_generator",
    "judged_background_fetchers",
]
