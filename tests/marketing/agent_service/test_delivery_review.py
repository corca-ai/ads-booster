"""Prepared review contracts do not grant or claim external execution."""

from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from PIL import Image

from ads_booster.contracts.creative_work import AssetParent, CreativeAsset, CreativeScope
from ads_booster.contracts.marketing_delivery import (
    ApprovalReference,
    DeliveryProposal,
    PaidBudgetTarget,
    PaidExecutionTarget,
    PostPublicationTarget,
    ProductionTarget,
    PublicationTarget,
    ReviewAsset,
)
from ads_booster.creative.creative_asset_verifier import CreativeAssetVerifier
from ads_booster.creative.creative_assets import SqliteCreativeAssetRepository
from ads_booster.delivery.delivery_review import DeliveryReviewStore

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 9, 7, tzinfo=UTC)
SCOPE = CreativeScope(workspace_id="trace", product_id="trace")


def _png(color: str = "white") -> bytes:
    buffer = io.BytesIO()
    with Image.new("RGB", (20, 40), color=color) as image:
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _assets(root: Path) -> SqliteCreativeAssetRepository:
    directory = root / "assets"
    directory.mkdir(exist_ok=True)
    repository = SqliteCreativeAssetRepository(root / "plans.db", directory)
    for name, color in [("source", "blue"), ("ja", "white")]:
        data = _png(color)
        _ = (directory / f"{name}.png").write_bytes(data)
        asset = CreativeAsset(
            asset_id=name,
            revision=1,
            scope=SCOPE,
            kind="edited_promotion",
            relative_path=f"{name}.png",
            sha256=hashlib.sha256(data).hexdigest(),
            parents=(
                AssetParent(
                    asset_id="source", revision=1, sha256=hashlib.sha256(_png("blue")).hexdigest()
                ),
            )
            if name == "ja"
            else (),
            source="Synthetic test PNG",
            use_terms="Test-owned",
            data_permission="synthetic",
            permission_evidence="Generated in fixture",
            origin="human_reported",
        )
        repository.add(asset, actor_scope=SCOPE)
    return repository


def _store(root: Path) -> DeliveryReviewStore:
    return DeliveryReviewStore(
        root / "plans.db", asset_verifier=CreativeAssetVerifier(_assets(root))
    )


def _publication() -> DeliveryProposal:
    return DeliveryProposal(
        proposal_id="publish",
        scope=SCOPE,
        run_id="small-work",
        rationale="Reviewed Japanese promotion",
        target=PublicationTarget(
            content_version=1,
            text="Synthetic Japanese caption",
            assets=(
                ReviewAsset(asset_id="ja", revision=1, sha256=hashlib.sha256(_png()).hexdigest()),
            ),
            account_id="japan-account",
            channel="threads",
            schedule_at=NOW + timedelta(minutes=2),
            conditions=("Human final visual approval required",),
            qa_summary="Human-reviewed fixture",
        ),
    )


def _approve(store: DeliveryReviewStore, proposal: DeliveryProposal, revision: int = 1) -> int:
    return store.review(
        SCOPE,
        proposal.proposal_id,
        expected_revision=revision,
        expected_target_sha256=proposal.target_sha256,
        reviewer_id="reviewer",
        reviewer_authorized=True,
        approved=True,
        expires_at=NOW + timedelta(minutes=5),
        now=NOW,
    ).revision


@pytest.mark.parametrize("legacy_campaign_id", [None, "historical-campaign"])
def test_exact_approval_change_invalidation_and_prepared_schedule(
    tmp_path: Path, legacy_campaign_id: str | None
) -> None:
    store = _store(tmp_path)
    proposal = _publication().model_copy(update={"d1_campaign_id": legacy_campaign_id})
    _ = store.prepare(proposal, actor_scope=SCOPE)
    restored = DeliveryReviewStore(store.database).get(SCOPE, proposal.proposal_id)
    assert restored is not None
    assert restored.proposal.model_dump_json() == proposal.model_dump_json()
    assert restored.proposal.target_sha256 == proposal.target_sha256
    with pytest.raises(ValueError, match="reviewer_not_authorized"):
        _ = store.review(
            SCOPE,
            proposal.proposal_id,
            expected_revision=1,
            expected_target_sha256=proposal.target_sha256,
            reviewer_id="intruder",
            reviewer_authorized=False,
            approved=True,
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )
    revision = _approve(store, proposal)
    planned = store.schedule(SCOPE, proposal.proposal_id, expected_revision=revision, now=NOW)
    assert planned.state == "scheduled_prepared"
    assert planned.external_execution_enabled is False
    changed = proposal.model_copy(
        update={
            "target": PublicationTarget.model_validate(
                {
                    **proposal.target.model_dump(),
                    "content_version": 2,
                    "account_id": "new-account",
                }
            )
        }
    )
    draft = store.prepare(changed, actor_scope=SCOPE, expected_revision=planned.revision)
    assert draft.approved_target_sha256 is None
    with pytest.raises(ValueError, match="target_changed"):
        _ = store.review(
            SCOPE,
            proposal.proposal_id,
            expected_revision=draft.revision,
            expected_target_sha256=proposal.target_sha256,
            reviewer_id="reviewer",
            reviewer_authorized=True,
            approved=True,
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )
    with pytest.raises(ValueError, match="current_approval_required"):
        _ = store.schedule(SCOPE, proposal.proposal_id, expected_revision=draft.revision, now=NOW)
    assert DeliveryReviewStore(tmp_path / "plans.db").get(SCOPE, "publish") == draft


def test_concurrent_review_one_wins_and_production_does_not_approve_publication(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    publication = _publication()
    production = DeliveryProposal(
        proposal_id="produce",
        scope=SCOPE,
        run_id="small-work",
        rationale="Only expand the margin",
        target=ProductionTarget(
            input_sha256="b" * 64,
            instructions="Expand top margin",
            preserve=("character",),
            change=("top margin",),
            max_cost_units=3,
        ),
    )
    _ = store.prepare(production, actor_scope=SCOPE)
    revision = _approve(store, production)
    with pytest.raises(ValueError, match="execution_review_required"):
        _ = store.schedule(SCOPE, production.proposal_id, expected_revision=revision, now=NOW)
    _ = store.prepare(publication, actor_scope=SCOPE)

    def review(_: int) -> str:
        try:
            _ = _approve(store, publication)
        except ValueError as error:
            return str(error)
        else:
            return "reviewed"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(review, range(2)))
    assert sorted(results) == ["delivery_revision_conflict", "reviewed"]


def test_reported_publication_requires_separate_change_review_and_no_fake_readback(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    proposal = _publication()
    _ = store.prepare(proposal, actor_scope=SCOPE)
    report = store.report_external(
        SCOPE,
        "publish",
        expected_revision=1,
        reference="Human reports a post URL",
        evidence_sha256="c" * 64,
    )
    assert report.observations[0].source == "human_reported"
    with pytest.raises(ValueError, match="readback_unavailable"):
        _ = store.refresh_readback(SCOPE, "publish", expected_revision=report.revision)
    with pytest.raises(ValueError, match="post_publication_change_requires_review"):
        _ = store.cancel(SCOPE, "publish", expected_revision=report.revision)
    amendment = DeliveryProposal(
        proposal_id="amend",
        scope=SCOPE,
        run_id="small-work",
        rationale="Correct Japanese title",
        target=PostPublicationTarget(
            publication=ApprovalReference(
                proposal_id="publish", target_sha256=proposal.target_sha256
            ),
            action="edit",
            draft="Corrected title",
            impact="Public wording changes",
            alternatives=("Leave unchanged with correction reply",),
        ),
    )
    draft = store.prepare(amendment, actor_scope=SCOPE)
    assert draft.state == "draft"
    _ = _approve(store, amendment)
    assert store.get(SCOPE, "publish") == report
    assert store.get(SCOPE.model_copy(update={"workspace_id": "other"}), "publish") is None


def test_paid_budget_and_execution_are_separate_and_revalidated(tmp_path: Path) -> None:
    store = _store(tmp_path)
    budget = DeliveryProposal(
        proposal_id="budget",
        scope=SCOPE,
        run_id="small-work",
        rationale="Prepare a bounded experiment",
        target=PaidBudgetTarget(
            account_id="ads-account", currency="JPY", max_minor_units=100, purpose="Test only"
        ),
    )
    _ = store.prepare(budget, actor_scope=SCOPE)
    execution = DeliveryProposal(
        proposal_id="paid-run",
        scope=SCOPE,
        run_id="small-work",
        rationale="Prepare creative experiment",
        target=PaidExecutionTarget(
            budget_approval=ApprovalReference(
                proposal_id="budget", target_sha256=budget.target_sha256
            ),
            account_id="ads-account",
            currency="JPY",
            spend_minor_units=80,
            content_sha256="d" * 64,
            audience="Japanese students",
            execution_conditions=("No live execution enabled",),
        ),
    )
    _ = store.prepare(execution, actor_scope=SCOPE)
    with pytest.raises(ValueError, match="current_approval_required"):
        _ = _approve(store, execution)
    budget_revision = _approve(store, budget)
    execution_revision = _approve(store, execution)
    _ = store.prepare(
        budget.model_copy(update={"rationale": "Budget reconsidered"}),
        actor_scope=SCOPE,
        expected_revision=budget_revision,
    )
    with pytest.raises(ValueError, match="current_approval_required"):
        _ = store.schedule(
            SCOPE, execution.proposal_id, expected_revision=execution_revision, now=NOW
        )


def test_expired_approval_and_cancelled_plan_cannot_prepare_execution(tmp_path: Path) -> None:
    store = _store(tmp_path)
    proposal = _publication()
    _ = store.prepare(proposal, actor_scope=SCOPE)
    revision = _approve(store, proposal)
    with pytest.raises(ValueError, match="current_approval_required"):
        _ = store.schedule(
            SCOPE, "publish", expected_revision=revision, now=NOW + timedelta(hours=1)
        )
    cancelled = store.cancel(SCOPE, "publish", expected_revision=revision)
    assert cancelled.state == "cancelled"
    assert cancelled.approved_target_sha256 is None
    with pytest.raises(ValueError, match="current_approval_required"):
        _ = store.schedule(SCOPE, "publish", expected_revision=cancelled.revision, now=NOW)


@pytest.mark.parametrize(
    ("account", "currency", "spend"),
    [("other", "JPY", 80), ("ads", "USD", 80), ("ads", "JPY", 101)],
)
def test_paid_approval_rejects_budget_scope_escape(
    tmp_path: Path, account: str, currency: str, spend: int
) -> None:
    store = _store(tmp_path)
    budget = DeliveryProposal(
        proposal_id="budget",
        scope=SCOPE,
        run_id="small-work",
        rationale="Bounded budget",
        target=PaidBudgetTarget(
            account_id="ads", currency="JPY", max_minor_units=100, purpose="Test only"
        ),
    )
    _ = store.prepare(budget, actor_scope=SCOPE)
    _ = _approve(store, budget)
    execution = DeliveryProposal(
        proposal_id="execute",
        scope=SCOPE,
        run_id="small-work",
        rationale="Scope escape fixture",
        target=PaidExecutionTarget(
            budget_approval=ApprovalReference(
                proposal_id="budget", target_sha256=budget.target_sha256
            ),
            account_id=account,
            currency=currency,
            spend_minor_units=spend,
            content_sha256="e" * 64,
            audience="Synthetic audience",
            execution_conditions=("Prepared only",),
        ),
    )
    _ = store.prepare(execution, actor_scope=SCOPE)
    with pytest.raises(ValueError, match="paid_budget_scope_mismatch"):
        _ = _approve(store, execution)


def test_publication_review_without_asset_verifier_fails_closed(tmp_path: Path) -> None:
    store = DeliveryReviewStore(tmp_path / "missing-assets.db")
    proposal = _publication()
    _ = store.prepare(proposal, actor_scope=SCOPE)
    with pytest.raises(ValueError, match="asset_verifier_required"):
        _ = _approve(store, proposal)


@pytest.mark.parametrize(
    ("boundary", "failure"),
    [("review", "missing"), ("review", "wrong_digest")]
    + [
        (boundary, failure)
        for boundary in ("review", "schedule")
        for failure in ("new_revision", "stale_parent", "own_bytes", "parent_bytes", "outside_root")
    ],
)
def test_asset_identity_and_every_ancestor_rechecked(
    tmp_path: Path, boundary: str, failure: str
) -> None:
    store = _store(tmp_path)
    repository = SqliteCreativeAssetRepository(tmp_path / "plans.db", tmp_path / "assets")
    proposal = _publication()
    if failure in {"missing", "wrong_digest"}:
        target = PublicationTarget.model_validate(
            {
                **proposal.target.model_dump(),
                "assets": [
                    {
                        "asset_id": "missing" if failure == "missing" else "ja",
                        "revision": 1,
                        "sha256": "f" * 64,
                    }
                ],
            }
        )
        proposal = proposal.model_copy(update={"target": target})
    _ = store.prepare(proposal, actor_scope=SCOPE)
    revision = _approve(store, proposal) if boundary == "schedule" else 1
    if failure in {"new_revision", "stale_parent"}:
        asset_id = "ja" if failure == "new_revision" else "source"
        original = repository.get(SCOPE, asset_id)
        assert original is not None
        repository.add(original.model_copy(update={"revision": 2}), actor_scope=SCOPE)
    elif failure in {"own_bytes", "parent_bytes"}:
        name = "ja" if failure == "own_bytes" else "source"
        _ = (tmp_path / "assets" / f"{name}.png").write_bytes(b"altered")
    elif failure == "outside_root":
        outside = tmp_path / "outside.png"
        _ = outside.write_bytes(_png())
        image = tmp_path / "assets" / "ja.png"
        image.unlink()
        image.symlink_to(outside)
    if boundary == "review":
        with pytest.raises(ValueError, match=r"creative_artifact|delivery_asset"):
            _ = _approve(store, proposal)
    else:
        with pytest.raises(ValueError, match=r"creative_artifact|delivery_asset"):
            _ = store.schedule(SCOPE, "publish", expected_revision=revision, now=NOW)


def test_production_source_assets_need_current_file_verification(tmp_path: Path) -> None:
    store = DeliveryReviewStore(tmp_path / "production.db")
    proposal = DeliveryProposal(
        proposal_id="production",
        scope=SCOPE,
        run_id="small-work",
        rationale="Preserve source",
        target=ProductionTarget(
            input_sha256="a" * 64,
            instructions="Extend whitespace",
            source_assets=(ReviewAsset(asset_id="missing", revision=1, sha256="a" * 64),),
            max_cost_units=1,
        ),
    )
    _ = store.prepare(proposal, actor_scope=SCOPE)
    with pytest.raises(ValueError, match="asset_verifier_required"):
        _ = _approve(store, proposal)


def test_paid_prepared_reservations_share_budget_and_cancel_releases(tmp_path: Path) -> None:
    store = _store(tmp_path)
    budget = DeliveryProposal(
        proposal_id="budget",
        scope=SCOPE,
        run_id="small-work",
        rationale="Bounded preparation",
        target=PaidBudgetTarget(
            account_id="ads", currency="JPY", max_minor_units=100, purpose="Test"
        ),
    )
    _ = store.prepare(budget, actor_scope=SCOPE)
    _ = _approve(store, budget)
    revisions: dict[str, int] = {}
    for name in ("one", "two"):
        execution = DeliveryProposal(
            proposal_id=name,
            scope=SCOPE,
            run_id="small-work",
            rationale="Prepared only",
            target=PaidExecutionTarget(
                budget_approval=ApprovalReference(
                    proposal_id="budget", target_sha256=budget.target_sha256
                ),
                account_id="ads",
                currency="JPY",
                spend_minor_units=80,
                content_sha256="a" * 64,
                audience="Synthetic students",
                execution_conditions=("No live execution",),
            ),
        )
        _ = store.prepare(execution, actor_scope=SCOPE)
        revisions[name] = _approve(store, execution)
    one = store.schedule(SCOPE, "one", expected_revision=revisions["one"], now=NOW)
    with pytest.raises(ValueError, match="prepared_budget_exceeded"):
        _ = store.schedule(SCOPE, "two", expected_revision=revisions["two"], now=NOW)
    _ = store.cancel(SCOPE, "one", expected_revision=one.revision)
    two = store.schedule(SCOPE, "two", expected_revision=revisions["two"], now=NOW)
    assert two.state == "scheduled_prepared"
    assert two.external_execution_enabled is False
