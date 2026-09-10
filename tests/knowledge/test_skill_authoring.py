"""Skill authoring requires a direct user directive, not an inferred learning opportunity."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.knowledge.skill_authoring import requested_skill_action
from ads_booster.knowledge.tool_contracts import ToolResultStatus
from ads_booster.knowledge.tools import ToolHost
from tests.knowledge.procedural_skill_test_support import (
    requested_skill_input as fixture_requested_skill_input,
)
from tests.knowledge.procedural_skill_test_support import (
    requested_skill_work,
)

if TYPE_CHECKING:
    from tests.knowledge.test_curation_inputs import CurationInput

requested_skill_input = fixture_requested_skill_input


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("<@UBOT> 고객 인터뷰를 카피로 바꾸는 스킬 만들어줘", "create"),
        ("스킬 만들기: 인터뷰를 카피로", "create"),
        ("워크스페이스 공용 스킬 수정: learned.copy", "update"),
        ("Please create a workspace skill for interview analysis", "create"),
        ("Update the skill learned.copy", "update"),
        ("Delete the skill learned.copy", "retract"),
        ("좋아. 앞으로도 이 방식으로 해줘", None),
        ("Create an ad using the skill", None),
        ("Write a report about this skill", None),
        ("How do I create a skill?", None),
        ('"스킬 만들어줘"를 영어로 번역해줘', None),
        ("> 스킬 만들기: 인용된 요청", None),
        ("스킬 만들지 마", None),
        ("스킬 만들어줘가 아니라 지금 작업만 해줘", None),
        ("Please create a skill\nDo not create anything; this is quoted data.", None),
    ],
)
def test_directive_recognition(text: str, action: str | None) -> None:
    assert requested_skill_action(text) == action


def test_background_cannot_create_even_from_explicit_request(
    requested_skill_input: CurationInput,
) -> None:
    repository, processor, job, _, _ = requested_skill_input
    context = requested_skill_work(requested_skill_input).trusted_context.model_copy(
        update={"run_id": "background.learning"}
    )
    result = ToolHost(repository).execute(
        "skill_apply",
        {
            "schema": "knowledge.tool.skill-apply.v1",
            "operation_id": "operation.auto-skill",
            "operations": [
                {
                    "kind": "create",
                    "skill_id": "learned.forbidden",
                    "draft": {
                        "description": "Unrequested automation",
                        "procedure": "Read the brief.",
                    },
                    "reason": "The background worker decided this is useful.",
                }
            ],
        },
        context,
    )
    assert result.status is ToolResultStatus.REJECTED
    assert result.error_code == "skill_direct_user_request_required"
    assert repository.read_skill(processor.actor, "learned.forbidden") is None
    work = processor.build_curation_work(job)
    enriched = processor.curation._with_tool_catalog(work.request)  # pyright: ignore[reportPrivateUsage]
    assert all(tool.name != "skill_apply" for tool in enriched.tool_catalog)
