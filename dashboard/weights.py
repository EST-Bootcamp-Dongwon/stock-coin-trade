"""화면이 지금 쓰는 **가중치** — 프리셋이거나, 사람이 슬라이더로 고른 것이거나.

## 🔴 왜 값객체인가 — 가중치가 화면의 입력이 되었기 때문이다

M9 전까지 화면은 `profile: str` 하나만 들고 다녔고 그것이 저장된 열 이름
(`score_balanced_bp`)이기도 했다. 슬라이더가 생기면서 **이름 없는 가중치**가
생겼고, 그때부터 "어느 열을 읽나" 와 "무엇으로 가중하나" 가 갈라진다.

그 둘을 한 자리에 묶어 두지 않으면 화면마다 `if profile in PRESETS` 가 흩어진다.

## 🔒 프리셋 값과 같은 비율이면 **프리셋으로 되돌린다**

슬라이더를 만졌다가 35/30/20/15 로 되돌린 사람은 "균형 프리셋을 보고 있다" 고
생각한다. 그때 화면이 커스텀 모드에 남아 있으면 근거·확정 칸이 이유 없이 닫혀 있다.

🔴 비교는 **비율**로 한다. `Σw·z/Σw` 는 가중치 스칼라배에 불변이라
   70/60/40/30 은 35/30/20/15 와 **점수가 한 칸도 다르지 않다**
   (`test_가중치를_배로_올려도_점수가_같다`). 숫자가 같은데 모드만 다르면
   그건 화면의 거짓말이다.

## 🔒 여기에는 화면 문구가 없다

사람에게 보이는 말은 전부 `dashboard/explain.py` 가 쓴다(`weighting_label`).
문구가 두 곳에 있으면 어느 쪽이 바뀌었는지 `git diff` 로 못 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from typing import Any, Mapping

from sector.scoring import AXES, PRESETS

__all__ = ["MAX_WEIGHT", "WeightError", "Weighting", "normalize"]

#: 슬라이더 한 축의 상한. 🔒 상한이 있어야 URL·입력으로 들어온 터무니없는 수를
#: **거절할 근거**가 생긴다. 비율만 쓰므로 100 이면 충분하다 (0~100 : 0~100).
MAX_WEIGHT = 100


class WeightError(ValueError):
    """가중치 입력이 쓸 수 없는 모양이다. 🔒 조용히 고치지 않는다 — 화면이 말한다."""


def normalize(raw: Mapping[str, Any]) -> dict[str, int]:
    """입력을 `{"M":35,"F":30,"B":20,"V":15}` 모양으로. 못 쓰면 `WeightError`.

    🔒 **합을 100 으로 맞추지 않는다.** `weighted_score_bp` 가 한 번에 나누므로
       비율만 맞으면 된다. 여기서 미리 정수 나눗셈을 하면 스칼라배 불변이 깨진다.
    """
    if set(raw) != set(AXES):
        raise WeightError(f"축은 {'·'.join(AXES)} 넷이어야 한다")
    out: dict[str, int] = {}
    for axis in AXES:
        value = raw[axis]
        if isinstance(value, bool) or not isinstance(value, int):
            # 🔒 `bool` 은 `int` 의 하위형이라 따로 막는다 — `True` 가 1 로 통과한다
            raise WeightError(f"{axis} 축 가중치가 정수가 아니다")
        if not 0 <= value <= MAX_WEIGHT:
            raise WeightError(f"{axis} 축 가중치가 0~{MAX_WEIGHT} 밖이다")
        out[axis] = value
    if sum(out.values()) == 0:
        # 🔴 전부 0 이면 모든 섹터의 점수가 `None` 이 되어 표가 통째로 빈다.
        #    빈 표를 그리고 사용자가 이유를 추측하게 두지 않는다
        raise WeightError("네 축의 가중치가 전부 0 이다 — 하나는 0 보다 커야 한다")
    return out


def _ratio(weights: Mapping[str, int]) -> tuple[int, ...]:
    """약분한 비율. 🔒 35/30/20/15 와 70/60/40/30 이 같은 값을 준다 (머리주석)."""
    values = tuple(weights[a] for a in AXES)
    divisor = 0
    for value in values:
        divisor = gcd(divisor, value)
    return tuple(v // divisor for v in values) if divisor else values


#: 프리셋의 비율 → 이름. 🔒 모듈이 읽힐 때 한 번만 만든다
_BY_RATIO: Mapping[tuple[int, ...], str] = {
    _ratio(weights): name for name, weights in PRESETS.items()
}


@dataclass(frozen=True, slots=True)
class Weighting:
    """화면이 지금 쓰는 가중치. `name` 이 있으면 프리셋, 없으면 사람이 고른 것."""

    name: str | None
    weights: Mapping[str, int]

    @classmethod
    def preset(cls, name: str) -> "Weighting":
        if name not in PRESETS:
            raise WeightError(f"모르는 프리셋이다 — {'·'.join(PRESETS)} 중 하나여야 한다")
        return cls(name=name, weights=dict(PRESETS[name]))

    @classmethod
    def of(cls, raw: Mapping[str, Any]) -> "Weighting":
        """슬라이더·URL 에서 온 값 → `Weighting`. 🔒 프리셋과 **같은 비율이면 프리셋이다**."""
        weights = normalize(raw)
        return cls(name=_BY_RATIO.get(_ratio(weights)), weights=weights)

    @property
    def is_preset(self) -> bool:
        return self.name is not None

    @property
    def ratio(self) -> tuple[int, ...]:
        return _ratio(self.weights)

    def column(self, prefix: str) -> str:
        """저장된 열 이름 — `score_balanced_bp` · `rank_momentum`.

        🔴 프리셋일 때만 부른다. 커스텀에는 저장된 열이 **없다** — 있는 척하면
           `KeyError` 대신 엉뚱한 열을 읽는 코드가 언젠가 생긴다.
        """
        if self.name is None:
            raise WeightError("커스텀 가중치에는 저장된 열이 없다")
        return f"score_{self.name}_bp" if prefix == "score" else f"rank_{self.name}"
