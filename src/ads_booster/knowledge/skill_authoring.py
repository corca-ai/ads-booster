"""Recognize explicit user skill directives, never infer them from useful feedback."""

from __future__ import annotations

# pyright: reportImplicitStringConcatenation=false
# ruff: noqa: RUF001 - intentional quoted-input and full-width punctuation handling
import re

from ads_booster.knowledge.operation_enums import SkillOperationKind

_ACTIONS = {
    "create": (
        "create",
        "make",
        "add",
        "write",
        "생성",
        "만들기",
        "등록",
        "만들어",
        "생성해",
        "등록해",
    ),
    "update": (
        "update",
        "edit",
        "revise",
        "modify",
        "수정",
        "업데이트",
        "수정해",
        "업데이트해",
        "바꿔",
    ),
    "retract": ("delete", "remove", "retract", "삭제", "폐기", "삭제해", "폐기해"),
}
_NEGATED = re.compile(
    r"\b(?:don't|do not|never|not asking|without creating)\b|"
    r"(?:만들|생성|등록|수정|업데이트|삭제|폐기)\s*(?:지\s*마|하지\s*마)|"
    r"(?:만들|생성|수정).{0,20}(?:아니|말고)",
    re.IGNORECASE,
)


def requested_skill_action(text: str) -> SkillOperationKind | None:
    """Accept a direct first-line imperative; ambiguous/quoted requests need clarification.

    The model still writes the procedure. This narrow authorization grammar does not
    classify task content, translate quotations, or promote a successful task to a skill.
    """
    text = re.sub(r"^\s*<@[A-Za-z0-9]+>\s*", "", text).strip()
    if not text or _NEGATED.search(text):
        return None
    line = text.splitlines()[0].strip()
    if line.startswith((">", "`", '"', "'", "“", "‘")):
        return None
    for kind, words in _ACTIONS.items():
        actions = "|".join(re.escape(word) for word in words)
        if re.match(
            rf"^(?:please\s+)?(?:{actions})\s+"
            r"(?:(?:a|an|the|new|reusable|workspace|shared|marketing)\s+){0,5}skill\b",
            line,
            re.IGNORECASE,
        ):
            return SkillOperationKind(kind)
        if re.match(rf"^(?:워크스페이스\s*(?:공용\s*)?)?스킬\s*(?:{actions})(?:\s*[:：]|$)", line):
            return SkillOperationKind(kind)
        if re.fullmatch(
            rf"[^\n]{{0,300}}스킬(?:을|로|도)?\s*(?:좀\s*|하나\s*)?"
            rf"(?:{actions})(?:줘|주세요|주십시오)[.!?。]*",
            line,
        ):
            return SkillOperationKind(kind)
    return None
