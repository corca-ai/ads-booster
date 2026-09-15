from __future__ import annotations

from typing import Final

OAUTH_CALLBACK_PATH: Final = "/integrations/threads/callback"
DEAUTHORIZE_CALLBACK_PATH: Final = "/integrations/threads/deauthorize"
DATA_DELETION_CALLBACK_PATH: Final = "/integrations/threads/data-deletion"
DATA_DELETION_STATUS_PREFIX: Final = DATA_DELETION_CALLBACK_PATH + "/"


def threads_callback_urls(public_origin: str) -> tuple[str, str, str]:
    origin = public_origin.rstrip("/")
    return (
        origin + OAUTH_CALLBACK_PATH,
        origin + DEAUTHORIZE_CALLBACK_PATH,
        origin + DATA_DELETION_CALLBACK_PATH,
    )


__all__ = [
    "DATA_DELETION_CALLBACK_PATH",
    "DATA_DELETION_STATUS_PREFIX",
    "DEAUTHORIZE_CALLBACK_PATH",
    "OAUTH_CALLBACK_PATH",
    "threads_callback_urls",
]
