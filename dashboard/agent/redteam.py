"""Red Team — **반론을 데이터로 세우고, 데이터가 막았는지 규칙으로 판정한다.**

## GIC 에서 옮긴 것 (구조만 · ADR-SC-0013)

공격·방어표의 칸(공격 질문 · 겨냥한 대상 · 방어 근거 · 정량 지표 유무 · 세 가지 상태 · 걸린 조건)과
**방어의 세기에 상한을 두는 생각**을 옮겼다. 여기서 상한은 셋이다 —

1. 숫자로 된 근거 없이는 [통과] 를 주지 않는다
2. 근거가 없으면 판정을 미루고 [추가확인] 으로 둔다 — 막을 재료가 없는데 막았다고 하지 않는다
3. [추가확인] 은 무엇이 모자란지(Gap)를 가리키고, 그 Gap 은 빈칸 목록에 실제로 있어야 한다

프롬프트는 이것을 챗봇에게 **부탁**했다. 여기서는 `Attack.__post_init__` 의 **불변식**이라 어긴 공격은
만들어지지 않는다. 🔴 그리고 guard 가 원천 값으로 판정을 **따로 다시 매겨** 맞춰 본다 — 판정만 뒤집거나
방어 문장을 바꿔 끼운 공격이 통과했었다(2026-09-14 리뷰 O4).

## 🔒 공격은 판정이 아니다

[통과] 는 "이 섹터가 좋다" 가 아니라 **"이 반론을 데이터가 막았다"** 다. 그래서 화면은 늘
`STATUS_PLAIN` 을 옆에 적는다. 반론을 다 막아도 이 도구는 무엇을 담으라고 말하지 않는다.

## 🔒 문턱은 낱말로 적고, 방어는 자리로 적는다

"세 계단 안에" 처럼 문턱을 **숫자 없이** 적는다. 방어 문장의 숫자는 `slots` 자리로만 들어간다.
판정이 무엇이냐에 따라 문장이 하나로 정해지므로(`RULES` · `defense_template`), guard 가 판정을
다시 매기면 문장까지 같이 대조된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Mapping

from dashboard.agent import ids
from dashboard.agent.inventory import Inventory
from dashboard.agent.slots import cites_of, fill, slot
from sector.scoring import AXES, AXIS_NAMES, PRESETS

__all__ = ["STATUSES", "STATUS_PLAIN", "CATEGORY", "TARGET", "RISK", "OPEN_GAP", "CASE_STATUS",
           "RULES", "Attack", "attacks", "risk_ledger", "defense_template", "extra_cites",
           "ATTACK_IDS"]

STATUSES: tuple[str, ...] = ("통과", "취약", "추가확인")
STATUS_PLAIN = {
    "통과": "데이터가 막았다",
    "취약": "데이터가 못 막았다",
    "추가확인": "데이터가 모자라 더 봐야 한다",
}
ATTACK_IDS: tuple[str, ...] = ("AT-1", "AT-2", "AT-3", "AT-4", "AT-5", "AT-6", "AT-7")

CATEGORY: Mapping[str, str] = {
    "AT-1": "논리 허점", "AT-2": "데이터 맹점", "AT-3": "논리 허점", "AT-4": "리스크 과소평가",
    "AT-5": "데이터 맹점", "AT-6": "밸류 선반영", "AT-7": "데이터 맹점",
}
#: 겨냥한 논점. 🔒 AT-3 은 끌어올린 축이 있으면 그 축을 겨냥한다(`_concentration`)
TARGET: Mapping[str, str] = {
    "AT-1": "TH-RANK", "AT-2": "TH-RANK", "AT-3": "TH-RANK", "AT-4": "TH-RANK",
    "AT-5": "TH-F", "AT-6": "TH-M", "AT-7": "TH-RANK",
}
RISK: Mapping[str, str] = {
    "AT-1": "RK-PRESET", "AT-2": "RK-SWING", "AT-3": "RK-CONC", "AT-4": "RK-LIQ",
    "AT-5": "RK-ONE-ETF", "AT-6": "RK-HEAT", "AT-7": "RK-AXES",
}
#: [추가확인] 일 때 가리키는 Gap. 🔴 뜻이 맞아야 한다 — ETF 수를 모르는 것을 "정의를 못 읽었다" 로 적었었다
OPEN_GAP: Mapping[str, str] = {
    "AT-1": "GP-AXES", "AT-2": "GP-STAB", "AT-3": "GP-AXES", "AT-4": "GP-LIQ",
    "AT-5": "GP-ETF-N", "AT-6": "GP-AXIS-V", "AT-7": "GP-AXES",
}

#: 판정의 갈래 → 상태. `none`(보탠 축 없음) · `single`(하나뿐) · `heavy`(한 축이 두 배 넘게 큼)는 AT-3 만 쓴다
CASE_STATUS: Mapping[str, str] = {
    "pass": "통과", "none": "통과", "weak": "취약", "single": "취약", "heavy": "취약", "open": "추가확인",
}

_OPEN_RULE = "자료가 모자라면 판정을 미룬다"
RULES: Mapping[tuple[str, str], str] = {
    ("AT-1", "pass"): "세 가중치의 순위가 세 계단 안에 모이면 통과",
    ("AT-1", "weak"): "세 가중치의 순위가 세 계단을 넘게 갈려 통과가 아니다",
    ("AT-2", "pass"): "순위 진폭이 두 계단 이하면 통과",
    ("AT-2", "weak"): "순위 진폭이 두 계단을 넘어 통과가 아니다",
    ("AT-3", "none"): "보탠 축이 없으면 한 축에 쏠릴 것도 없다",
    ("AT-3", "pass"): "가장 큰 축의 몫이 나머지를 합친 것의 두 배를 넘지 않는다",
    ("AT-3", "single"): "보탠 축이 하나뿐이다",
    ("AT-3", "heavy"): "가장 큰 축의 몫이 나머지를 합친 것의 두 배를 넘는다",
    ("AT-4", "pass"): "거래대금 기준을 넘으면 통과",
    ("AT-4", "weak"): "거래대금 기준에 못 미쳐 통과가 아니다",
    ("AT-5", "pass"): "ETF 가 둘 이상이면 통과",
    ("AT-5", "weak"): "ETF 가 둘보다 적어 자금흐름이 한 종목에 달려 있다",
    ("AT-6", "pass"): "밸류 σ 가 한 표준편차 넘게 낮지 않으면 통과",
    ("AT-6", "weak"): "밸류 σ 가 한 표준편차 넘게 낮으면 이미 많이 오른 쪽으로 본다",
    ("AT-7", "pass"): "네 축을 다 쓰고 척도를 내린 축이 없으면 통과",
    ("AT-7", "weak"): "네 축을 다 쓰지 못했거나 척도를 내린 축이 있으면 통과가 아니다",
    **{(attack_id, "open"): _OPEN_RULE for attack_id in ATTACK_IDS},
}

_OPEN_DEFENSE: Mapping[str, str] = {
    "AT-1": "세 가중치 중 순위를 내지 못한 것이 있어 판정하지 않는다",
    "AT-2": "순위 이력이 창만큼 차지 않아 판정하지 않는다",
    "AT-3": "기여를 낼 축이 없어 판정하지 않는다",
    "AT-4": "거래대금 평균을 낼 창이 차지 않아 판정하지 않는다",
    "AT-5": "점수에 쓴 ETF 수를 알 수 없어 판정하지 않는다",
    "AT-6": "밸류 축을 계산하지 못해 판정하지 않는다",
    "AT-7": "점수에 쓴 축 수를 알 수 없어 판정하지 않는다",
}


def defense_template(attack_id: str, case: str, *, positives: tuple[str, ...] = (),
                     degraded: bool = False) -> str:
    """판정 갈래가 정해지면 방어 문장은 하나로 정해진다. 🔒 guard 도 이 함수로 기대 문장을 만든다 —
    **갈래는 guard 가 따로 매긴다.**"""
    if case == "open":
        return _OPEN_DEFENSE[attack_id]
    if attack_id == "AT-1":
        ranks = " · ".join(f"{label} {slot(f'EV-RANK-{preset.upper()}')}위"
                           for preset, label in (("balanced", "균형"), ("momentum", "모멘텀 중시"),
                                                 ("contrarian", "역발상")))
        return f"세 가중치의 순위 — {ranks}."
    if attack_id == "AT-2":
        return (f"최근 {slot('EV-STAB-DAYS')}영업일 평균 {slot('EV-STAB-MEAN', 'dec1')}위 · "
                f"진폭 ±{slot('EV-STAB-SPREAD', 'dec1')} 다.")
    if attack_id == "AT-3":
        if case == "none":
            return "총점에 보탠 축이 없다 — 다른 섹터가 더 깎였을 뿐이다."
        listed = " · ".join(f"{AXIS_NAMES[axis]} {slot(f'EV-{axis}-CONTRIB', 'signed')}"
                            for axis in positives)
        return f"총점에 보탠 축 — {listed}."
    if attack_id == "AT-4":
        head = f"최근 {slot('EV-RULE-SHORT')}영업일 ETF 거래대금 평균이 하루 {slot('EV-RULE-LIQ')}억"
        return f"{head} 이상이다." if case == "pass" else f"{head}에 못 미친다."
    if attack_id == "AT-5":
        return f"점수에 쓴 ETF 는 {slot('EV-ETF-N')}개다."
    if attack_id == "AT-6":
        return f"밸류 σ 가 {slot('EV-V-Z', 'sigma')}σ 다."
    if attack_id == "AT-7":
        if case == "pass":
            return "네 축을 모두 썼고 척도를 내린 축이 없다."
        return f"점수에 쓴 축은 {slot('EV-AXES-USED')}개다." + (" 척도를 한 단 내린 축이 있다." if degraded else "")
    raise ValueError(f"모르는 공격 {attack_id}")


def extra_cites(attack_id: str, case: str, *, contributions: tuple[str, ...] = ()) -> tuple[str, ...]:
    """자리 밖에서 기대는 근거 — 🔒 숫자가 문장에 없어도 판정의 재료는 근거로 단다."""
    if case == "open":
        return ()
    if attack_id == "AT-3" and case == "none":
        return contributions
    if attack_id == "AT-4":
        return ("EV-LIQ",)
    if attack_id == "AT-7":
        return ("EV-AXES-USED", "EV-MISSING", "EV-DEGRADED")
    return ()


@dataclass(frozen=True, slots=True)
class Attack:
    id: str
    case: str
    target: str               # 겨냥한 논점 TH-
    defense_template: str
    defense: str              # 방어 — 데이터가 말하는 것
    cites: tuple[str, ...]    # 방어 근거 EV-
    gap: str | None = None    # 추가확인이면 가리키는 Gap

    def __post_init__(self) -> None:
        if self.id not in ATTACK_IDS or not ids.is_known(self.target) or not self.target.startswith("TH-"):
            raise ValueError(f"대장에 없는 공격 · 논점이다: {self.id} · {self.target}")
        if self.case not in CASE_STATUS or (self.id, self.case) not in RULES:
            raise ValueError(f"{self.id}: 모르는 판정 갈래 {self.case}")
        if not self.defense.strip():
            raise ValueError(f"{self.id}: 방어를 비워 둘 수 없다")
        # 🔴 방어의 세기에 둔 상한 (머리주석 1 · 2 · 3)
        if self.status == "통과" and not (self.quantitative and self.cites):
            raise ValueError(f"{self.id}: 숫자로 된 근거 없이 [통과] 를 줄 수 없다")
        if not self.cites and self.status != "추가확인":
            raise ValueError(f"{self.id}: 근거가 없으면 판정을 미루고 [추가확인] 으로 둔다")
        if self.status == "추가확인" and (self.gap is None or not ids.is_known(self.gap)):
            raise ValueError(f"{self.id}: [추가확인] 은 모자란 것(Gap)을 가리켜야 한다")

    @property
    def status(self) -> str:
        return CASE_STATUS[self.case]

    @property
    def quantitative(self) -> bool:
        return self.case != "open"

    @property
    def rule(self) -> str:
        return RULES[(self.id, self.case)]

    @property
    def category(self) -> str:
        return CATEGORY[self.id]

    @property
    def risk(self) -> str:
        return RISK[self.id]

    @property
    def question(self) -> str:
        return ids.CATALOG[self.id]


def _make(inventory: Inventory, attack_id: str, case: str, *, target: str | None = None,
          positives: tuple[str, ...] = (), degraded: bool = False,
          contributions: tuple[str, ...] = ()) -> Attack:
    template = defense_template(attack_id, case, positives=positives, degraded=degraded)
    values = {k: e.value for k, e in inventory.evidence.items()}
    cites = tuple(dict.fromkeys(cites_of(template) + extra_cites(attack_id, case,
                                                                 contributions=contributions)))
    return Attack(id=attack_id, case=case, target=target or TARGET[attack_id],
                  defense_template=template, defense=fill(template, values), cites=cites,
                  gap=OPEN_GAP[attack_id] if case == "open" else None)


def attacks(inventory: Inventory) -> tuple[Attack, ...]:
    """공격 일곱. 🔒 순위가 있는 섹터에만 세운다 — 없는 순위를 공격하지 않는다."""
    value = inventory.value
    if value("EV-RANK") is None:
        raise ValueError("순위가 없는 섹터에는 반론을 세우지 않는다")
    out: list[Attack] = []

    ranks = [value(f"EV-RANK-{p.upper()}") for p in PRESETS]
    out.append(_make(inventory, "AT-1", "open" if any(r is None for r in ranks)
                     else "pass" if max(ranks) - min(ranks) <= 3 else "weak"))

    spread = value("EV-STAB-SPREAD")
    out.append(_make(inventory, "AT-2", "open" if value("EV-STAB-MEAN") is None or spread is None
                     else "pass" if Fraction(spread) <= 2 else "weak"))

    parts = [(axis, value(f"EV-{axis}-CONTRIB")) for axis in AXES]
    live = tuple(f"EV-{axis}-CONTRIB" for axis, c in parts if c is not None)
    positives = sorted(((axis, c) for axis, c in parts if c is not None and c > 0),
                       key=lambda pair: (-pair[1], AXES.index(pair[0])))
    if not live:
        out.append(_make(inventory, "AT-3", "open"))
    elif not positives:
        out.append(_make(inventory, "AT-3", "none", contributions=live))
    else:
        best, rest = positives[0][1], sum(c for _, c in positives[1:])
        case = "single" if len(positives) == 1 else "pass" if best <= 2 * rest else "heavy"
        out.append(_make(inventory, "AT-3", case, target=f"TH-{positives[0][0]}",
                         positives=tuple(axis for axis, _ in positives)))

    ok = value("EV-LIQ")
    out.append(_make(inventory, "AT-4", "open" if ok is None else "pass" if ok else "weak"))

    count = value("EV-ETF-N")
    out.append(_make(inventory, "AT-5", "open" if count is None else "pass" if count >= 2 else "weak"))

    z = value("EV-V-Z")
    out.append(_make(inventory, "AT-6", "open" if z is None else "weak" if z <= -10000 else "pass"))

    used, degraded = value("EV-AXES-USED"), bool(value("EV-DEGRADED"))
    out.append(_make(inventory, "AT-7", "open" if used is None
                     else "pass" if used == len(AXES) and not degraded else "weak",
                     degraded=degraded))
    return tuple(out)


def risk_ledger(found: tuple[Attack, ...]) -> tuple[tuple[str, str, str], ...]:
    """고정 ID 대장의 리스크 칸 — (ID, 한 줄, 상태). 🔒 못 막은 공격의 리스크만 **유효** 다."""
    states = {"통과": "해당 없음", "취약": "유효", "추가확인": "확인 필요"}
    return tuple((a.risk, ids.CATALOG[a.risk], states[a.status]) for a in found)
