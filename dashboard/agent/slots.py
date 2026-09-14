"""문장의 숫자 자리 — compose 는 자리를 비워 두고, guard 는 원천 값으로 **다시 채워 본다.**

## 🔴 왜 자리인가 (2026-09-14 적대적 리뷰 R2)

처음 guard 는 "문장 속 숫자가 그 문장이 단 근거들 어딘가에 있는가" 만 봤다. 그러자
"21개 섹터 중 11위" 를 "11개 섹터 중 21위" 로 바꿔도 두 숫자가 다 근거에 있어 통과했고,
부호를 떼거나 `bp` 를 붙인 숫자도 지나갔다. 숫자를 한 통에 모아 대조하면 **어느 값이 어느
자리에 있는지**를 모른다.

이제 숫자는 `⟦EV-RANK|int⟧` 처럼 **근거 ID 와 모양이 적힌 자리**로만 문장에 들어간다.
guard 는 원천에서 다시 얻은 값으로 같은 자리를 채워 **글자까지 같은지** 보고, 자리마다 허락된
모양과 **뒤따르는 단위**(순위 뒤에는 "위", 섹터 수 뒤에는 "개")까지 본다.
🔒 자리 밖(템플릿의 글자)에는 숫자가 한 글자도 없어야 한다 — guard 가 그것도 본다.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

__all__ = ["FORMATS", "SlotError", "slot", "cites_of", "literal_of", "fill", "slots_in"]

FORMATS: tuple[str, ...] = ("int", "signed", "sigma", "pct2", "pct1", "date", "text", "dec1")
_INTEGER_FORMATS = ("int", "signed", "sigma", "pct2", "pct1")
_SLOT = re.compile(r"⟦(EV-[A-Z0-9]+(?:-[A-Z0-9]+)*)\|([a-z0-9]+)⟧")


class SlotError(ValueError):
    """자리를 채울 수 없다 — 없는 근거 · 빈 값 · 모양이 맞지 않는 값."""


def slot(evidence_id: str, fmt: str = "int") -> str:
    if fmt not in FORMATS:
        raise SlotError(f"모르는 자리 모양: {fmt}")
    return f"⟦{evidence_id}|{fmt}⟧"


def slots_in(template: str) -> list[tuple[str, str, int]]:
    """(근거 ID, 모양, 자리가 끝나는 위치) — guard 가 자리 뒤의 단위를 본다."""
    return [(m.group(1), m.group(2), m.end()) for m in _SLOT.finditer(template)]


def cites_of(template: str) -> tuple[str, ...]:
    """템플릿이 쓰는 근거 ID — 나온 순서대로, 겹치지 않게."""
    return tuple(dict.fromkeys(match.group(1) for match in _SLOT.finditer(template)))


def literal_of(template: str) -> str:
    """자리를 뺀 글자 — 🔒 여기에는 숫자가 없어야 한다."""
    return _SLOT.sub(" ", template)


def _format(value: Any, fmt: str, evidence_id: str) -> str:
    # 🔴 빈 값을 글자로 만들지 않는다 — "None개" 가 화면에 나갔다(리뷰 O6)
    if value is None or value == "":
        raise SlotError(f"{evidence_id} 가 비어 있는데 문장에 넣으려 했다")
    if fmt in _INTEGER_FORMATS and (isinstance(value, bool) or not isinstance(value, int)):
        raise SlotError(f"{evidence_id} 는 정수가 아니다 ({value!r})")
    if fmt == "int":
        return str(value)
    if fmt == "signed":
        return f"{value:+d}"
    if fmt == "sigma":
        return f"{value / 10000:+.2f}"
    if fmt == "pct2":
        return f"{abs(value) / 100:.2f}"
    if fmt == "pct1":
        return f"{abs(value) / 100:.1f}"
    if fmt == "date":
        text = str(value)
        if not re.fullmatch(r"[0-9]{8}", text):
            raise SlotError(f"{evidence_id} 는 날짜 모양이 아니다 ({value!r})")
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return str(value)


def fill(template: str, values: Mapping[str, Any]) -> str:
    """자리를 채운다. 🔒 채워 넣은 글자는 다시 훑지 않는다 — 이름에 자리 모양이 있어도 풀리지 않는다."""
    def one(match: re.Match[str]) -> str:
        evidence_id, fmt = match.group(1), match.group(2)
        if fmt not in FORMATS:
            raise SlotError(f"모르는 자리 모양: {fmt}")
        if evidence_id not in values:
            raise SlotError(f"{evidence_id} 가 장부에 없다")
        return _format(values[evidence_id], fmt, evidence_id)

    return _SLOT.sub(one, template)
