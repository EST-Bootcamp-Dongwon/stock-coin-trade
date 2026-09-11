"""정산 산식 — **DB 를 모르는 순수 함수 모음** (F-04 5·6장 · F-05 2·3장).

`contests/rules.py` 가 *사전 차단*의 판정을 담았다면, 여기는 *사후 판정*의 계산이다.
기준가(NAV) · 회전율 · 허핀달 지수 · 백분위 · 등급이 전부 여기 있다.

★★ **왜 별도 모듈인가** ─────────────────────────────────────────────────────

이 산식들은 **1회 대회를 돌려본 뒤 조정하는 것을 전제**한다 (F-04 6.2 —
타임폴리오도 *"관리 점수 산출 항목이나 가중치 등은 변경될 수 있습니다"* 라고
명시했다). 조정할 때 건드릴 곳이 한 파일이어야 하고, 무엇보다 **DB 없이 시험할 수
있어야** 한다. 참가자 100명의 스냅샷을 만들어 놓고 산식을 고치는 것은 실험이 아니다.

    contests/scoring.py   숫자 → 숫자          ← DB 를 모른다. 테스트가 즉시 돈다
    contests/services.py  DB → 숫자 → DB       ← 여기가 조회·저장을 맡는다
    contests/jobs.py      job_run 으로 감싼다   ← 실행 정책(기록·재시도)

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 프로젝트였다면 이런 계산을 Pydantic 모델의 `@computed_field` 나
`@property` 로 응답 스키마에 붙이는 일이 흔했다. **여기서는 모델에 붙이지 않는다.**
`DailySnapshot.nav` 는 *계산해서 저장하는 값*이지 *읽을 때 파생되는 값*이 아니다 —
정산 시점의 산식으로 굳혀 둬야 나중에 산식을 바꿔도 과거 대회가 흔들리지 않는다.
Django 의 `@property` 로 만들면 그 구분이 흐려진다.

숫자 규약 ───────────────────────────────────────────────────────────────────

**전부 `Decimal` 이다.** `float` 로 비율을 다루면 0.1 + 0.2 ≠ 0.3 이 정산에 들어온다.
DB 에는 항상 **퍼센트 값**(15.0)을 넣는다. 비율(0.15)이 아니다 (규약 4.2).
"""

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Mapping, Sequence

# 기준가의 출발점. 시작 자본이 얼마든 첫날은 1000 이다 (F-05 2.2).
NAV_BASE = Decimal(1000)

# 자릿수 — `core/fields.py` 의 컬럼 정의와 짝이 맞아야 한다.
# 계산 도중에는 자르지 않고 **저장 직전 한 번만** 맞춘다. 중간에 반올림하면
# 오차가 누적된다 (특히 종목별 비중을 합산할 때).
PCT_QUANT = Decimal("0.0001")       # core.fields.PCT  (9,4)
NAV_QUANT = Decimal("0.0001")       # core.fields.NAV  (12,4)
HHI_QUANT = Decimal("0.000001")     # core.fields.HHI  (9,6)

# ★ **수익 분산 점수의 가점 상한** (F-04 6.2 — `min(수익종목수 / 5, 1)`).
#   "수익 종목이 많을수록 가점" 조항을 5종목에서 만점으로 본다.
PNL_WINNER_TARGET = Decimal(5)

# ★★ **등급 구간 — v2.0 이 정한 값이다** ─────────────────────────────────────
#
#   F-04 6.3 은 *"A+ 를 부여하는 절대적인 점수 컷트라인은 없고, 대회별로 상대평가"*
#   까지만 정하고 **구간은 주지 않았다.** 그래서 여기서 정한다.
#
#   `(하한 백분위, 등급)` 을 **내림차순**으로 둔다. 최종 점수의 백분위가 하한 이상이면
#   그 등급이다. 마지막 항목이 0 이라 어떤 값이든 등급을 받는다.
#
#   ★ **참가자가 적으면 위쪽 등급이 안 나온다.** 백분위 정의상(아래 `percentiles`)
#     8명 대회의 1등은 93.75 이고 A+ 하한 95 에 못 미친다. 이것은 **버그가 아니라
#     상대평가의 성질**이다 — 8명 중 1등에게 "상위 5%" 라고 말할 수 없다.
#     동아리 대회에서 A+ 를 보고 싶다면 참가자를 20명 이상 모으거나 이 표를 낮춘다.
GRADE_BANDS: tuple[tuple[Decimal, str], ...] = (
    (Decimal(95), "A+"),
    (Decimal(85), "A"),
    (Decimal(70), "B+"),
    (Decimal(50), "B"),
    (Decimal(30), "C+"),
    (Decimal(15), "C"),
    (Decimal(5), "D"),
    (Decimal(0), "F"),
)


# ─────────────────────────────────────────────────────────────────
# 1. 기준가(NAV)와 수익률 (F-05 2.2)
# ─────────────────────────────────────────────────────────────────


def nav_for(total_asset: int, initial_capital: int) -> Decimal:
    """순자산을 기준가로 환산한다.

        NAV = 1000 × (현재 순자산 / 시작 자본)

    시작 자본이 다른 대회를 같은 축에 놓기 위한 지수다 (F-05 2.2).

    >>> nav_for(110_000_000, 100_000_000)
    Decimal('1100.0000')

    ★ `initial_capital` 이 0 이면 **1000 을 돌려준다.** 0 으로 나눌 수는 없고,
      그렇다고 예외를 던지면 대회 하나의 설정 실수로 정산 잡 전체가 멈춘다.
      "아무 변화 없음"으로 두는 편이 안전하다 — 화면에는 수익률 0% 로 보인다.
    """
    if initial_capital <= 0:
        return NAV_BASE.quantize(NAV_QUANT)
    return (NAV_BASE * Decimal(total_asset) / Decimal(initial_capital)).quantize(NAV_QUANT)


def cumulative_return_pct(nav: Decimal) -> Decimal:
    """누적 수익률 % — `(NAV - 1000) / 10`.

    NAV 를 1000 으로 잡은 덕에 나눗셈 한 번으로 끝난다 (F-05 2.2 표의 세 번째 줄).
    """
    return ((Decimal(nav) - NAV_BASE) / Decimal(10)).quantize(PCT_QUANT)


def daily_return_pct(nav: Decimal, prev_nav: Decimal | None) -> Decimal:
    """전일 대비 수익률 %.

    Args:
        prev_nav: 전 영업일의 NAV. **대회 첫날에는 없다** — 그때는 시작 자본이
            기준이므로 누적 수익률과 같은 값이 된다.

    ★ `prev_nav` 가 0 이하인 경우도 첫날과 똑같이 다룬다. 순자산이 0 이 된 계좌는
      나눗셈의 분모가 될 수 없고, 그 상태에서 "전일 대비 몇 %" 는 의미가 없다.
    """
    if prev_nav is None or Decimal(prev_nav) <= 0:
        return cumulative_return_pct(nav)
    return ((Decimal(nav) / Decimal(prev_nav) - 1) * 100).quantize(PCT_QUANT)


def ratio_pct(value: int | Decimal, total: int | Decimal) -> Decimal:
    """비중 % 를 낸다. 분모가 0 이하면 0.

    편입비(`position_value / total_asset`)와 종목 비중이 함께 쓴다.
    """
    total = Decimal(total)
    if total <= 0:
        return Decimal(0).quantize(PCT_QUANT)
    return (Decimal(value) / total * 100).quantize(PCT_QUANT)


# ─────────────────────────────────────────────────────────────────
# 2. 주간 회전율 (F-04 5.1)
# ─────────────────────────────────────────────────────────────────


def turnover_pct(buy_amount: int, sell_amount: int, avg_asset: int) -> Decimal:
    """주간 회전율 %.

        (기간 내 매수총액 + 매도총액) / 기간 평균 운용금액 × 0.5 × 100

    ★ **왜 0.5 를 곱하는가** — 사고판 것을 둘 다 더하면 한 번의 왕복매매가 두 번으로
      세어진다. 0.5 는 그것을 한 번으로 되돌린다. 업계 관행이고 F-04 5.1 의 확정 산식이다.

    ★ `avg_asset` 이 0 이면 0 을 돌려준다. **그리고 그 값은 "위반"으로 판정된다**
      (`is_turnover_violation` 참조) — 운용금액이 0 인 참가자는 애초에 매매를 하지
      않았다는 뜻이라, 회전율 기준을 못 지킨 것이 맞다.
    """
    if avg_asset <= 0:
        return Decimal(0).quantize(PCT_QUANT)
    total = Decimal(buy_amount) + Decimal(sell_amount)
    return (total / Decimal(avg_asset) * Decimal("0.5") * 100).quantize(PCT_QUANT)


def is_turnover_violation(pct: Decimal, minimum_pct: Decimal | float) -> bool:
    """회전율이 기준에 못 미치는가 (기본 주간 5% · F-04 5.1).

    ★ **미만이 위반이고 같으면 통과다.** "5% 이상 유지" 가 기준이므로 정확히 5.0 은
      위반이 아니다. 부등호 방향을 여기 한 곳에 가둬 둔다 — 잡·화면·테스트가
      제각기 `<` 와 `<=` 를 적으면 경계에서 답이 갈린다.
    """
    return Decimal(pct) < Decimal(str(minimum_pct))


# ─────────────────────────────────────────────────────────────────
# 3. 집중도 — 허핀달 지수 (F-04 6.2)
# ─────────────────────────────────────────────────────────────────


def herfindahl(values: Iterable[int | Decimal]) -> Decimal:
    """허핀달 지수 — `Σ (각 몫 / 전체)²`. 0 에 가까울수록 분산, 1 이면 몰빵.

    >>> herfindahl([50, 50])
    Decimal('0.500000')
    >>> herfindahl([100])
    Decimal('1.000000')

    ★ **음수는 버린다.** 손익 집중도를 잴 때 손실 종목이 섞여 들어오면 분모가
      실제 수익보다 작아져 지수가 1 을 넘는다. 호출부가 걸러 넘기는 것이 원칙이지만
      여기서도 한 겹 막는다 — HHI 컬럼은 `max_digits=9, decimal_places=6` 이라
      1 을 넘는 값이 들어와도 저장은 되고, **틀린 채로 조용히 남는다.**

    ★ 합이 0 이면 **1(최악)** 을 돌려준다. 0 을 주면 "완전히 분산됐다"가 되어
      **아무것도 안 한 사람이 만점**을 받는다. 분산 점수의 취지와 정반대다.
    """
    positives = [Decimal(v) for v in values if Decimal(v) > 0]
    total = sum(positives, Decimal(0))
    if total <= 0:
        return Decimal(1).quantize(HHI_QUANT)
    index = sum(((v / total) ** 2 for v in positives), Decimal(0))
    return index.quantize(HHI_QUANT)


def average_weights(daily_values: Sequence[Mapping[str, int]]) -> dict[str, Decimal]:
    """일별 보유 평가액에서 **종목별 평균 비중**을 낸다 (F-04 6.2).

    Args:
        daily_values: 하루치씩의 `{종목: 평가액}`. 보유가 없는 날은 빈 dict 로 넣는다.

    Returns:
        `{종목: 평균 비중}` — **합이 1 이다** (보유가 하루도 없으면 빈 dict).

    ★★ **왜 "일별 비중의 평균" 이 아니라 "평가액 합의 비중" 인가** ────────────

    F-04 6.2 는 *"종목별 평균 비중은 일별 스냅샷의 평균"* 이라고만 적었다.
    그대로 읽으면 두 가지 계산이 가능한데 결과가 다르다::

        ① 일별 비중을 먼저 내고 평균     Σ_d (v_d / total_d) / 날짜수
        ② 평가액을 먼저 합치고 비중       Σ_d v_d / Σ_d total_d      ← 이걸 쓴다

    ①은 **보유가 없는 날을 어떻게 셀지**가 정해지지 않는다. 분모에서 빼면
    "19일 현금 + 1일 몰빵" 이 몰빵 하루만으로 평가되고, 넣으면 현금으로 앉아 있을수록
    비중이 희석돼 점수가 오른다. **둘 다 게임이 된다.**

    ②는 그 문제가 없다. 현금으로 있던 날은 분자·분모에 똑같이 0 을 더할 뿐이라
    유리해지지도 불리해지지도 않고, 오래 크게 들고 있던 종목일수록 비중이 커진다 —
    "무엇을 얼마나 오래 담았는가" 라는 원래 의도에 맞는다.

    ★ 현금 비중은 **분모에서 뺀다.** 순자산 대비로 재면 현금 100% 인 참가자의
      HHI 가 0 이 되어 **분산 만점**을 받는다. 관리 점수는 *포트폴리오 안에서*
      몰빵했는지를 보는 지표다. 매매를 안 하는 것은 회전율 규칙(F-04 5장)이 잡는다.
    """
    totals: dict[str, Decimal] = {}
    grand_total = Decimal(0)
    for day in daily_values:
        for symbol, value in day.items():
            amount = Decimal(value)
            if amount <= 0:
                continue
            totals[symbol] = totals.get(symbol, Decimal(0)) + amount
            grand_total += amount
    if grand_total <= 0:
        return {}
    return {symbol: value / grand_total for symbol, value in totals.items()}


# ─────────────────────────────────────────────────────────────────
# 4. 관리 점수 (F-04 6.2)
# ─────────────────────────────────────────────────────────────────


def portfolio_dispersion_score(port_hhi: Decimal) -> Decimal:
    """포트 분산 점수 = `(1 - HHI_port) × 100`. 0~100."""
    return ((1 - Decimal(port_hhi)) * 100).quantize(PCT_QUANT)


def pnl_dispersion_score(pnl_hhi: Decimal, winner_count: int) -> Decimal:
    """수익 분산 점수 = `(1 - HHI_pnl) × 100 × min(수익종목수 / 5, 1)`.

    뒤의 계수가 *"수익 종목이 많을수록 가점"* 조항이다 (F-04 6.1 표).
    수익 종목이 1개뿐이면 아무리 분산돼 보여도 점수의 1/5 만 받는다.
    """
    if winner_count <= 0:
        return Decimal(0).quantize(PCT_QUANT)
    bonus = min(Decimal(winner_count) / PNL_WINNER_TARGET, Decimal(1))
    return ((1 - Decimal(pnl_hhi)) * 100 * bonus).quantize(PCT_QUANT)


def management_score(
    port_score: Decimal,
    pnl_score: Decimal,
    *,
    port_weight: Decimal | float = 0.5,
    pnl_weight: Decimal | float = 0.5,
) -> Decimal:
    """관리 원점수 = `포트 분산 × 0.5 + 수익 분산 × 0.5` (F-04 6.2).

    가중치는 `Contest.rule_set` 에서 넘어온다 — 대회마다 조정 가능해야 한다.
    """
    return (
        Decimal(port_score) * Decimal(str(port_weight))
        + Decimal(pnl_score) * Decimal(str(pnl_weight))
    ).quantize(PCT_QUANT)


# ─────────────────────────────────────────────────────────────────
# 5. 상대평가 — 순위 · 백분위 · 등급 (F-04 6.3 · F-05 3.1)
# ─────────────────────────────────────────────────────────────────


def competition_ranks(values: Sequence[Decimal]) -> list[int]:
    """값이 클수록 1등인 **경쟁 순위**를 매긴다. 동점은 같은 순위, 다음은 건너뛴다.

    >>> competition_ranks([Decimal(10), Decimal(10), Decimal(5)])
    [1, 1, 3]

    ★ **왜 조밀 순위(1-1-2)가 아닌가** — 랭킹 화면이 "몇 등 / 전체 몇 명" 을 함께
      보여준다. 조밀 순위를 쓰면 100명 대회에서 최하위가 40등처럼 보인다.
      경쟁 순위는 등수와 인원수가 어긋나지 않는다.

    ★ 반환은 **입력 순서 그대로**다. 정렬해서 돌려주면 호출부가 참가자와 짝을
      다시 맞춰야 하고, 그때 어긋나면 **남의 순위가 내 화면에 뜬다.**
    """
    ordered = sorted(range(len(values)), key=lambda i: Decimal(values[i]), reverse=True)
    ranks = [0] * len(values)
    previous: Decimal | None = None
    previous_rank = 0
    for position, index in enumerate(ordered, start=1):
        value = Decimal(values[index])
        if previous is not None and value == previous:
            ranks[index] = previous_rank          # 동점 — 앞 사람과 같은 등수
        else:
            ranks[index] = position               # 새 값 — 자기 자리(건너뛴 등수)
            previous, previous_rank = value, position
    return ranks


def percentiles(values: Sequence[Decimal]) -> list[Decimal]:
    """백분위 — **클수록 좋다.** 0~100.

        백분위 = (나보다 낮은 수 + 나와 같은 수 × 0.5) / 전체 × 100

    통계학의 표준 정의(mid-rank)다. 동점자가 서로 다른 백분위를 받지 않고,
    참가자가 1명이면 50 이 된다.

    >>> percentiles([Decimal(10), Decimal(5), Decimal(1)])
    [Decimal('83.3333'), Decimal('50.0000'), Decimal('16.6667')]

    ★ **100 도 0 도 나오지 않는다.** 1등이라고 "100%" 를 주면 *더 잘할 수 없다*는
      뜻이 되는데 상대평가에서 그런 값은 존재할 수 없다. 이 성질 때문에 참가자가
      적은 대회에서는 위쪽 등급이 안 나온다 (`GRADE_BANDS` 주석 참조).
    """
    total = len(values)
    if total == 0:
        return []
    decimals = [Decimal(v) for v in values]
    result = []
    for value in decimals:
        below = sum(1 for other in decimals if other < value)
        equal = sum(1 for other in decimals if other == value)
        rank = (Decimal(below) + Decimal(equal) * Decimal("0.5")) / Decimal(total)
        result.append((rank * 100).quantize(PCT_QUANT))
    return result


def final_score(
    return_percentile: Decimal,
    management_percentile: Decimal,
    *,
    return_weight: Decimal | float = 0.7,
    management_weight: Decimal | float = 0.3,
) -> Decimal:
    """최종 점수 = `수익 백분위 × 0.7 + 관리 백분위 × 0.3` (F-04 6.3).

    ★ **원점수가 아니라 백분위를 가중합한다.** 수익률(%)과 관리 점수(0~100)는
      단위도 분포도 달라서 그대로 더하면 한쪽이 압도한다. 백분위로 옮기면
      **둘 다 0~100 의 같은 자로 재는 값**이 되어 가중치가 의도대로 먹는다.
    """
    return (
        Decimal(return_percentile) * Decimal(str(return_weight))
        + Decimal(management_percentile) * Decimal(str(management_weight))
    ).quantize(PCT_QUANT)


def grade_for(percentile: Decimal) -> str:
    """최종 점수의 백분위 → 등급 (A+ ~ F). `GRADE_BANDS` 참조."""
    value = Decimal(percentile)
    for floor, grade in GRADE_BANDS:
        if value >= floor:
            return grade
    return GRADE_BANDS[-1][1]


def round_krw(value: Decimal) -> int:
    """원 단위 정수로 맞춘다. 금액 컬럼은 전부 `BigIntegerField` 다 (규약 2.2).

    ★ 여기서만은 **반올림**이다. 체결 경로의 수수료·세금은 참가자에게 유리하게
      절사(floor)하지만(규약 2.2), 평가액은 돈이 오가는 값이 아니라 *재는 값*이다.
      매번 내림하면 종목이 많을수록 순자산이 조금씩 낮게 잡혀 편향이 쌓인다.
    """
    return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


__all__ = [
    "GRADE_BANDS",
    "NAV_BASE",
    "average_weights",
    "competition_ranks",
    "cumulative_return_pct",
    "daily_return_pct",
    "final_score",
    "grade_for",
    "herfindahl",
    "is_turnover_violation",
    "management_score",
    "nav_for",
    "percentiles",
    "pnl_dispersion_score",
    "portfolio_dispersion_score",
    "ratio_pct",
    "round_krw",
    "turnover_pct",
]
