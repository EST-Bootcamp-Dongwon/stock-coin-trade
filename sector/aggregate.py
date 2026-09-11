"""원천 → `sector_daily` 집계. **스코어링의 유일한 입력을 만드는 곳이다.**

## 이 모듈의 한 가지 생각 — 수익률로 지수를 만든다

KRX 종가는 **무수정 주가**다. 액면분할·병합·유상증자 권리락이 끼면
`close_t / close_{t-1} − 1` 이 −50% 같은 가짜 수익률을 낸다. 반면 원천이 주는
`FLUC_RT` 는 **거래소가 조정한 기준가 대비** 등락률이다.

그래서 이 모듈은 종가를 수익률의 재료로 쓰지 않는다. `fluc_rt_bp` 를 곱해 나가
**체인연결(chain-link) 지수**를 만든다 —

```
level_t = level_{t-1} × (1 + r_t)        level_0 = 100
```

이 시계열은 **수정주가 시계열과 동등**하고, 자본변동을 따로 탐지할 필요가 없다.
이동평균(SMA20)도 폭(breadth)도 전부 이 level 위에서 계산한다. 종가는 표시와
유동성 판단에만 쓴다.

🔒 **level 의 절대값에는 뜻이 없다.** 종목마다 관측 시작일이 달라 기준일이 다르기
   때문이다. 뜻이 있는 것은 **비율**뿐이다 — `level_T / level_{T-20}`,
   `level_T / SMA20`. 스코어링 4축이 전부 비율만 쓰므로(계획서 D-1) 문제가 없고,
   화면에 level 자체를 "가격"으로 보여주면 안 된다.

## 룩어헤드가 구조적으로 불가능하다

날짜를 **오름차순으로 한 번만** 훑으며 상태를 갱신한다. `t` 를 계산하는 시점에
`t` 이후를 **가진 적이 없다.** 그래서 `data[:T+30]` 으로 계산한 `row(T)` 와
`data[:T]` 로 계산한 값이 같다 — 계획서 D-2 의 `test_asof_monotone` 이 요구하는
성질이 알고리즘에서 나온다. `as_of` 는 **기본값 없는 키워드 인자**다(D-2 ①).

## 🔴 채우지 않는다

관측이 없는 날은 지수가 **전진하지 않고**, 그날 값은 `None` 으로 나간다.
전일값·0·평균으로 메우지 않는다. 대신 `is_partial` 이 "이 행은 온전하지 않다"고
말한다 (→ ADR-SC-0007).

## 원천 복원 가능성을 낮춘다 (약관 제11조② → ADR-SC-0006)

섹터에 ETF 가 하나뿐이면 절대가격을 실을 경우 그 ETF 의 종가가 그대로 드러난다.
그래서 가격을 **지수로만** 싣는다 — 기준일이 관측 시작일이라 역산에 그날의
절대가격이 필요하고, 그 값은 어디에도 없다.

## 왜 `market_daily` 가 함께 나오는가

계획서 D-1 의 두 축이 **시장 기준선**을 요구한다 —
M 모멘텀은 `r20_s − r20_mkt`, B 폭은 `breadth_up_bp − (전체 시장 같은 비율)`.
같은 한 번의 훑기에서 나오고, 없으면 M7 이 시작될 수 없다.
🔒 기준선은 **우리가 이미 받는 주식 유니버스**에서 만든다 — 지수 API 를 따로
   부르지 않는다. 폭(breadth)과 같은 유니버스·같은 방법이어야 뺄셈이 뜻을 가진다.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN
from typing import Any

from sector.sector_master import SectorMaster

__all__ = [
    "BASE_IDX_BP",
    "AggregateResult",
    "DayFrame",
    "MarketDailyRow",
    "SectorDailyRow",
    "aggregate",
]

#: 기준일 지수 = 100.00 → bp 정수 10000. 🔒 `float` 를 쓰지 않는다(`AGENTS.md` 4장).
BASE_IDX_BP = 10_000

_BP = Decimal(10_000)
_BASE_LEVEL = Decimal(100)
_ONE = Decimal(1)

#: 이동평균·지연수익률 창. SMA20 에 20개, 20일 수익률에 21개가 필요하다.
#: 🔒 SMA120(밸류 축)은 여기서 만들지 않는다 — `sector_daily` 시계열 위에서
#:    M7 이 계산한다. 여기 창을 키우면 메모리만 먹는다.
_WINDOW = 20
_HISTORY = _WINDOW + 1


def _to_bp(value: Decimal | None) -> int | None:
    """bp 정수로 양자화. 🔒 저장 직전에 한 번만 — 중간 계산은 `Decimal` 로 간다."""
    if value is None:
        return None
    return int(value.quantize(_ONE, rounding=ROUND_HALF_EVEN))


# ── 출력 행 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class SectorDailyRow:
    """1행 = 1섹터 × 1영업일. **스코어링의 유일한 입력이다.**

    `None` 은 "그날 그 값을 만들 수 없었다"는 뜻이다 — 0 이 아니다.
    """

    bas_dd: str
    sector_id: str
    gics: str

    # ── ETF 렌즈 ──
    etf_n: int                      # 그날 실제로 집계에 들어간 ETF 수
    etf_idx_bp: int | None          # 순자산 가중 체인연결 지수 (기준일 10000 = 100.00)
    etf_ret_1d_bp: int | None       # 그날 섹터 ETF 수익률
    etf_ret_20d_bp: int | None      # 20영업일 수익률 — 모멘텀 축의 재료
    etf_shares_sum: int | None      # 상장좌수 합 (좌) — ★ 자금흐름 축의 유일한 입력
    etf_nav_sum: int | None         # 순자산총액 합 (원) — 🔒 점수에 쓰지 않는다(이중계산)
    etf_value_sum: int | None       # 거래대금 합 (원) — 🔒 유동성 게이트 전용(V15)
    etf_premium_bp: int | None      # (종가−NAV)/NAV 순자산가중 평균

    # ── 구성종목 렌즈 ── 🔒 ETF 렌즈와 평균 내지 않는다. 갈라지면 갈라진 채로 둔다
    member_n: int                   # 그날 실제로 집계에 들어간 종목 수
    member_idx_bp: int | None       # 동일가중 체인연결 지수
    member_ret_1d_bp: int | None
    member_ret_20d_bp: int | None
    breadth_up_bp: int | None       # 지수 > SMA20 인 종목 비율 (bp) — 폭 축의 재료
    breadth_n: int                  # 폭 판정이 가능했던 종목 수 (SMA20 이 선 종목만)

    source_ids: tuple[str, ...]
    is_partial: bool                # 🔴 설정된 것 전부를 담지 못했다는 표시
    fetched_at: str                 # UTC ISO


@dataclass(frozen=True, slots=True)
class MarketDailyRow:
    """1행 = 1영업일. 모멘텀·폭 축이 빼는 **시장 기준선**이다."""

    bas_dd: str
    stock_n: int
    mkt_idx_bp: int | None          # 시가총액 가중 체인연결 지수
    mkt_ret_1d_bp: int | None
    mkt_ret_20d_bp: int | None
    eqw_idx_bp: int | None          # 동일가중 — 대형주 쏠림을 빼고 본 시장
    breadth_up_bp: int | None       # 전체 시장에서 지수 > SMA20 인 비율
    breadth_n: int
    is_partial: bool
    fetched_at: str


@dataclass(frozen=True, slots=True)
class AggregateResult:
    sectors: tuple[SectorDailyRow, ...]
    market: tuple[MarketDailyRow, ...]
    bas_dds: tuple[str, ...]


#: 하루치 입력 — `(bas_dd, ETF 시세, 주식 시세)`.
#: 어댑터의 값 객체를 그대로 받는다. 🔒 이 모듈은 KRX 필드명을 모른다.
DayFrame = tuple[str, Sequence[Any], Sequence[Any]]


# ── 체인연결 지수 ────────────────────────────────────────────────────────────

class _Series:
    """한 대상(종목·섹터·시장)의 체인연결 지수와 짧은 이동평균.

    🔒 **과거만 본다.** `advance` 는 그날 수익률 하나만 받고, 미래를 알 방법이 없다.
    """

    __slots__ = ("level", "_levels")

    def __init__(self) -> None:
        self.level: Decimal | None = None
        self._levels: deque[Decimal] = deque(maxlen=_HISTORY)

    def advance(self, ret: Decimal | None) -> bool:
        """오늘 수익률로 한 걸음 나아간다. 관측이 없으면 **전진하지 않는다.**

        전일값으로 메우면 이동평균이 조용히 평평해지고 20일 수익률이 짧아진다.
        차라리 그날을 비운다 (→ ADR-SC-0007).
        """
        if ret is None:
            return False
        base = _BASE_LEVEL if self.level is None else self.level
        self.level = base * (_ONE + ret)
        self._levels.append(self.level)
        return True

    @property
    def idx_bp(self) -> int | None:
        """지수를 bp 로. 기준일 100.00 → 10000."""
        return None if self.level is None else _to_bp(self.level * 100)

    def lagged_return(self, lag: int = _WINDOW) -> Decimal | None:
        """`lag` **관측** 전 대비 수익률. 관측이 모자라면 `None` — 0 이 아니다.

        ★ "관측"이지 "달력"이 아니다. 중간에 값이 없어 전진하지 못한 날이 있으면
          이 창은 달력상 20일보다 길어진다. 그래도 이쪽을 고른 이유 —
          달력 기준으로 맞추려면 빈 날을 **메워야** 하고, 그게 정확히 하지 않기로 한
          일이다(ADR-SC-0007). 창이 늘어난 행은 `is_partial` 이 표시한다.
        """
        if len(self._levels) < lag + 1 or self.level is None:
            return None
        past = self._levels[-(lag + 1)]
        if past == 0:
            return None
        return self.level / past - _ONE

    def above_sma(self, window: int = _WINDOW) -> bool | None:
        """지수가 자기 이동평균 위인가. 관측이 모자라면 `None` — `False` 가 아니다.

        ★ 이 구별이 폭(breadth)의 정직성이다. 상장 직후 종목을 `False` 로 세면
          신규 상장이 많은 섹터의 폭이 가짜로 낮아진다.
        """
        if len(self._levels) < window or self.level is None:
            return None
        recent = list(self._levels)[-window:]
        return self.level > sum(recent) / Decimal(window)


#: `MKT_NM` → `data/raw/` 원천 키. 🔒 문자열을 그때그때 변형하지 않는다 —
#: 원천이 이름을 바꾸면 조용히 이상한 키가 만들어지는 대신 목록에서 빠진다.
_SOURCE_OF_MKT: Mapping[str, str] = {"KOSPI": "stk", "KOSDAQ": "ksq"}


def _ret_bp(ratio: Decimal | None) -> int | None:
    """비율(`Decimal`)을 bp 정수로. `None` 은 그대로."""
    return None if ratio is None else _to_bp(ratio * _BP)


def _ret(bp: int | None) -> Decimal | None:
    """bp 정수 수익률을 비율로. `None` 은 그대로 `None`."""
    return None if bp is None else Decimal(bp) / _BP


def _weighted_return(
    parts: Sequence[tuple[Decimal, Decimal]],
) -> Decimal | None:
    """`(가중치, 수익률)` 목록의 가중평균. 가중치 합이 0 이면 `None`."""
    total = sum((w for w, _ in parts), Decimal(0))
    if total <= 0:
        return None
    return sum((w * r for w, r in parts), Decimal(0)) / total


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return sum(values, Decimal(0)) / Decimal(len(values))


# ── 집계 ─────────────────────────────────────────────────────────────────────

def aggregate(
    frames: Iterable[DayFrame],
    master: SectorMaster,
    *,
    as_of: str,
    fetched_at: str,
) -> AggregateResult:
    """날짜 프레임을 오름차순으로 훑어 `sector_daily` · `market_daily` 를 만든다.

    `as_of` 에 **기본값이 없다.** 빠뜨린 호출이 조용히 전체 기간을 보는 일을
    막는다 (계획서 D-2 ①). `as_of` 보다 뒤인 날짜는 **버린다** — 들어오면 안 되는
    것이 들어왔다는 뜻이고, 조용히 섞이면 룩어헤드가 된다.

    🔒 입력은 **오름차순이어야 한다.** 순서가 어긋나면 체인연결이 무의미해지므로
       확인하고 던진다 — 정렬해 주지 않는다. 호출부가 무엇을 주는지 알아야 한다.
    """
    sectors = master.sectors
    etf_of: dict[str, list[str]] = {s.id: [i.code for i in s.etfs] for s in sectors}
    member_of: dict[str, list[str]] = {s.id: [i.code for i in s.members] for s in sectors}

    # 섹터별 지수 / 종목별 지수 / 시장 지수 — 전부 같은 `_Series` 다
    etf_series: dict[str, _Series] = {s.id: _Series() for s in sectors}
    member_series: dict[str, _Series] = {s.id: _Series() for s in sectors}
    stock_series: dict[str, _Series] = {}      # 종목코드 → 지수 (폭 판정용)
    mkt_cap_series = _Series()
    mkt_eqw_series = _Series()

    # 전일 가중치 원천. 🔒 **전일** 값을 쓴다 — 당일 순자산으로 당일 수익률을
    # 가중하면 그날 오른 ETF 에 더 큰 가중치가 붙어 수익률이 부풀려진다.
    prev_net_asset: dict[str, int] = {}
    prev_mktcap: dict[str, int] = {}

    sector_rows: list[SectorDailyRow] = []
    market_rows: list[MarketDailyRow] = []
    seen_days: list[str] = []

    for bas_dd, etf_quotes, stock_quotes in frames:
        if bas_dd > as_of:
            continue
        if seen_days and bas_dd <= seen_days[-1]:
            raise ValueError(
                f"날짜가 오름차순이 아니다: {seen_days[-1]} 다음에 {bas_dd}. "
                f"체인연결 지수는 순서에 의존한다 — 호출부가 정렬해서 준다."
            )
        seen_days.append(bas_dd)

        etf_by_code = {q.isu_cd: q for q in etf_quotes}
        stock_by_code = {q.isu_cd: q for q in stock_quotes}
        sources = tuple(sorted(
            ({"etf"} if etf_quotes else set())
            | {_SOURCE_OF_MKT[q.mkt_nm] for q in stock_quotes if q.mkt_nm in _SOURCE_OF_MKT}
        ))

        # ── 시장 기준선 ──
        # ⚠️ 유니버스는 **거르지 않은 전 종목**이다 — 우선주·SPAC·관리종목 포함.
        #    거르고 싶지만 두 시장을 **일관되게** 거를 수가 없다: `SECT_TP_NM` 이
        #    코스닥에만 있고 코스피는 전 행이 빈 문자열이다(V23 실측). 한쪽만 거르면
        #    기준선이 시장마다 다른 뜻을 갖는다. B 축은 이 값을 **빼서** 쓰므로
        #    (계획서 D-1) 상수 편향은 대부분 상쇄된다 — 거르지 않는 편이 정직하다.
        cap_parts: list[tuple[Decimal, Decimal]] = []
        eqw_parts: list[Decimal] = []
        breadth_hits = breadth_total = 0
        for code, quote in stock_by_code.items():
            ret = _ret(quote.fluc_rt_bp)
            series = stock_series.setdefault(code, _Series())
            series.advance(ret)
            verdict = series.above_sma()
            if verdict is not None:
                breadth_total += 1
                breadth_hits += int(verdict)
            if ret is None:
                continue
            eqw_parts.append(ret)
            weight = prev_mktcap.get(code)
            if weight:
                cap_parts.append((Decimal(weight), ret))

        cap_ret = _weighted_return(cap_parts)
        eqw_ret = _mean(eqw_parts)
        mkt_cap_series.advance(cap_ret)
        mkt_eqw_series.advance(eqw_ret)
        market_rows.append(MarketDailyRow(
            bas_dd=bas_dd,
            stock_n=len(stock_by_code),
            mkt_idx_bp=mkt_cap_series.idx_bp,
            mkt_ret_1d_bp=_ret_bp(cap_ret),
            mkt_ret_20d_bp=_ret_bp(mkt_cap_series.lagged_return()),
            eqw_idx_bp=mkt_eqw_series.idx_bp,
            breadth_up_bp=(
                _to_bp(Decimal(breadth_hits) / Decimal(breadth_total) * _BP)
                if breadth_total else None
            ),
            breadth_n=breadth_total,
            is_partial=not stock_by_code or cap_ret is None,
            fetched_at=fetched_at,
        ))

        # ── 섹터 ──
        for sector in sectors:
            partial = False

            # ETF 렌즈 — 순자산 가중
            etf_parts: list[tuple[Decimal, Decimal]] = []
            shares_sum = nav_sum = value_sum = 0
            have_shares = have_nav = have_value = False
            premium_parts: list[tuple[Decimal, Decimal]] = []
            etf_n = 0
            for code in etf_of[sector.id]:
                quote = etf_by_code.get(code)
                if quote is None:
                    partial = True     # 설정에 있으나 그날 원천에 없다
                    continue
                etf_n += 1
                if quote.list_shrs is not None:
                    shares_sum += quote.list_shrs
                    have_shares = True
                else:
                    partial = True
                if quote.net_asset_total is not None:
                    nav_sum += quote.net_asset_total
                    have_nav = True
                else:
                    partial = True
                if quote.acc_trdval is not None:
                    value_sum += quote.acc_trdval
                    have_value = True
                else:
                    partial = True

                ret = _ret(quote.fluc_rt_bp)
                weight = prev_net_asset.get(code)
                if ret is not None and weight:
                    etf_parts.append((Decimal(weight), ret))
                elif ret is None:
                    partial = True

                # 괴리율 — **당일** 순자산 가중. NAV 가 0 이면 나눌 수 없다.
                # ★ 수익률과 달리 전일 가중을 쓰지 않는다. 전일 가중은 "그날 오른
                #   ETF 에 큰 가중치가 붙어 수익률이 부풀려지는" 것을 막으려는
                #   장치인데, 괴리율은 **수익률이 아니라 그날의 상태값**이라 그
                #   편향이 없다. 전일 가중을 고집하면 신규 상장 ETF 의 괴리율이
                #   이유 없이 하루 비는 것만 남는다.
                if quote.nav and quote.close_prc is not None and quote.net_asset_total:
                    premium = (Decimal(quote.close_prc) - quote.nav) / quote.nav * _BP
                    premium_parts.append((Decimal(quote.net_asset_total), premium))

            if etf_of[sector.id] and not etf_parts:
                partial = True
            etf_ret = _weighted_return(etf_parts)
            etf_series[sector.id].advance(etf_ret)

            # 구성종목 렌즈 — 동일가중
            member_rets: list[Decimal] = []
            m_hits = m_total = 0
            member_n = 0
            for code in member_of[sector.id]:
                quote = stock_by_code.get(code)
                if quote is None:
                    partial = True
                    continue
                member_n += 1
                ret = _ret(quote.fluc_rt_bp)
                if ret is None:
                    partial = True
                else:
                    member_rets.append(ret)
                verdict = stock_series[code].above_sma()
                if verdict is not None:
                    m_total += 1
                    m_hits += int(verdict)
            member_ret = _mean(member_rets)
            member_series[sector.id].advance(member_ret)

            e_series = etf_series[sector.id]
            m_series = member_series[sector.id]
            sector_rows.append(SectorDailyRow(
                bas_dd=bas_dd,
                sector_id=sector.id,
                gics=sector.gics,
                etf_n=etf_n,
                etf_idx_bp=e_series.idx_bp,
                etf_ret_1d_bp=_ret_bp(etf_ret),
                etf_ret_20d_bp=_ret_bp(e_series.lagged_return()),
                etf_shares_sum=shares_sum if have_shares else None,
                etf_nav_sum=nav_sum if have_nav else None,
                etf_value_sum=value_sum if have_value else None,
                etf_premium_bp=_to_bp(_weighted_return(premium_parts)),
                member_n=member_n,
                member_idx_bp=m_series.idx_bp,
                member_ret_1d_bp=_ret_bp(member_ret),
                member_ret_20d_bp=_ret_bp(m_series.lagged_return()),
                breadth_up_bp=(
                    _to_bp(Decimal(m_hits) / Decimal(m_total) * _BP) if m_total else None
                ),
                breadth_n=m_total,
                source_ids=sources,
                is_partial=partial,
                fetched_at=fetched_at,
            ))

        # 🔒 다음 날의 가중치는 **오늘** 값이다. 루프 끝에서 갱신한다 —
        #    위에서 갱신하면 당일 가중이 되어 수익률이 부풀려진다.
        for code, quote in etf_by_code.items():
            if quote.net_asset_total is not None:
                prev_net_asset[code] = quote.net_asset_total
        for code, quote in stock_by_code.items():
            if quote.mktcap is not None:
                prev_mktcap[code] = quote.mktcap

    return AggregateResult(
        sectors=tuple(sector_rows),
        market=tuple(market_rows),
        bas_dds=tuple(seen_days),
    )
