"""Bounded provider diagnostics shared by deferred workers and their consumers."""

from __future__ import annotations

from ads_booster.providers.codex_cli import CodexCliError

FAILURE_TEXT = {
    "codex_image_edit_no_generation": (
        "모델 응답이 끝났지만 이미지 생성 완료 이벤트가 없어 제작 결과를 확인하지 못했습니다."
    ),
    "codex_image_edit_preparation_failed": (
        "이미지 준비 명령이 실패했고 생성 완료 이벤트 없이 작업이 종료됐습니다."
    ),
    "codex_sandbox_launcher_unavailable": (
        "실행 환경의 내부 실행기를 시작하지 못해 제작이 중단됐습니다."
    ),
    "codex_image_edit_generation_event_required": (
        "이미지 생성 완료 기록을 받지 못해 제작 결과를 확인하지 못했습니다."
    ),
    "codex_image_edit_outcome_unknown": (
        "이미지 도구 응답이 중단되어 제작 결과를 확인하지 못했습니다."
    ),
    "provider_outcome_unknown": "제작 도구 실행이 중단되어 결과를 확인하지 못했습니다.",
    "trace_post_recovery_proof_invalid": (
        "저장된 제작 증거와 결과 파일이 일치하지 않아 복구하지 못했습니다."
    ),
}


def provider_failure_code(error: Exception) -> str:
    code = str(error) if isinstance(error, CodexCliError) else ""
    return code if code in FAILURE_TEXT else "provider_outcome_unknown"
