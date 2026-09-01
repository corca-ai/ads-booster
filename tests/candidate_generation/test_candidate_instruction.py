from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ads_booster.candidate_generation import (
    REQUIRED_DOCUMENTS,
    CandidateContextSource,
    CandidateDocument,
    CandidateFormatError,
    assign_candidates,
    build_instruction,
    parse_candidate_drafts,
)
from ads_booster.candidate_generation.instruction import CandidateAssignment  # noqa: TC001
from ads_booster.workspace import (
    CandidateAccountBrief,
    CandidateBackgroundSubject,
    CandidateHistoryEntry,
    CandidatePersonaDomain,
)
from tests.candidate_generation._corpus import write_context

if TYPE_CHECKING:
    from pathlib import Path

    from ads_booster.candidate_generation import CandidateContextBundle
    from ads_booster.transport.json_types import JsonObject, JsonValue


def _bundle(tmp_path: Path) -> CandidateContextBundle:
    return CandidateContextSource(write_context(tmp_path), required=REQUIRED_DOCUMENTS).load()


_DOMAINS: tuple[CandidatePersonaDomain, ...] = (
    CandidatePersonaDomain.SPORTS_FAN,
    CandidatePersonaDomain.PARENTING,
    CandidatePersonaDomain.EXAM_PREPPER,
)


def _for(count: int, interests: tuple[str, ...] = ()) -> tuple[CandidateAssignment, ...]:
    """The assignment a batch of `count` candidates gets, for tests about the prose."""
    return assign_candidates(_DOMAINS[:count], interests)


def _account() -> CandidateAccountBrief:
    return CandidateAccountBrief(
        display_name="김도현",
        age=29,
        region="서울 성동구",
        occupation="백엔드 개발자",
        concept="야근과 직관 사이에서 잠금화면 일정으로 버티는 4년차 개발자",
        domain=CandidatePersonaDomain.SPORTS_FAN,
        interests=("KIA 타이거즈", "주말 러닝"),
        life_rhythm="평일 10시 출근, 주말 오전 러닝",
        background_subject=CandidateBackgroundSubject.SPORTS_TEAM,
        background_mood="야간 경기 조명이 켜진 외야 관중석",
    )


def _draft(topic: str = "시험기간 일정 관리", domain: str = "sports_fan") -> JsonObject:
    return {
        "topic": topic,
        "country": "KR",
        "posting_slot": "evening",
        "persona_domain": domain,
        "caption": f"{topic} — 잠금화면부터 바꾼다",
        "hypothesis": "1인칭 감탄이 저장률을 올린다",
        "refs_used": ["kr-001"],
        "principles_applied": [1, 4],
        "appium_prompt": "입력_일정: 09:00 스터디\n기기_시각: 07:20",
        "image_inputs": {
            "trace_items": [
                "05:50 한강 러닝",
                "09:00 통계학 2교시",
                "13:00 스터디",
                "19:00 저녁 약속",
                "22:30 인강 복습",
            ],
            "device_time": "07:20",
            "background_subject": "scenery",
            "background_mood": "늦은 밤 책상 위 스탠드 불빛",
            "language": "ko",
        },
    }


def _envelope(*drafts: JsonObject) -> JsonObject:
    return {"candidates": list(drafts)}


def test_instruction_carries_every_document_and_the_hard_rules(tmp_path: Path) -> None:
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(bundle, assignments=_for(3))

    # Then
    for relative_path in REQUIRED_DOCUMENTS:
        assert f"[context 문서: {relative_path}]" in instruction
    assert "FACTS 문서에 없는 검증 가능한 사실을 주장하지 마세요." in instruction
    assert "면책성 괄호 문구" in instruction
    assert "VOICE 문서를 그대로 따르세요" in instruction
    assert "INDEX 문서에 실제로 존재하는 id만" in instruction
    assert "appium_prompt" in instruction
    assert "정확히 3개의 후보 객체" in instruction
    assert "image_inputs" in instruction
    assert "character_kitty" in instruction
    assert "sports_team" in instruction
    assert "18~22개를 만드세요" in instruction
    assert "모호어 대신 실제로 보이는 것을" in instruction
    assert "실제로 잠금화면에 설정해뒀을 법한 배경" in instruction


def test_instruction_states_the_schedule_format_the_lock_screen_can_render(
    tmp_path: Path,
) -> None:
    """A schedule line the control plane refuses costs the whole call that produced it."""
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(bundle, assignments=_for(3))

    # Then the format is stated, and every example in the prompt obeys it
    assert "각 항목은 문자열이 아니라 객체입니다" in instruction
    assert "(최소 5개, 최대 24개)" in instruction
    assert '"title": "기아전 직관"' in instruction
    assert '"title": "제주 워크샵", "day": 4, "days": 3' in instruction
    assert "day + days는 7을 넘을 수 없습니다" in instruction


def test_instruction_states_the_country_and_language_the_request_asked_for(
    tmp_path: Path,
) -> None:
    """These are the two values the drafts are held to, so the prompt has to name them."""
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(bundle, assignments=_for(2), country="JP", language="ja")

    # Then
    assert "JP 게시물 후보 2개" in instruction
    assert '"country": "JP"' in instruction
    assert '"language": "ja"' in instruction
    assert '이 배치에서는 "ja" 를 그대로 쓰세요' in instruction


def test_instruction_keeps_the_job_out_of_the_schedule_and_the_caption(
    tmp_path: Path,
) -> None:
    """Occupation is background, not subject matter.

    The reference corpus is what settles this: the same lock-screen material reached 76x
    fewer people once the caption spoke as the maker (kr-032 against kr-026), and the
    maker story collapses 35x on its second use (kr-020 to kr-029). So a developer account
    has to post about its life, not about building the product it is advertising.
    """
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(bundle, assignments=_for(3))

    # Then
    assert "직무 작업을 일정으로 늘어놓지 마세요" in instruction
    assert "업무 티켓이 아닙니다" in instruction
    assert "staging 배포 확인" in instruction
    assert "앱을 만든 사람의 목소리가 아니라 앱을 쓰는 사람의 목소리" in instruction
    assert "제품·개발 용어를 캡션에 쓰지 마세요" in instruction
    assert "메이커 화법을 쓰지 마세요" in instruction
    assert "죽는 것은 메이커라는 신분이 아니라" in instruction
    assert "kr-020 → kr-029" in instruction
    assert "직업은 이 사람의 배경이지 글의 소재가 아닙니다" in instruction


def test_the_account_block_supplies_a_person_without_dictating_the_prose(
    tmp_path: Path,
) -> None:
    """An account says who is writing and what they can write about — never how.

    Style belongs to the reference corpus, which weighs each voice hypothesis against its
    counter-examples. A one-line instruction here would quietly outrank all of it, and did:
    the first accounts shipped a voice field and every caption opened by introducing itself.
    """
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(bundle, assignments=_for(3), account=_account())

    # Then
    assert "[이 계정으로 씁니다]" in instruction
    assert "김도현" in instruction
    assert "자기소개나 직업 소개로 캡션을 시작하지 마세요" in instruction
    assert "컨셉 문장을 캡션에 그대로 옮겨 쓰지 마세요" in instruction
    assert "직업은 이 사람이 어떤 시간을 사는지 알려줄 뿐" in instruction
    assert "문체·어미·길이는 이 블록이 정하지 않습니다" in instruction
    assert "말투:" not in instruction


def test_an_account_replaces_the_per_candidate_domain_spread(tmp_path: Path) -> None:
    """Spreading one account's batch across domains would contradict the account."""
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(
        bundle,
        assignments=assign_candidates((CandidatePersonaDomain.PARENTING,) * 3),
        account=_account(),
    )

    # Then
    assert "누적 커버리지가 가장 적은 순서로" not in instruction
    assert "persona_domain 은 sports_fan 으로 고정합니다." in instruction
    assert "계정 블록이 있으면 그 계정의 도메인" in instruction


def _assert_identity_invention_block(instruction: str) -> None:
    """The half of the old persona block that only the account-less path may read."""
    assert "[정체성 창작 규칙]" in instruction
    assert '서로 다른 "구체 정체성"을 먼저 창작하고' in instruction
    assert "도메인을 스포츠에 몰지 말고 넓게 흩으세요" in instruction
    assert '"야구를 좋아함", "운동을 좋아함" 수준은 금지입니다.' in instruction
    assert "어느 팀의 팬인지까지 정해진" in instruction


def _assert_craft_block(instruction: str) -> None:
    """The half both paths read: how surface detail is derived from whoever is writing."""
    assert "[구체성 규칙]" in instruction
    assert "그 사람의 실제 한 주에서 나올 법한" in instruction
    assert "기아전 직관" in instruction
    assert '"회의", "운동", "공부", "약속" 같은 범용 항목은 금지입니다.' in instruction
    assert "그 사람의 생활 리듬과 맞아야 합니다" in instruction
    assert (
        "실존 인물명·캐릭터명·팀명을 쓰는 자리는 image_inputs.background_search_query 하나뿐입니다."
        in instruction
    )
    assert "background_mood와 topic에는 넣지 마세요" in instruction
    assert "캡션의 화자는 그 사람 본인입니다" in instruction


def test_instruction_sanctions_real_names_only_in_the_background_search_query(
    tmp_path: Path,
) -> None:
    """The model must author the search query, and it is the one field real names belong in."""
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(bundle, assignments=_for(3))

    # Then the rule block asks for a wallpaper, not an occupation scene, and allows proper
    # nouns there
    assert '"그 사람이 자기 폰 배경화면으로 저장해뒀을 사진"을 찾는 검색어' in instruction
    assert (
        "이 필드에 한해 실존 인물명·캐릭터명·팀명·아이돌 그룹명을 그대로 써도 됩니다."
        in instruction
    )
    assert '"김도영 직캠"' in instruction
    assert '"쿠로미 배경화면"' in instruction
    # And the output contract names the field so the model actually emits it
    assert '"background_search_query"' in instruction
    assert (
        "- background_search_query: 그 사람이 배경화면으로 저장했을 사진을 찾을 검색어"
        in instruction
    )


def test_instruction_carries_the_persona_specificity_blocks(tmp_path: Path) -> None:
    """Both blocks reach the model on the plain path, with the INDEX but no reference bodies."""
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(bundle, assignments=_for(3))

    # Then
    _assert_identity_invention_block(instruction)
    _assert_craft_block(instruction)


def test_persona_specificity_block_survives_added_reference_bodies(tmp_path: Path) -> None:
    """Extra reference documents in the bundle must not push the block out of the prompt."""
    # Given
    loaded = _bundle(tmp_path)
    bundle = loaded.model_copy(
        update={
            "documents": (
                *loaded.documents,
                CandidateDocument(relative_path="references/KR/kr-001.md", text="# kr-001\n본문"),
            )
        }
    )

    # When
    instruction = build_instruction(bundle, assignments=_for(3))

    # Then
    assert "[context 문서: references/KR/kr-001.md]" in instruction
    _assert_identity_invention_block(instruction)
    _assert_craft_block(instruction)


def test_an_account_batch_is_never_told_to_invent_a_person(tmp_path: Path) -> None:
    """The two blocks used to contradict each other, and the invented person won.

    "Author a different concrete identity per candidate" and "do not invent an identity,
    the account is fixed" were both in the prompt whenever an account was chosen. So the
    invention rules only belong to the path that has nobody to write as; the craft rules
    belong to both.
    """
    # Given
    bundle = _bundle(tmp_path)

    # When the batch is written as an existing account
    instruction = build_instruction(bundle, assignments=_for(3), account=_account())

    # Then nothing asks it to author a person, while the craft rules still apply
    assert "[정체성 창작 규칙]" not in instruction
    assert '서로 다른 "구체 정체성"을 먼저 창작하고' not in instruction
    _assert_craft_block(instruction)


def test_the_caption_never_announces_that_it_is_staged(tmp_path: Path) -> None:
    """Accounts are run as concepts, so the writing does not declare itself a demo."""
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(bundle, assignments=_for(3))

    # Then the demo frame is gone and its verbs are named as what not to write
    assert "데모 프레임" not in instruction
    assert "연출임을 글에서 선언하지 마세요" in instruction
    assert '"만들어봤어", "올려봤어", "담아봤어", "세팅해봤어" 같은 시연 동사를 쓰지 마세요' in (
        instruction
    )
    assert "겪지 않은 삶이라는 사실을 글 안에서 밝히거나 암시하지" in instruction


def test_the_schedule_belongs_to_the_image_and_not_to_the_caption(tmp_path: Path) -> None:
    """Captions were reading the lock screen back out loud, which is the image's job."""
    # Given
    bundle = _bundle(tmp_path)

    # When
    instruction = build_instruction(bundle, assignments=_for(3))

    # Then
    assert "일정은 이미지가 보여줍니다" in instruction
    assert "캡션이 image_inputs.trace_items를 낭독하지 마세요" in instruction
    assert "시각이 붙은 일정 나열을 캡션에 넣지 마세요" in instruction
    assert "이미지의 일은 증명이고 캡션의 일은 스크롤을 멈추는 것입니다" in instruction
    assert "kr-001(relative 175.30)에는 일정 나열이 없습니다" in instruction


def test_the_instruction_lists_recent_candidates_to_avoid_repeating(tmp_path: Path) -> None:
    # Given a history of what this batch has already written
    bundle = _bundle(tmp_path)

    # When it is handed to the next call
    instruction = build_instruction(
        bundle,
        assignments=_for(1),
        history=(
            CandidateHistoryEntry(
                persona_domain=CandidatePersonaDomain.PARENTING, topic="첫째 재우기"
            ),
            CandidateHistoryEntry(persona_domain=None, topic="기록 없는 후보"),
        ),
    )

    # Then that call is shown both what was written and under which domain
    assert "[최근 생성된 후보 목록]" in instruction
    assert "- [육아] 첫째 재우기" in instruction
    assert "- [도메인 미기록] 기록 없는 후보" in instruction


def test_parse_rejects_a_wrong_count_and_a_wrong_country() -> None:
    # Given
    two_items = _envelope(_draft(), _draft("다른 주제"))
    japanese = _envelope({**_draft(), "country": "JP"})

    # When / Then
    with pytest.raises(CandidateFormatError) as count_failure:
        _ = parse_candidate_drafts(two_items, expected=1, country="KR")
    with pytest.raises(CandidateFormatError) as country_failure:
        _ = parse_candidate_drafts(japanese, expected=1, country="KR")
    assert count_failure.value.detail == "후보 1개가 필요하지만 2개를 받았습니다."
    assert "country는 모두 KR" in country_failure.value.detail
    assert (
        count_failure.value.message == "AI 응답이 형식을 통과하지 못했습니다 — 다시 시도해 주세요."
    )


def test_parse_rejects_an_answer_that_is_not_the_candidates_envelope() -> None:
    # Given a bare array, which is what the free-text path used to return
    payload: JsonValue = [_draft()]

    # When / Then
    with pytest.raises(CandidateFormatError) as failure:
        _ = parse_candidate_drafts(payload, expected=1, country="KR")
    assert failure.value.detail == "최상위 값이 JSON 객체가 아닙니다."


def test_parse_rejects_unusable_image_inputs() -> None:
    # Given answers whose image inputs break the machine contract
    base = _draft()["image_inputs"]
    assert isinstance(base, dict)

    def one(image_inputs: JsonObject) -> JsonObject:
        return _envelope({**_draft(), "image_inputs": image_inputs})

    bad_time = one({**base, "device_time": "7시 20분"})
    unknown_subject = one({**base, "background_subject": "감성적인 무언가"})
    week: list[JsonValue] = [{"title": f"일정 {index}", "day": index % 7} for index in range(25)]
    too_many = one({**base, "trace_items": week})
    four_items = one({**base, "trace_items": week[:4]})
    past_the_week = one(
        {**base, "trace_items": [{"title": "출장", "day": 5, "days": 4}, *week[:4]]}
    )
    outside: JsonValue = {"title": "출장", "day": 7}
    eighth_day = one({**base, "trace_items": [outside, *week[:4]]})
    too_many_todos = one({**base, "trace_todos": [f"할일 {index}" for index in range(21)]})

    # When / Then
    for payload, rejected_field in (
        (bad_time, "device_time"),
        (unknown_subject, "background_subject"),
        (too_many, "trace_items"),
        (four_items, "trace_items"),
        (past_the_week, "trace_items"),
        (eighth_day, "trace_items"),
        (too_many_todos, "trace_todos"),
    ):
        with pytest.raises(CandidateFormatError) as failure:
            _ = parse_candidate_drafts(payload, expected=1, country="KR")
        assert rejected_field in failure.value.detail


def test_parse_requires_at_least_one_principle() -> None:
    """A candidate that cites nothing cannot say what it was reasoning from."""
    # Given
    payload = _envelope({**_draft(), "principles_applied": []})

    # When / Then
    with pytest.raises(CandidateFormatError) as failure:
        _ = parse_candidate_drafts(payload, expected=1, country="KR")
    assert "principles_applied" in failure.value.detail


def test_parse_accepts_five_to_seven_schedule_items() -> None:
    # Given answers at the recommended schedule lengths
    base = _draft()["image_inputs"]
    assert isinstance(base, dict)

    # When / Then
    for count in (5, 6, 7):
        items: list[JsonValue] = [f"{index:02d}:00 일정" for index in range(count)]
        image_inputs: JsonObject = {**base, "trace_items": items}
        payload = _envelope({**_draft(), "image_inputs": image_inputs})
        drafts = parse_candidate_drafts(payload, expected=1, country="KR")
        assert len(drafts[0].image_inputs.trace_items) == count


def test_parse_binds_a_candidate_to_the_domain_its_call_was_assigned() -> None:
    """A model told to cover a domain will write its favourite one and call it covered."""
    # Given a call assigned to parenting whose answer came back as sports
    payload = _envelope(_draft(domain="sports_fan"))

    # When / Then the mismatch is named in terms the retry turn can quote
    with pytest.raises(CandidateFormatError) as failure:
        _ = parse_candidate_drafts(
            payload,
            expected=1,
            country="KR",
            domains=(CandidatePersonaDomain.PARENTING,),
        )
    assert "persona_domain은 배정된 순서대로 [parenting]" in failure.value.detail
    assert "[sports_fan] 를 받았습니다" in failure.value.detail
