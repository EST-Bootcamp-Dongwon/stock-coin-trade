"""고정 ID 대장 — **한 번 붙은 ID 는 뜻이 바뀌지 않고, 은퇴한 ID 는 새 뜻으로 돌아오지 않는다.**

GIC 프롬프트의 고정 ID 장치를 구조로 옮겼다(원문 문장은 옮기지 않는다 · ADR-SC-0013).
프롬프트는 단계마다 챗봇이 번호를 매기므로 규칙으로 막아야 했지만, 여기서는 ID 가 **카탈로그의
열쇠**라 번호를 새로 매길 일이 구조적으로 없다. 남은 위험은 사람이 ID 를 지우고 같은 이름을
다른 뜻으로 되살리는 것 — `RETIRED` 와 `agent_test` 가 그것을 막는다.

| 접두사 | 무엇 | GIC 대응 |
|---|---|---|
| `TH-` | 논점 — 이 답이 주장하는 것 | 산업리서치 TH- |
| `RK-` | 리스크 — 반론이 못 막으면 유효해진다 | RK- |
| `AT-` | 공격 — Red Team 질문 | 기업리서치 AT- |
| `GP-` | Gap — 이 답이 채우지 못한 것 | Gap Log 번호 |
| `EV-` | 근거 — 장부의 한 줄 (카탈로그 밖 · 형식만 검사) | evidence_id |

🔒 `IP-`(투자포인트) · `CT-`(촉매) · `CD-`(후보) 는 대장에 두지 않는다 — 이 도구에는 그 단계가 없다.
   없는 단계의 이름을 새 뜻으로 빌려 오면 대장을 읽는 사람이 원래 뜻으로 오해한다.
"""

from __future__ import annotations

import re
from typing import Mapping

__all__ = ["CATALOG", "RETIRED", "EVIDENCE_ID", "is_known"]

CATALOG: Mapping[str, str] = {
    # ── 논점 ──
    "TH-RANK": "이 섹터가 이 순위에 있다",
    "TH-M": "모멘텀 축이 순위를 움직였다",
    "TH-F": "자금흐름 축이 순위를 움직였다",
    "TH-B": "폭 축이 순위를 움직였다",
    "TH-V": "밸류 축이 순위를 움직였다",
    "TH-DEF": "이 섹터를 이렇게 묶었다",
    # ── 리스크 ──
    "RK-PRESET": "가중치에 기댄 순위",
    "RK-SWING": "흔들리는 순위",
    "RK-CONC": "한 축에 쏠린 점수",
    "RK-LIQ": "ETF 거래가 적어 실제로 담기 어렵다",
    "RK-ONE-ETF": "자금흐름이 ETF 한 종목에 달려 있다",
    "RK-HEAT": "이미 많이 올라 밸류가 낮다",
    "RK-AXES": "네 축을 다 쓰지 못한 점수",
    # ── 공격 ──
    "AT-1": "가중치를 바꾸면 순위가 무너지지 않나",
    "AT-2": "오늘만 반짝인 순위가 아닌가",
    "AT-3": "한 축만 끌어올린 점수가 아닌가",
    "AT-4": "ETF 로 실제로 거래할 수 있나",
    "AT-5": "자금흐름이 ETF 한 종목에 달려 있지 않나",
    "AT-6": "이미 많이 올라 과열된 것 아닌가",
    "AT-7": "네 축을 다 써서 낸 점수인가",
    # ── Gap ──
    "GP-AXIS-M": "모멘텀 축을 계산하지 못했다",
    "GP-AXIS-F": "자금흐름 축을 계산하지 못했다",
    "GP-AXIS-B": "폭 축을 계산하지 못했다",
    "GP-AXIS-V": "밸류 축을 계산하지 못했다",
    "GP-STAB": "순위 안정성을 낼 이력이 모자라다",
    "GP-LIQ": "유동성을 판정할 창이 차지 않았다",
    "GP-PAST": "비교할 과거 기준일이 없다",
    "GP-CONFIRM": "확정한 날을 알 수 없다",
    "GP-MASTER": "섹터 정의를 읽지 못했다",
    "GP-ETF-N": "점수에 쓴 ETF 수를 알 수 없다",
    "GP-AXES": "점수에 쓴 축을 알 수 없다",
    "GP-INVESTOR": "투자자별 매매동향 · 외국인 보유",
    "GP-FUNDAMENTAL": "이익 기반 밸류(PER · PBR)",
    "GP-PDF": "ETF 실제 구성(PDF) · 지수 구성종목",
    "GP-CAUSE": "왜 그렇게 움직였는가",
    "GP-PRESET": "가중치에 따라 순위가 갈린다",
    "GP-YAML-LIQ": "유동성 경고 — 사람이 적은 것과 실측이 다르다",
}

#: 🔒 **지운 ID 는 여기로 옮기고 다시 쓰지 않는다.** 지금은 비어 있다.
RETIRED: frozenset[str] = frozenset()

#: 근거 ID 의 모양. 카탈로그에 넣지 않는다 — ETF 가 몇 개인지에 따라 늘어난다.
EVIDENCE_ID = re.compile(r"^EV-[A-Z0-9]+(?:-[A-Z0-9]+)*$")


def is_known(identifier: str) -> bool:
    if identifier.startswith("EV-"):
        return bool(EVIDENCE_ID.match(identifier))
    return identifier in CATALOG and identifier not in RETIRED
