"""Local preparation and exact reviews; never dispatch publication or paid execution."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING, Protocol, cast

from ads_booster.contracts.creative_work import CreativeScope
from ads_booster.contracts.marketing_delivery import (
    DeliveryObservation,
    DeliveryProposal,
    DeliveryReviewPacket,
    PaidBudgetTarget,
    PaidExecutionTarget,
    PostPublicationTarget,
    ProductionTarget,
    PublicationTarget,
    ReviewAsset,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime
    from pathlib import Path


_MAX_OBSERVATIONS = 32
_MAX_PREPARED_PAID = 256


class OwnerReadback(Protocol):
    """Host-injected trusted existing owner; user request bodies cannot implement this port."""

    def verify(self, proposal: DeliveryProposal) -> DeliveryObservation: ...


class AssetReviewVerifier(Protocol):
    """Host-issued current artifact-root, digest and lineage verification."""

    def verify(self, scope: CreativeScope, refs: tuple[ReviewAsset, ...]) -> None: ...


class DeliveryReviewStore:
    def __init__(
        self,
        database: Path,
        *,
        owner_readback: OwnerReadback | None = None,
        asset_verifier: AssetReviewVerifier | None = None,
    ) -> None:
        self.database: Path = database
        self.owner_readback: OwnerReadback | None = owner_readback
        self.asset_verifier: AssetReviewVerifier | None = asset_verifier
        with self._connect() as connection:
            _ = connection.execute("""CREATE TABLE IF NOT EXISTS delivery_review_versions (
                scope TEXT NOT NULL, proposal_id TEXT NOT NULL, revision INTEGER NOT NULL,
                packet TEXT NOT NULL, PRIMARY KEY(scope,proposal_id,revision))""")

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _scope(self, scope: CreativeScope) -> str:
        # Scope equality is applied in SQL before deserializing any other team's proposal.
        return scope.model_dump_json()

    def _get(
        self,
        connection: sqlite3.Connection,
        scope: CreativeScope,
        proposal_id: str,
    ) -> DeliveryReviewPacket | None:
        row = cast(
            "tuple[str] | None",
            connection.execute(
                """SELECT packet FROM delivery_review_versions WHERE scope=? AND proposal_id=?
            ORDER BY revision DESC LIMIT 1""",
                (self._scope(scope), proposal_id),
            ).fetchone(),
        )
        return DeliveryReviewPacket.model_validate_json(row[0]) if row else None

    def get(self, scope: CreativeScope, proposal_id: str) -> DeliveryReviewPacket | None:
        with self._connect() as connection:
            return self._get(connection, scope, proposal_id)

    def _required(
        self,
        connection: sqlite3.Connection,
        scope: CreativeScope,
        proposal_id: str,
        expected_revision: int,
    ) -> DeliveryReviewPacket:
        packet = self._get(connection, scope, proposal_id)
        if packet is None:
            raise ValueError("delivery_proposal_not_found")
        if packet.revision != expected_revision:
            raise ValueError("delivery_revision_conflict")
        return packet

    def _save(self, connection: sqlite3.Connection, packet: DeliveryReviewPacket) -> None:
        _ = DeliveryReviewPacket.model_validate_json(packet.model_dump_json())
        _ = connection.execute(
            "INSERT INTO delivery_review_versions VALUES(?,?,?,?)",
            (
                self._scope(packet.proposal.scope),
                packet.proposal.proposal_id,
                packet.revision,
                packet.model_dump_json(),
            ),
        )

    def prepare(
        self,
        proposal: DeliveryProposal,
        *,
        actor_scope: CreativeScope,
        expected_revision: int = 0,
    ) -> DeliveryReviewPacket:
        if actor_scope != proposal.scope:
            raise ValueError("delivery_scope_denied")
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            previous = self._get(connection, actor_scope, proposal.proposal_id)
            if (previous.revision if previous else 0) != expected_revision:
                raise ValueError("delivery_revision_conflict")
            if previous and previous.observations:
                raise ValueError("delivery_post_publication_change_requires_review")
            if previous and previous.proposal.target.kind != proposal.target.kind:
                raise ValueError("delivery_review_kind_immutable")
            packet = DeliveryReviewPacket(
                proposal=proposal, revision=expected_revision + 1, state="draft"
            )
            self._save(connection, packet)
            return packet

    def _dependencies(
        self,
        connection: sqlite3.Connection,
        proposal: DeliveryProposal,
        *,
        now: datetime,
    ) -> None:
        target = proposal.target
        refs = (
            target.assets
            if isinstance(target, PublicationTarget)
            else (target.source_assets if isinstance(target, ProductionTarget) else ())
        )
        if refs:
            if self.asset_verifier is None:
                raise ValueError("delivery_asset_verifier_required")
            self.asset_verifier.verify(proposal.scope, refs)
        if isinstance(target, PaidExecutionTarget):
            budget = self._get(connection, proposal.scope, target.budget_approval.proposal_id)
            if budget is None or not isinstance(budget.proposal.target, PaidBudgetTarget):
                raise ValueError("delivery_paid_budget_required")
            self._approved(budget, now=now)
            approved = budget.proposal.target
            if (
                budget.proposal.target_sha256 != target.budget_approval.target_sha256
                or approved.account_id != target.account_id
                or approved.currency != target.currency
                or target.spend_minor_units > approved.max_minor_units
            ):
                raise ValueError("delivery_paid_budget_scope_mismatch")
        if isinstance(target, PostPublicationTarget):
            original = self._get(connection, proposal.scope, target.publication.proposal_id)
            if (
                original is None
                or not isinstance(original.proposal.target, PublicationTarget)
                or original.proposal.target_sha256 != target.publication.target_sha256
                or not original.observations
            ):
                raise ValueError("delivery_publication_observation_required")

    def review(  # noqa: PLR0913 - exact trusted reviewer, version, target and expiry are distinct.
        self,
        scope: CreativeScope,
        proposal_id: str,
        *,
        expected_revision: int,
        expected_target_sha256: str,
        reviewer_id: str,
        reviewer_authorized: bool,
        approved: bool,
        expires_at: datetime,
        now: datetime,
    ) -> DeliveryReviewPacket:
        if not reviewer_authorized or scope.member_id is not None:
            raise ValueError("delivery_reviewer_not_authorized")
        if expires_at.tzinfo is None or now.tzinfo is None or expires_at <= now:
            raise ValueError("delivery_approval_expired")
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            packet = self._required(connection, scope, proposal_id, expected_revision)
            if packet.proposal.target_sha256 != expected_target_sha256:
                raise ValueError("delivery_approval_target_changed")
            if packet.state != "draft":
                raise ValueError("delivery_review_not_pending")
            if approved:
                self._dependencies(connection, packet.proposal, now=now)
            reviewed = packet.model_copy(
                update={
                    "revision": packet.revision + 1,
                    "state": "reviewed" if approved else "rejected",
                    "approved_target_sha256": expected_target_sha256 if approved else None,
                    "reviewer_id": reviewer_id,
                    "approval_expires_at": expires_at if approved else None,
                }
            )
            self._save(connection, reviewed)
            return reviewed

    def _approved(self, packet: DeliveryReviewPacket, *, now: datetime) -> None:
        if (
            packet.state not in {"reviewed", "scheduled_prepared"}
            or packet.approved_target_sha256 != packet.proposal.target_sha256
            or packet.approval_expires_at is None
            or packet.approval_expires_at <= now
        ):
            raise ValueError("delivery_current_approval_required")

    def schedule(
        self,
        scope: CreativeScope,
        proposal_id: str,
        *,
        expected_revision: int,
        now: datetime,
    ) -> DeliveryReviewPacket:
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            packet = self._required(connection, scope, proposal_id, expected_revision)
            if not isinstance(packet.proposal.target, (PublicationTarget, PaidExecutionTarget)):
                raise ValueError("delivery_execution_review_required")
            if packet.observations:
                raise ValueError("delivery_external_result_already_reported")
            self._approved(packet, now=now)
            self._dependencies(connection, packet.proposal, now=now)
            self._prepared_budget(connection, packet.proposal)
            result = packet.model_copy(
                update={"revision": packet.revision + 1, "state": "scheduled_prepared"}
            )
            self._save(connection, result)
            return result

    def _prepared_budget(self, connection: sqlite3.Connection, proposal: DeliveryProposal) -> None:
        """Reserve local prepared amounts atomically; never claim provider spend accounting."""
        target = proposal.target
        if not isinstance(target, PaidExecutionTarget):
            return
        budget = self._get(connection, proposal.scope, target.budget_approval.proposal_id)
        if budget is None or not isinstance(budget.proposal.target, PaidBudgetTarget):
            raise ValueError("delivery_paid_budget_required")
        rows = cast(
            "list[tuple[str]]",
            connection.execute(
                """
            SELECT packet FROM delivery_review_versions v
            WHERE scope=? AND json_extract(packet,'$.state')='scheduled_prepared'
              AND json_extract(packet,'$.proposal.target.kind')='paid_execution'
              AND revision=(SELECT MAX(revision) FROM delivery_review_versions head
                            WHERE head.scope=v.scope AND head.proposal_id=v.proposal_id)
            LIMIT ?
        """,
                (self._scope(proposal.scope), _MAX_PREPARED_PAID + 1),
            ).fetchall(),
        )
        if len(rows) > _MAX_PREPARED_PAID:
            raise ValueError("delivery_prepared_reservation_limit")
        reserved = 0
        for row in rows:
            other = DeliveryReviewPacket.model_validate_json(row[0]).proposal
            if (
                other.proposal_id != proposal.proposal_id
                and isinstance(other.target, PaidExecutionTarget)
                and other.target.budget_approval == target.budget_approval
                and other.target.account_id == target.account_id
                and other.target.currency == target.currency
            ):
                reserved += other.target.spend_minor_units
        if reserved + target.spend_minor_units > budget.proposal.target.max_minor_units:
            raise ValueError("delivery_prepared_budget_exceeded")

    def cancel(
        self,
        scope: CreativeScope,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> DeliveryReviewPacket:
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            packet = self._required(connection, scope, proposal_id, expected_revision)
            if packet.observations:
                raise ValueError("delivery_post_publication_change_requires_review")
            result = packet.model_copy(
                update={
                    "revision": packet.revision + 1,
                    "state": "cancelled",
                    "approved_target_sha256": None,
                }
            )
            self._save(connection, result)
            return result

    def report_external(
        self,
        scope: CreativeScope,
        proposal_id: str,
        *,
        expected_revision: int,
        reference: str,
        evidence_sha256: str,
    ) -> DeliveryReviewPacket:
        observation = DeliveryObservation(
            reference=reference, evidence_sha256=evidence_sha256, source="human_reported"
        )
        return self._observe(scope, proposal_id, expected_revision, observation)

    def refresh_readback(
        self,
        scope: CreativeScope,
        proposal_id: str,
        *,
        expected_revision: int,
    ) -> DeliveryReviewPacket:
        if self.owner_readback is None:
            raise ValueError("delivery_owner_readback_unavailable")
        packet = self.get(scope, proposal_id)
        if packet is None or packet.revision != expected_revision:
            raise ValueError("delivery_revision_conflict")
        observation = self.owner_readback.verify(packet.proposal)
        if observation.source != "owner_readback":
            raise ValueError("delivery_owner_readback_unverified")
        return self._observe(scope, proposal_id, expected_revision, observation)

    def _observe(
        self,
        scope: CreativeScope,
        proposal_id: str,
        revision: int,
        observation: DeliveryObservation,
    ) -> DeliveryReviewPacket:
        with self._connect() as connection:
            _ = connection.execute("BEGIN IMMEDIATE")
            packet = self._required(connection, scope, proposal_id, revision)
            if not isinstance(packet.proposal.target, (PublicationTarget, PaidExecutionTarget)):
                raise ValueError("delivery_external_observation_kind_invalid")
            if observation in packet.observations:
                return packet
            if len(packet.observations) >= _MAX_OBSERVATIONS:
                raise ValueError("delivery_observation_limit")
            result = packet.model_copy(
                update={
                    "revision": packet.revision + 1,
                    "observations": (*packet.observations, observation),
                }
            )
            self._save(connection, result)
            return result
