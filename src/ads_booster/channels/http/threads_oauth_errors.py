from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ads_booster.providers.threads_api import ThreadsApiError

TEST_INVITE_REQUIRED: Final = 1349245
TOKEN_INVALID: Final = 190

if TYPE_CHECKING:
    from ads_booster.transport.json_types import JsonObject


def oauth_error_body(error: ThreadsApiError | ValueError | OSError) -> JsonObject:
    code = error.code if isinstance(error, ThreadsApiError) else None
    message = str(error).lower()
    if code == TEST_INVITE_REQUIRED or "not accepted the invite to test" in message:
        return {
            "error": "threads_test_invite_required",
            "message": """이 Threads 계정의 앱 테스트 초대가 수락되지 않았습니다.
Meta Developers에서 Threads 테스터로 등록한 뒤, 해당 Threads 계정의
설정 → 계정 → 웹사이트 권한 → 초대에서 수락하고 새 연결 링크를 요청하세요.""",
        }
    if code == TOKEN_INVALID or "session key invalid" in message:
        return {
            "error": "threads_token_rejected",
            "message": """Meta가 인증 토큰을 거절했습니다. 개발 중인 앱이라면 로그인한 계정의
Threads 테스트 계정 등록과 초대 수락 여부를 확인하세요.
등록된 계정도 세션 만료·권한 철회로 거절될 수 있습니다. 새 연결 링크로 인증하세요.""",
        }
    if "scope_missing" in message:
        return {
            "error": "threads_permissions_missing",
            "message": "필요한 Threads 권한이 없습니다. 새 연결 링크에서 권한을 승인하세요.",
        }
    if "state" in message:
        return {
            "error": "threads_oauth_state_invalid",
            "message": "링크가 만료·사용되었거나 유효하지 않습니다. 새 연결 링크를 요청하세요.",
        }
    return {
        "error": "threads_connection_failed",
        "message": """Threads 계정 연결에 실패했습니다. 새 연결 링크로 다시 인증하세요.
반복되면 관리자에게 연결 설정과 서버 상태 확인을 요청하세요.""",
    }
