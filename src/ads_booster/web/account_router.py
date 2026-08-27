"""Routes for the marketing accounts a workspace posts as.

Creation is deliberately plain: an account exists because a person opened a Threads
identity for it, so the service records one rather than inventing one. The status route is
separate from the edit route because promoting or retiring an account is a verdict about
its posting record, not a correction of its description, and the two want different
histories once posting results arrive.
"""

from collections.abc import Generator  # noqa: TC003
from contextlib import contextmanager
from typing import Annotated, Final

from fastapi import APIRouter, Depends, HTTPException, status

from ads_booster.web.auth import CurrentPrincipal, Principal  # noqa: TC001
from ads_booster.web.schemas import (
    MarketingAccountCreateRequest,
    MarketingAccountResponse,
    MarketingAccountStatusRequest,
    MarketingAccountUpdateRequest,
)
from ads_booster.workspace import (
    MarketingAccountCreate,
    MarketingAccountId,
    MarketingAccountRecord,
    MarketingAccountSettings,
    MarketingAccountWriter,
    RevisionConflictError,
    ScopedRecordNotFoundError,
)

_ACCOUNT_NOT_FOUND: Final = "marketing account not found"
_ACCOUNT_REVISION_CONFLICT: Final = "marketing account revision conflict"


def _response(record: MarketingAccountRecord) -> MarketingAccountResponse:
    return MarketingAccountResponse.of(record)


@contextmanager
def _mapped_errors() -> Generator[None]:
    """Translate the store's typed refusals into the HTTP answers the browser expects."""
    try:
        yield
    except ScopedRecordNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, _ACCOUNT_NOT_FOUND) from error
    except RevisionConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, _ACCOUNT_REVISION_CONFLICT) from error


def build_account_router(
    store: MarketingAccountWriter,
    current_principal: CurrentPrincipal,
) -> APIRouter:
    router = APIRouter(prefix="/api/accounts", tags=["accounts"])

    @router.get("", response_model=list[MarketingAccountResponse])
    def list_accounts(
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> list[MarketingAccountResponse]:
        return [_response(record) for record in store.list_accounts(principal.workspace_id)]

    @router.post("", response_model=MarketingAccountResponse, status_code=status.HTTP_201_CREATED)
    def create_account(
        payload: MarketingAccountCreateRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> MarketingAccountResponse:
        return _response(
            store.create_account(
                principal.workspace_id,
                MarketingAccountCreate(
                    country=payload.country,
                    identity=payload.identity,
                    schedule=payload.schedule,
                    status=payload.status,
                    note=payload.note,
                ),
            )
        )

    @router.get("/{account_id}", response_model=MarketingAccountResponse)
    def get_account(
        account_id: MarketingAccountId,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> MarketingAccountResponse:
        with _mapped_errors():
            return _response(store.get_account(principal.workspace_id, account_id))

    @router.put("/{account_id}", response_model=MarketingAccountResponse)
    def update_account(
        account_id: MarketingAccountId,
        payload: MarketingAccountUpdateRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> MarketingAccountResponse:
        with _mapped_errors():
            return _response(
                store.update_account(
                    principal.workspace_id,
                    account_id,
                    settings=MarketingAccountSettings(
                        identity=payload.identity,
                        schedule=payload.schedule,
                        note=payload.note,
                    ),
                    expected_revision=payload.expected_revision,
                )
            )

    @router.post("/{account_id}/status", response_model=MarketingAccountResponse)
    def set_account_status(
        account_id: MarketingAccountId,
        payload: MarketingAccountStatusRequest,
        principal: Annotated[Principal, Depends(current_principal)],
    ) -> MarketingAccountResponse:
        with _mapped_errors():
            return _response(
                store.set_account_status(
                    principal.workspace_id,
                    account_id,
                    status=payload.status,
                    expected_revision=payload.expected_revision,
                )
            )

    _ = (list_accounts, create_account, get_account, update_account, set_account_status)
    return router
