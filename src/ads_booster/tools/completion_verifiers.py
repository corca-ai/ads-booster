from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ads_booster.contracts.creative_work import CreativeAsset
from ads_booster.contracts.marketing_delivery import ReviewAsset
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.creative.creative_asset_verifier import CreativeAssetVerifier
from ads_booster.tools.completion_registry import (
    CompletionProofRegistry,
    ProofIdentity,
    ProofRegistration,
)
from ads_booster.tools.github_issues import REPOSITORY, IssueInput
from ads_booster.tools.image_generation import png_dimensions, read_artifact

if TYPE_CHECKING:
    from ads_booster.agent.service.completion_evidence import BoundCompletionEvidence
    from ads_booster.contracts.agent_run import AgentRun
    from ads_booster.tools.completion_registry import CompletionArtifactOwners


@dataclass(frozen=True, slots=True)
class GeneratedImageProof:
    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        _ = run
        root = owners.image_root
        digest = bound.output.get("artifact_sha256")
        if root is None or not isinstance(digest, str):
            return False
        if bound.output.get("invocation_sha256") != bound.receipt.invocation_sha256:
            return False
        width, height = png_dimensions(read_artifact(root, digest))
        return (
            bound.output.get("media_type") == "image/png"
            and bound.output.get("width") == width
            and bound.output.get("height") == height
        )


@dataclass(frozen=True, slots=True)
class ManagedImageProof:
    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        repository, scope_for_run = owners.assets, owners.scope_for_run
        if repository is None or scope_for_run is None:
            return False
        asset = CreativeAsset.model_validate(bound.output.get("asset"))
        scope = scope_for_run(run)
        if asset.scope != scope or asset.origin != "worker_receipt":
            return False
        CreativeAssetVerifier(repository).verify(
            scope,
            (ReviewAsset(asset_id=asset.asset_id, revision=asset.revision, sha256=asset.sha256),),
        )
        current = repository.get(scope, asset.asset_id, asset.revision)
        if current != asset:
            return False
        _ = png_dimensions((repository.artifact_root / asset.relative_path).read_bytes())
        return True


@dataclass(frozen=True, slots=True)
class GitHubIssueProof:
    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        _ = run, owners
        _ = IssueInput.model_validate(bound.invocation.input)
        number = bound.output.get("number")
        return (
            type(number) is int
            and number > 0
            and bound.output.get("repository") == REPOSITORY
            and bound.output.get("url") == f"https://github.com/{REPOSITORY}/issues/{number}"
        )


def configured_proof_registry() -> CompletionProofRegistry:
    return CompletionProofRegistry(
        (
            ProofRegistration(
                ProofIdentity(
                    "creative.image.generate",
                    "codex.image_generation",
                    "codex.image_generation",
                    EffectClass.LOCAL_ARTIFACT,
                ),
                GeneratedImageProof(),
            ),
            *(
                ProofRegistration(
                    ProofIdentity(
                        capability,
                        "ads_booster.creative_image_edit",
                        "codex-image-edit",
                        EffectClass.LOCAL_ARTIFACT,
                    ),
                    ManagedImageProof(),
                )
                for capability in ("creative.image.edit", "creative.image.localize")
            ),
            ProofRegistration(
                ProofIdentity(
                    "github.issue.create",
                    "github.issues",
                    "github.issues",
                    EffectClass.EXTERNAL,
                ),
                GitHubIssueProof(),
                installation_id="configured:github",
            ),
        )
    )
