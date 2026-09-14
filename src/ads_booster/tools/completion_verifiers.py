from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ads_booster.contracts.agent_run import contract_sha256
from ads_booster.contracts.creative_work import CreativeAsset
from ads_booster.contracts.marketing_delivery import ReviewAsset
from ads_booster.contracts.tool_capability import EffectClass
from ads_booster.contracts.trace_post import TracePostSuccess
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
class TracePostProof:
    """Verify all six current Trace-post assets through their canonical owner."""

    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        repository, scope_for_run = owners.assets, owners.scope_for_run
        if repository is None or scope_for_run is None:
            return False
        result = TracePostSuccess.model_validate(bound.output)
        scope = scope_for_run(run)
        references = tuple(
            ReviewAsset(
                asset_id=item.asset.asset_id,
                revision=item.asset.revision,
                sha256=item.asset.sha256,
            )
            for item in result.assets
        )
        CreativeAssetVerifier(repository).verify(scope, references)
        for reference in references:
            asset = repository.get(scope, reference.asset_id, reference.revision)
            if asset is None or asset.origin != "worker_receipt":
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


@dataclass(frozen=True, slots=True)
class SlackDeliveryProof:
    """Verify the configured Slack owner's successful message readback."""

    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        _ = run
        text = bound.invocation.input.get("text")
        channel = bound.output.get("channel")
        timestamp = bound.output.get("ts")
        expected_channel = owners.slack_channel_id
        channel_id = channel if isinstance(channel, str) else None
        timestamp_id = timestamp if isinstance(timestamp, str) else None
        if (
            not _receipt_is_bound(bound)
            or not _nonempty_string(text)
            or bound.output.get("ok") is not True
            or not _nonempty_string(channel_id)
            or expected_channel is None
            or channel_id != expected_channel
            or not _nonempty_string(timestamp_id)
        ):
            return False
        message = bound.output.get("message")
        return message is None or _slack_message_matches(message, channel_id, timestamp_id, text)


@dataclass(frozen=True, slots=True)
class NotionDailyProof:
    """Verify the configured Notion owner's created-page readback."""

    def verify(
        self, run: AgentRun, bound: BoundCompletionEvidence, owners: CompletionArtifactOwners
    ) -> bool:
        _ = run
        title = bound.invocation.input.get("title")
        content = bound.invocation.input.get("content")
        page_id = bound.output.get("id")
        page_url = bound.output.get("url")
        if (
            not _receipt_is_bound(bound)
            or not _nonempty_string(title)
            or not _nonempty_string(content)
            or not _nonempty_string(page_id)
            or not _notion_url(page_url)
            or owners.notion_parent_page_id is None
        ):
            return False
        if bound.output.get("object") not in {None, "page"}:
            return False
        parent = bound.output.get("parent")
        if not _notion_parent_matches(parent, owners.notion_parent_page_id):
            return False
        properties = bound.output.get("properties")
        return _notion_title_matches(properties, title)


def _receipt_is_bound(bound: BoundCompletionEvidence) -> bool:
    return (
        bound.receipt.approval_sha256 is not None
        and bound.receipt.invocation_sha256 == contract_sha256(bound.invocation)
    )


def _nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _slack_message_matches(
    value: object, channel: str | None, timestamp: str | None, text: object
) -> bool:
    if not isinstance(value, dict):
        return False
    message = cast("dict[str, object]", value)
    return (
        message.get("channel") == channel
        and message.get("ts") == timestamp
        and message.get("text") == text
    )


def _notion_url(value: object) -> bool:
    return isinstance(value, str) and value.startswith(
        ("https://notion.so/", "https://www.notion.so/")
    )


def _notion_parent_matches(value: object, expected: str) -> bool:
    if not isinstance(value, dict):
        return False
    parent = cast("dict[str, object]", value)
    if parent.get("type") != "page_id":
        return False
    page_id = parent.get("page_id")
    return isinstance(page_id, str) and bool(page_id.strip()) and page_id == expected


def _notion_title_matches(value: object, title: object) -> bool:  # noqa: PLR0911 - structural JSON guard.
    if not isinstance(value, dict):
        return False
    properties = cast("dict[str, object]", value)
    title_property = properties.get("title")
    if not isinstance(title_property, dict):
        return False
    title_values = cast("dict[str, object]", title_property).get("title")
    if not isinstance(title_values, list) or not title_values:
        return False
    title_items = cast("list[object]", title_values)
    first = title_items[0]
    if not isinstance(first, dict):
        return False
    title_value = cast("dict[str, object]", first)
    plain_text = title_value.get("plain_text")
    if isinstance(plain_text, str):
        return plain_text == title
    text_value = title_value.get("text")
    if not isinstance(text_value, dict):
        return False
    return cast("dict[str, object]", text_value).get("content") == title


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
                    "creative.trace_post",
                    "ads_booster.agent.service.trace_post",
                    "codex-trace-post",
                    EffectClass.LOCAL_ARTIFACT,
                ),
                TracePostProof(),
                installation_id="installed:trace-post",
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
            ProofRegistration(
                ProofIdentity(
                    "deliver.slack",
                    "slack.delivery",
                    "slack.chat_post_message",
                    EffectClass.EXTERNAL,
                ),
                SlackDeliveryProof(),
                installation_id="configured:slack",
            ),
            ProofRegistration(
                ProofIdentity(
                    "store.notion.daily",
                    "notion.daily_marketing_archive",
                    "notion.pages_create",
                    EffectClass.EXTERNAL,
                ),
                NotionDailyProof(),
                installation_id="configured:notion",
            ),
        )
    )
