"""Assembling the marketing context corpus into candidate drafts.

The engine here reads the packaged context documents and a fresh sample of the country's
reference bodies, states the rules the corpus settled, and asks for one candidate per call.
It has no opinion about where the drafts go: the Mac worker sends them to the hosted
control plane, and anything else that wants candidates can ask the same way.
"""

from ads_booster.candidate_generation.context_source import (
    CONTEXT_DIR_ENVIRONMENT,
    REFERENCE_ROOT,
    REQUIRED_DOCUMENTS,
    CandidateContextSource,
    CandidateReferenceSource,
    ReferencePool,
    default_context_directory,
    reference_directory,
    reference_id,
)
from ads_booster.candidate_generation.draft_engine import (
    DEFAULT_FLOP_SAMPLES,
    DEFAULT_HIT_SAMPLES,
    DEFAULT_MAX_BATCH,
    DEFAULT_MAX_INSTRUCTION_CHARS,
    CandidateDraftBatch,
    CandidateDraftClient,
    CandidateDraftEngine,
    DraftedCandidate,
    WrittenTopics,
    assign_domains,
    default_domain_shuffle,
)
from ads_booster.candidate_generation.errors import (
    CandidateContextMissingError,
    CandidateFormatError,
    CandidateGenerationError,
    CandidateProviderError,
    CandidateReferencesMissingError,
)
from ads_booster.candidate_generation.instruction import (
    DEFAULT_COUNTRY,
    DEFAULT_LANGUAGE,
    CandidateAssignment,
    CaptionForm,
    assign_candidates,
    assign_caption_forms,
    assign_interests,
    assignment_section,
    build_instruction,
    build_retry_instruction,
)
from ads_booster.candidate_generation.models import (
    CandidateContextBundle,
    CandidateDocument,
    CandidateDraft,
)
from ads_booster.candidate_generation.parsing import CANDIDATES_KEY, parse_candidate_drafts

__all__ = [
    "CANDIDATES_KEY",
    "CONTEXT_DIR_ENVIRONMENT",
    "DEFAULT_COUNTRY",
    "DEFAULT_FLOP_SAMPLES",
    "DEFAULT_HIT_SAMPLES",
    "DEFAULT_LANGUAGE",
    "DEFAULT_MAX_BATCH",
    "DEFAULT_MAX_INSTRUCTION_CHARS",
    "REFERENCE_ROOT",
    "REQUIRED_DOCUMENTS",
    "CandidateAssignment",
    "CandidateContextBundle",
    "CandidateContextMissingError",
    "CandidateContextSource",
    "CandidateDocument",
    "CandidateDraft",
    "CandidateDraftBatch",
    "CandidateDraftClient",
    "CandidateDraftEngine",
    "CandidateFormatError",
    "CandidateGenerationError",
    "CandidateProviderError",
    "CandidateReferenceSource",
    "CandidateReferencesMissingError",
    "CaptionForm",
    "DraftedCandidate",
    "ReferencePool",
    "WrittenTopics",
    "assign_candidates",
    "assign_caption_forms",
    "assign_domains",
    "assign_interests",
    "assignment_section",
    "build_instruction",
    "build_retry_instruction",
    "default_context_directory",
    "default_domain_shuffle",
    "parse_candidate_drafts",
    "reference_directory",
    "reference_id",
]
