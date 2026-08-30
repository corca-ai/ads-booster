"""Marketing workspace record shapes, without the store that used to persist them.

Only the model definitions live here. The SQLite workspace, its stores and the local web
surface were removed with the move to the Codex execution path and are out of scope; what
generation actually needs from that package is the vocabulary — which domains exist, what a
lock-screen image is made of, and what one batch recorded about itself while it ran.
"""

from ads_booster.workspace.models import (
    PERSONA_DOMAIN_LABELS,
    CandidateAccountBrief,
    CandidateBackgroundIntent,
    CandidateBackgroundMood,
    CandidateBackgroundSearchQuery,
    CandidateBackgroundSubject,
    CandidateCaption,
    CandidateContextDocument,
    CandidateContextRelativePath,
    CandidateCountry,
    CandidateDeviceTime,
    CandidateGenerationModel,
    CandidateGenerationProvenance,
    CandidateHistoryEntry,
    CandidateHypothesis,
    CandidateImageInputs,
    CandidateLanguage,
    CandidatePersonaDomain,
    CandidatePostingSlot,
    CandidatePrinciple,
    CandidateReference,
    CandidateScheduleItem,
    CandidateShootingOrder,
    CandidateTopic,
)

__all__ = [
    "PERSONA_DOMAIN_LABELS",
    "CandidateAccountBrief",
    "CandidateBackgroundIntent",
    "CandidateBackgroundMood",
    "CandidateBackgroundSearchQuery",
    "CandidateBackgroundSubject",
    "CandidateCaption",
    "CandidateContextDocument",
    "CandidateContextRelativePath",
    "CandidateCountry",
    "CandidateDeviceTime",
    "CandidateGenerationModel",
    "CandidateGenerationProvenance",
    "CandidateHistoryEntry",
    "CandidateHypothesis",
    "CandidateImageInputs",
    "CandidateLanguage",
    "CandidatePersonaDomain",
    "CandidatePostingSlot",
    "CandidatePrinciple",
    "CandidateReference",
    "CandidateScheduleItem",
    "CandidateShootingOrder",
    "CandidateTopic",
]
