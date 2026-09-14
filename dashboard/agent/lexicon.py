"""섹터 이름 사전 — 질문에서 **어느 섹터인가** 를 찾고, 찾은 이름을 가린다.

## 🔴 왜 가리는가 (2026-09-14 라우팅 실측)

임베딩이든 n-gram 이든 "반도체는 왜 이렇게 점수가 높아?" 를 **섹터 이름 쪽으로** 끌고 갔다.
공통 글자 "반도체" 가 의도("왜")보다 강하게 작용한다. 그래서 의도를 가르기 전에 찾은 이름을
`SECTOR_TOKEN` 한 낱말로 바꾼다 — 예문도 같은 낱말로 적혀 있어 섹터 이름이 판정에서 빠진다.

`SECTOR_TOKEN` 이 "섹터" 인 것은 의도다. 사용자가 "이 섹터 왜 1위야" 라고 쓰면 그것도
같은 낱말이 된다 — 이름을 적은 질문과 적지 않은 질문이 같은 모양이 된다.

## 🔴 이름은 **어절 머리**에서 찾고, 애매하면 **애매하다고** 돌려준다 (2026-09-14 리뷰 R1 · O7 · 재검증 R-C)

처음에는 별칭 글자 사이에 공백을 허락하고 어디서든 찾았다. 그러자
- "통신 사도 돼?" 에서 별칭 `통신사` 가 "통신 사" 를 먹어 거절어 "사도" 가 사라졌고
- "유통기한" 이 유통 섹터, "제약 조건" 이 바이오 섹터가 됐다.
그래서 이름은 어절 머리에서 시작해 **조사 · 짧은 꼬리까지만** 붙은 채 어절이 끝나야 한다. 공백은 네 글자
이상 별칭("2차 전지")에만 허락한다. 거절 규칙은 **가리기 전 문장에도** 걸린다(`intent`).

🔴 그러자 반대 문제가 생겼다 — "반도체ETF" · "KODEX반도체" 를 못 알아보고 **화면에서 고른 섹터로 조용히**
답했다. 이제 어절 **안에** 별칭이 들어 있는데 온전히 읽지 못했으면 `Found.unclear` 에 담는다. 라우터는 그때
짐작하지 않고 되묻는다.

## 🔒 별칭은 `sectors.yaml` 이 아니라 여기 둔다

yaml 을 바꾸면 `config_sha256` 이 바뀌어 점수 행 전부가 "바뀐 파일" 이 되고 HF 게시가
통째로 다시 돈다(M6 멱등성). 별칭은 점수의 입력이 아니라 **화면의 입력**이다.
`agent_test` 가 모든 별칭이 실재하는 섹터 id 를 가리키는지, 두 섹터에 걸리지 않는지 대조한다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

__all__ = ["ALIASES", "SECTOR_TOKEN", "Found", "Lexicon", "normalize"]

#: 가린 섹터 이름이 되는 낱말. 🔒 `normalize` 를 지나도 살아남는 글자여야 한다.
SECTOR_TOKEN = "섹터"

#: 섹터 id → 사람들이 실제로 부르는 이름. `name_ko` 와 id 는 `Lexicon.of` 가 스스로 더한다.
#: 🔒 **짧고 흔한 말은 넣지 않는다** — "차"(자동차) · "칩"(반도체) · "항공"(운송의 항공사와 겹친다) ·
#:    "제약"("제약 조건"). 두 섹터에 걸리는 별칭은 테스트가 막는다.
ALIASES: Mapping[str, tuple[str, ...]] = {
    "semiconductor": ("반도체",),
    "software": ("소프트웨어",),
    "game": ("게임",),
    "media_entertainment": ("미디어", "엔터", "엔터테인먼트", "콘텐츠", "컨텐츠"),
    "telecom": ("통신", "통신사"),
    "battery": ("2차전지", "이차전지", "배터리"),
    "shipbuilding": ("조선", "조선업"),
    "defense": ("방산", "방위산업"),
    "robot": ("로봇",),
    "aerospace": ("우주항공", "항공우주", "우주"),
    "transport": ("운송",),
    "construction": ("건설",),
    "steel": ("철강",),
    "chemical": ("화학", "석유화학"),
    "auto": ("자동차", "완성차"),
    "retail_ecommerce": ("유통", "이커머스", "커머스"),
    "cosmetics": ("화장품", "뷰티"),
    "healthcare": ("바이오", "헬스케어", "제약바이오"),
    "bank": ("은행",),
    "securities": ("증권", "증권사"),
    "nuclear": ("원자력", "원전"),
}

#: 이름 뒤에 붙어도 되는 것 — 🔒 **짧은 꼬리 + 조사까지만.** "유통기한" 의 "기한" 은 붙지 않는다.
_TAIL = (r"(?:주|업|업계|업체|섹터|쪽|etf)?(?:들)?"
         r"(?:은|는|이|가|을|를|에|에서|에서는|에는|의|도|만|만의|과|와|랑|이랑|하고|으로|로|보다|부터|까지|"
         r"이야|야|이지|지|이고|고|이나|나|요|은요|는요|엔|라서|이라서|인데|은데|는데|한테|"
         r"였어|였다|이었어|이었다|처럼|같이)?")


def normalize(text: object) -> str:
    """NFKC · 소문자 · 글자와 숫자와 `%` 만 남기고 나머지는 공백 한 칸.

    🔒 문장부호를 지우지 않고 **공백으로** 바꾼다 — "반도체,조선" 이 "반도체조선" 이 되면
       어절 경계로 거는 거절 규칙(`intent`)이 경계를 잃는다.
    🔴 **폭 없는 문자(유니코드 Cf)는 공백이 아니라 지운다** — "매(폭 없는 문자)수" 가 "매 수" 가 되어
       거절 규칙을 비껴갔다(리뷰 R1).
    """
    folded = unicodedata.normalize("NFKC", str(text)).lower()
    kept = []
    for ch in folded:
        if unicodedata.category(ch) == "Cf":
            continue
        kept.append(ch if (ch.isalnum() or ch == "%") else " ")
    return " ".join("".join(kept).split())


def _compact(text: str) -> str:
    return "".join(text.split())


@lru_cache(maxsize=512)
def _pattern(alias: str) -> re.Pattern[str]:
    gap = r"\s?" if len(alias) >= 4 else ""
    body = gap.join(re.escape(ch) for ch in alias)
    return re.compile(r"(?<=\s)(" + body + r")(" + _TAIL + r")(?=\s)")


@dataclass(frozen=True, slots=True)
class Found:
    """질문에서 찾은 것. `masked` 가 라우터의 입력이다."""

    sectors: tuple[str, ...]
    gics: tuple[str, ...]
    masked: str
    #: 🔴 어절 안에 별칭이 있는데 온전히 읽지 못한 섹터 — 라우터가 짐작하지 않고 되묻는다
    unclear: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Lexicon:
    """별칭(공백을 뺀 모양) → 섹터 id · GICS id."""

    sector_of: Mapping[str, str]
    gics_of: Mapping[str, str]

    @classmethod
    def of(cls, master: Any) -> "Lexicon":
        """`SectorMaster` 에서 만든다. 🔒 master 가 없으면 빈 사전이다 — 이름을 지어내지 않는다."""
        sector_of: dict[str, str] = {}
        gics_of: dict[str, str] = {}
        if master is None:
            return cls(sector_of={}, gics_of={})
        for sector in master.sectors:
            for alias in (sector.name_ko, sector.id, *ALIASES.get(sector.id, ())):
                key = _compact(normalize(alias))
                if key:
                    sector_of.setdefault(key, sector.id)
        for gics in master.gics_sectors:
            key = _compact(normalize(gics.name_ko))
            # 🔒 테마 섹터 이름과 같으면(예: "헬스케어") 테마 섹터가 이긴다 — 실제 매매 단위다
            if key and key not in sector_of:
                gics_of[key] = gics.id
        return cls(sector_of=sector_of, gics_of=gics_of)

    def find(self, text: str) -> Found:
        """`text` 는 `normalize` 를 지난 것이다. **긴 별칭부터** 찾고 찾은 이름만 가린다(꼬리는 남긴다).

        🔒 긴 것부터인 이유 — "석유화학" 을 먼저 가려야 "화학" 이 그 안에서 한 번 더 걸리지 않는다.
        """
        masked = f" {text} "
        sectors: list[str] = []
        gics: list[str] = []
        entries = [(alias, sid, "sector") for alias, sid in self.sector_of.items()]
        entries += [(alias, gid, "gics") for alias, gid in self.gics_of.items()]
        ordered = sorted(entries, key=lambda e: (-len(e[0]), e[0]))
        for alias, target, kind in ordered:
            pattern = _pattern(alias)
            if not pattern.search(masked):
                continue
            masked = pattern.sub(lambda m: SECTOR_TOKEN + m.group(2), masked)
            bucket = sectors if kind == "sector" else gics
            if target not in bucket:
                bucket.append(target)
        unclear: list[str] = []
        for word in masked.split():
            if word.startswith(SECTOR_TOKEN):
                continue
            for alias, target, kind in ordered:
                if kind == "sector" and len(alias) >= 2 and alias in word:
                    if target not in unclear and target not in sectors:
                        unclear.append(target)
                    break
        return Found(
            sectors=tuple(sorted(sectors)),
            gics=tuple(sorted(gics)) if not sectors else (),
            masked=" ".join(masked.split()),
            unclear=tuple(sorted(unclear)),
        )
