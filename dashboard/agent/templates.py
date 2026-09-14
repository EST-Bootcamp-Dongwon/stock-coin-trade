"""문장 틀 — 🔒 **글자만 여기 있다.**

어느 틀을 쓸지는 `compose` 가 장부 값으로 정하고, 그 결정이 맞는지는 `guard` 가 원천 값으로 **따로** 다시 정한다.
guard 는 같은 틀 함수로 "나와야 할 문장" 을 만들어 글자까지 대조하고, 틀 안 자리의 **순서**는 자기 표와
또 맞춰 본다. 그래서 틀 함수가 자리를 잘못 끼워도(균형 순위 대신 역발상 순위 · 두 기준일 맞바꿈) 두 번째
대조에서 걸린다(2026-09-14 재검증 R-A).

🔒 문장마다 **열쇠**(`ROLE_OF` 의 키)가 있다. 열쇠가 역할을 정하고, 역할이 guard 의 방향 검사를 정한다.
   열쇠를 바꾸면 guard 가 기대하는 틀도 달라지므로 "역할만 슬쩍 내리기" 가 통하지 않는다.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Mapping

from dashboard.agent.slots import slot
from dashboard.explain import josa
from sector.scoring import AXIS_NAMES

__all__ = [
    "ROLE_OF", "LABEL", "rank_headline", "sigma_band", "sigma", "no_score", "trust_rank",
    "TRUST_COUNTS", "axis_plain", "RAW_MISSING", "contrib", "stability_words", "stability",
    "STAB_MISSING", "no_past", "NO_NEW_DAY", "SINCE", "DEFAULT_WINDOW", "RANK_MISSING", "rank_change",
    "z_change", "Z_SAME", "no_master", "contents_head", "etf_line", "member_line", "ETF_COUNT",
    "ETF_UNKNOWN", "liquidity", "axis_title", "change_title",
]

#: 열쇠 → 역할. 🔒 guard 의 방향 검사는 역할을 따른다
ROLE_OF: Mapping[str, str] = {
    "fixed": "fixed", "disclaimer": "disclaimer",
    "rank_headline": "plain", "sigma": "sigma", "no_score": "no_score",
    "trust_rank": "plain", "trust_counts": "plain",
    "axis_plain": "axis_plain", "raw_missing": "raw_missing", "contrib": "contrib",
    "stability": "stability", "stab_missing": "stab_missing",
    "no_past": "no_past", "no_new_day": "no_new_day", "since": "plain", "default_window": "plain",
    "rank_missing": "rank_missing", "rank_change": "rank_change", "z_change": "z_change",
    "z_same": "z_same",
    "no_master": "no_master", "contents_head": "plain", "etf_line": "plain", "member_line": "plain",
    "etf_count": "plain", "etf_unknown": "etf_unknown", "liquidity": "liquidity",
}

LABEL = slot("EV-LABEL", "text")


def _who(label: str, pair: str = "은는") -> str:
    return f"**{LABEL}**{josa(label, pair)}"


# ── 왜 이 자리인가 ──────────────────────────────────────────────────────────

def rank_headline(label: str) -> str:
    return f"{_who(label)} {slot('EV-TOTAL')}개 섹터 중 **{slot('EV-RANK')}위**다."


def sigma_band(z_bp: int) -> str:
    """`explain.sigma_words` 와 같은 구간 — 🔒 숫자는 자리로 적는다(테스트가 두 함수를 대조한다)."""
    side = "높다" if z_bp > 0 else "낮다"
    size = abs(z_bp)
    if size >= 20000:
        return f"다른 섹터들보다 {slot('EV-RULE-SIGMA2')}σ 이상 {side}"
    if size >= 10000:
        return f"다른 섹터들보다 뚜렷이 {side}"
    if size >= 4000:
        return f"다른 섹터들보다 다소 {side}"
    return "다른 섹터들과 비슷하다"


def sigma(z_bp: int) -> str:
    return f"점수 {slot('EV-SCORE', 'sigma')}σ 는 '{sigma_band(z_bp)}' 는 뜻이다."


def no_score(label: str) -> str:
    return f"{_who(label)} 점수를 낼 수 없었다."


def axis_plain(axis: str, raw_bp: int) -> str:
    """원시값이 **실제로 무슨 일인지** — 부호마다 해석까지 한 문장. 🔴 밸류는 양수가 "평균보다 아래" 다."""
    r = slot(f"EV-{axis}-RAW", "pct1" if axis == "B" else "pct2")
    short, long_, value = slot("EV-RULE-SHORT"), slot("EV-RULE-LONG"), slot("EV-RULE-VALUE")
    sign = (raw_bp > 0) - (raw_bp < 0)
    table = {
        "M": {1: f"최근 {short}·{long_}일 수익률이 시장 평균보다 {r}%p 더 올랐다.",
              -1: f"최근 {short}·{long_}일 수익률이 시장 평균보다 {r}%p 덜 올랐다.",
              0: f"최근 {short}·{long_}일 수익률이 시장 평균과 같다."},
        "F": {1: f"ETF 상장좌수가 {short}영업일 새 {r}% 늘었다 — 운용사가 설정을 늘렸다는 뜻이고, "
                 f"실제로 돈이 들어온 자국이다.",
              -1: f"ETF 상장좌수가 {short}영업일 새 {r}% 줄었다 — 돈이 빠져나간 자국이다.",
              0: f"ETF 상장좌수가 {short}영업일 새 그대로다."},
        "B": {1: f"섹터 안에서 오름세인 종목 비율이 시장 전체보다 {r}%p 많다.",
              -1: f"섹터 안에서 오름세인 종목 비율이 시장 전체보다 {r}%p 적다.",
              0: "섹터 안에서 오름세인 종목 비율이 시장 전체와 같다."},
        "V": {1: f"지수가 {value}일 평균보다 {r}% **아래**에 있다 — 최근 많이 오르지 않았다는 뜻이다.",
              -1: f"지수가 {value}일 평균보다 {r}% **위**에 있다 — 최근 많이 올라 과열 쪽이라는 뜻이다.",
              0: f"지수가 {value}일 평균과 같은 자리에 있다."},
    }
    return table[axis][sign]


RAW_MISSING = "이 축의 원시값이 없어 무슨 일인지 말할 수 없다."


def contrib(axis: str, positive: bool) -> str:
    return (f"이 축에서 {slot('EV-TOTAL')}개 중 **{slot(f'EV-{axis}-RANK')}위**이고, "
            f"총점을 **{slot(f'EV-{axis}-CONTRIB', 'signed')}** 만큼 {'보탰다' if positive else '깎았다'}.")


def stability_words(spread: Fraction) -> str:
    if spread <= 2:
        return "꾸준히 이 근처에 있었다"
    if spread <= 5:
        return "순위가 오르내렸다"
    return "순위가 많이 흔들렸다"


def stability(spread: Fraction) -> str:
    return (f"최근 {slot('EV-STAB-DAYS')}영업일 평균 순위는 **{slot('EV-STAB-MEAN', 'dec1')}위**이고 "
            f"진폭은 ±{slot('EV-STAB-SPREAD', 'dec1')} 다 — {stability_words(spread)}.")


STAB_MISSING = f"최근 {slot('EV-STAB-DAYS')}영업일 이력이 모자라 '오늘만 반짝인 것인지' 는 말할 수 없다."


# ── 믿어도 되나 ─────────────────────────────────────────────────────────────

def trust_rank(label: str) -> str:
    return (f"{_who(label)} {slot('EV-TOTAL')}개 섹터 중 **{slot('EV-RANK')}위**다 — "
            f"이 자리를 겨냥한 반론 {slot('EV-AT-TOTAL')}개를 데이터로 세웠다.")


TRUST_COUNTS = (f"데이터가 막은 것 {slot('EV-AT-PASS')}개 · 못 막은 것 {slot('EV-AT-WEAK')}개 · "
                f"더 봐야 하는 것 {slot('EV-AT-OPEN')}개다.")


# ── 무엇이 바뀌었나 ─────────────────────────────────────────────────────────

def no_past(label: str) -> str:
    return f"{_who(label)} 견줄 과거 기준일을 정하지 못했다 — 아래 빈칸 목록이 이유다."


_CONFIRMED = slot("EV-CONFIRMED-DATE", "date")
NO_NEW_DAY = f"확정한 날(KST {_CONFIRMED}) 이후 새로 들어온 기준일이 없다 — 견줄 것이 없다."
SINCE = f"확정한 날(KST {_CONFIRMED}) 전의 마지막 기준일과 견준다."
DEFAULT_WINDOW = f"비교할 때를 적지 않아 기본값인 {slot('EV-WINDOW')}영업일 전과 견준다."
_THEN = f"{slot('EV-WINDOW')}영업일 전({slot('EV-PAST-ASOF', 'date')})"
_NOW = f"기준일({slot('EV-ASOF', 'date')})"
RANK_MISSING = f"{_THEN}과 {_NOW} 중 순위가 없는 날이 있어 순위는 견주지 않는다."


def rank_change(label: str, move: str) -> str:
    """`move` 는 up · down · same — 🔒 순위 숫자가 작아지면 오른 것이다."""
    diff = slot("EV-RANK-DIFF")
    moved = {"up": f"{diff}계단 올랐다", "down": f"{diff}계단 내려갔다", "same": "그대로다"}[move]
    return (f"{_who(label)} {_THEN} **{slot('EV-PAST-RANK')}위**에서 {_NOW} **{slot('EV-RANK')}위**로 — "
            f"순위가 {moved}.")


def z_change(axis: str, rising: bool) -> str:
    return (f"{AXIS_NAMES[axis]} σ 가 {slot(f'EV-PAST-{axis}-Z', 'sigma')}σ 에서 "
            f"{slot(f'EV-{axis}-Z', 'sigma')}σ 로 {'높아졌다' if rising else '낮아졌다'}.")


Z_SAME = "네 축의 σ 가 두 기준일에서 같거나, 한쪽이 없어 견줄 수 없다."


# ── 무엇이 들어 있나 ────────────────────────────────────────────────────────

def no_master(label: str) -> str:
    return f"{_who(label)} 섹터 정의를 읽지 못해 구성을 말할 수 없다."


def contents_head(label: str) -> str:
    return (f"{_who(label)} GICS **{slot('EV-GICS', 'text')}** 에 속하고, "
            f"ETF **{slot('EV-ETF-COUNT')}개** · 구성종목 **{slot('EV-MEMBER-COUNT')}개**로 묶었다.")


def etf_line(index: int) -> str:
    return f"ETF — {slot(f'EV-ETF-{index}', 'text')}"


def member_line(index: int) -> str:
    return f"구성종목 — {slot(f'EV-MEMBER-{index}', 'text')}"


ETF_COUNT = f"그중 기준일 점수에 들어간 ETF 는 {slot('EV-ETF-N')}개다."
ETF_UNKNOWN = "그중 기준일 점수에 들어간 ETF 수는 알 수 없다."


def liquidity(ok: bool | None) -> str:
    """🔒 "아직 모른다" 와 "미달이다" 는 다른 말이다."""
    if ok is None:
        return f"유동성: — ({slot('EV-RULE-SHORT')}영업일이 아직 안 찼다)"
    if ok:
        return f"유동성: 충분 (일평균 거래대금 {slot('EV-RULE-LIQ')}억 이상)"
    return "🔴 유동성 미달 — ETF 로는 실제로 거래하기 어렵다. 구성종목을 본다"


# ── 카드 제목 ───────────────────────────────────────────────────────────────

def axis_title(axis: str, lifted: bool) -> str:
    return f"{AXIS_NAMES[axis]} 축 — 순위를 {'끌어올렸다' if lifted else '끌어내렸다'}"


def change_title(axis: str, rising: bool) -> str:
    return f"{AXIS_NAMES[axis]} 축 — σ 가 {'높아졌다' if rising else '낮아졌다'}"
