"""`sector_daily` · `market_daily` → **4축 점수**. 판단이 숫자가 되는 곳이다.

계획서 D절의 구현이다. 축은 넷 —
모멘텀 35 / 자금흐름 30 / 폭 20 / 밸류 15 (→ `PRESETS`).

## 입력은 **집계 원본**이다 — 게시본이 아니다

`batch/build_sector_daily.py` 가 쓴 `data/derived/*.parquet` 을 그대로 읽는다.
게시본(`gate.project_sector`)이 아닌 이유가 둘 있다.

1. F 축이 쓰는 `etf_shares_sum` 은 게시본에서 `etf_shares_idx_bp` 로 **지수화**된다.
   지수화는 반올림을 한 번 넣으므로 원본을 쓰는 편이 정확하다(V31 실측 오차 1.6e-05).
2. 유동성 게이트가 쓰는 `etf_value_sum` 은 **게시되지 않는다** — ETF 가 1개인 섹터에서
   그 값이 원천과 같아지기 때문이다(V31 · ADR-SC-0006 ②). 그래서 `liquidity_ok` 라는
   **판정 결과만** `score_daily` 에 실어 앱에 넘긴다.

즉 이 모듈은 로컬에서만 돌고, 나가는 것은 점수·z·판정뿐이다.

## 🔴 창은 **영업일** 기준이고, 창이 온전할 때만 계산한다

`sector/aggregate.py` 의 `lagged_return` 은 *관측* 기준이다(값이 없는 날은 창이
달력상 길어진다). 스코어링은 **그럴 수 없다.** M 축이 `r20_섹터 − r20_시장` 이라
두 계열이 **같은 달력 구간**을 봐야 뺄셈이 뜻을 갖기 때문이다 — 섹터가 3일 쉬는
동안 시장은 안 쉬었다면, 관측 기준으로는 23영업일과 20영업일을 빼게 된다.

그래서 여기서는 창을 행(영업일)으로 잡고, **창 안에 관측이 하나라도 빠지면 그 축을
계산하지 않는다**(`None`). 전일값으로 메우면 그건 결측을 0 으로 채우는 것과 같다
(→ ADR-SC-0007). 못 채우면 못 채운 채로 두고, `axes_missing` 이 말한다.

## 🔴 M 축의 시장 기준선은 **동일가중**이다 (V28 의 답)

시총 상위 2종이 전체의 46% 라, 시총가중 지수는 하루 +17.6% 를 찍는 날이 실재한다.
둘 중 어느 쪽을 쓸지 실데이터(285영업일 × 21섹터)로 재 봤다 —

- **원시값(raw)은 최대 4354bp(43.5%p) 달라진다.**
- **z 는 최대 98bp(0.0098σ)** · score 는 40bp(0.0040σ) · 순위가 바뀐 행 **18/5985**.

z 가 거의 안 움직이는 것은 우연이 아니다. `r20_mkt` 는 모든 섹터에 **공통 상수**이고,
횡단면에서 중앙값을 빼는 순간 상수항이 소거된다. 남은 98bp 는 전부 **bp 양자화의
반올림**이다 — `raw_bp` 가 정확히 같은 1281행에서는 z 차이가 **0bp** 였다(실측).
즉 기준선이 바뀌며 남는 잔차는 같은 날 다른 섹터의 반올림이 중앙값·MAD 를 1bp 옮긴
것이고, 신호가 아니다.

점수로는 사실상 차이가 없으니 아무거나 골라도 된다 — 그런데 이 도구의 존재 이유는
점수가 아니라 **근거**다(계획서 D-5). 축 분해 표에 "시장 대비 +3%" 라고 적으려면
기준선이 실제로 시장이어야 한다. 시총가중은 그 문장을 거짓으로 만든다 — 사실상
"반도체 상위 2종 대비"이기 때문이다. 그래서 `eqw_idx_bp` 를 쓴다.
🔒 이 성질은 `scoring_golden_test.py` 가 고정한다. 나중에 누가 기준선을 바꿔도
   **raw 는 크게 움직이고 점수는 거의 안 움직인다**는 사실이 테스트로 남아 있어야 한다.

## 🔴 MAD 가 0 이 되는 날이 실제로 있다

폭(B) 축에서 **266일 중 45일**이 그렇다(실측). 섹터 구성종목이 3~8개뿐이라
`breadth_up_bp` 가 극도로 이산적이고(0% 18.8% · 100% 18.3%), 과반이 같은 값에
몰리면 중앙값 절대편차가 0 이 된다.

그 45일에 중앙값과 **다른** 섹터는 2~10개였다 — 0개인 날은 하루도 없다. 그러니
"변별 불가"가 아니다. 축을 통째로 버리면 **반드시 정보를 잃는다.**

그래서 척도를 한 단 내린다 — MAD 가 0 이면 **평균절대편차**(meanAD)를 쓴다.
`1.2533` 은 정규분포에서 meanAD→σ 환산 상수(√(π/2))로, `1.4826`(MAD→σ)과 같은
역할이다. 둘 다 0 이면 그때는 정말 모든 섹터가 같은 값이므로 축을 쓰지 않는다.

🔒 이것은 "없는 값을 지어내는 것"이 아니다 — 척도 **추정량**을 바꾸는 것이고,
   어느 쪽을 썼는지 `axes_degraded` 가 행마다 기록한다. 화면이 그걸 표시한다.

## 🔒 `float` 를 쓰지 않는다

전부 `Decimal` 이고 컨텍스트를 `_CONTEXT` 로 고정한다. 로그도 `Decimal.ln()` 이다.
골든 스냅샷이 환경마다 흔들리면 없느니만 못하기 때문이다(`AGENTS.md` 5장).
원시값을 **먼저 bp 정수로 양자화한 뒤** 그 위에서 z 를 계산한다 — 그래야 게시된
`*_raw_bp` 로 앱이 z 를 다시 계산해도 같은 값이 나온다(축 분해 표가 재현 가능하다).

## 🔴 같은 규율이 z → 점수 단계에도 걸린다 (2026-09-17)

한동안 **점수만 양자화 *전* z(Decimal)로** 계산했다. 게시되는 것은 양자화된 z 인데
점수는 그보다 정밀한 값에서 나왔으니, 게시본만 가진 앱은 같은 점수를 낼 수 없었다 —
5985행 중 670~827행이 ±1bp 어긋났고 285영업일 중 1일에서 순위가 뒤집혔다.

지금은 `weighted_score_bp` 가 **게시되는 `*_z_bp` 를 받아** 점수를 낸다. 배치와 앱이
그 함수 하나를 쓰고, `gate._check_score_reproducible` 이 게시 때마다 재현을 검사한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from typing import Any, Mapping, Sequence

from sector.sector_master import SectorMaster

__all__ = [
    "AXES",
    "CLIP_Z",
    "LIQUIDITY_MIN_WON",
    "LOOKBACK_LONG",
    "LOOKBACK_SHORT",
    "PRESETS",
    "VALUE_WINDOW",
    "ScoreRow",
    "ScoringError",
    "rank_scores",
    "score",
    "score_history",
    "weighted_score_bp",
]


class ScoringError(Exception):
    """입력이 스코어링의 전제를 깨뜨렸다. 🔒 조용히 넘어가지 않는다."""


#: 축 기호와 순서. 🔒 **한 글자**인 것이 계약이다 — `axes_missing` 같은 문자열 열이
#: `"BV"` 처럼 이어 붙이고 `"B" in value` 로 읽힌다.
AXES: tuple[str, ...] = ("M", "F", "B", "V")

#: 축 이름 — 화면과 오류 메시지가 쓴다.
AXIS_NAMES: Mapping[str, str] = {
    "M": "모멘텀", "F": "자금흐름", "B": "폭", "V": "밸류",
}

#: 🔒 HF 에 저장하는 것은 이 셋의 결과뿐이다 (계획서 D-4).
#:    사이드바 슬라이더는 앱이 `*_z_bp` 로 그 자리에서 가중합만 다시 한다.
PRESETS: Mapping[str, Mapping[str, int]] = {
    "balanced":   {"M": 35, "F": 30, "B": 20, "V": 15},
    "momentum":   {"M": 55, "F": 25, "B": 15, "V": 5},
    "contrarian": {"M": 15, "F": 20, "B": 20, "V": 45},
}

LOOKBACK_SHORT = 20      # r20 · F 축 · 유동성 창
LOOKBACK_LONG = 60       # r60
VALUE_WINDOW = 120       # SMA120 (밸류 축)

#: 모멘텀은 단기·장기를 섞는다 (계획서 D-1). 🔒 **둘 다 있어야 M 축이 있다** —
#: 한쪽만으로 채우면 다른 척도가 되고, 그 사실이 점수에 드러나지 않는다.
MOMENTUM_MIX: Mapping[int, Decimal] = {
    LOOKBACK_SHORT: Decimal("0.6"),
    LOOKBACK_LONG: Decimal("0.4"),
}

#: 유동성 게이트 — 20영업일 평균 거래대금이 이 아래면 경고 배지 (계획서 D-1).
#: 🔒 점수에 넣지 않는다. 고회전은 오히려 미래 수익률이 낮다(V15).
LIQUIDITY_MIN_WON = 100_000_000

CLIP_Z = Decimal(3)                  # 한 축이 총점을 지배하는 것을 막는다
MAD_TO_SIGMA = Decimal("1.4826")     # 정규분포에서 MAD → σ
MEANAD_TO_SIGMA = Decimal("1.2533")  # 정규분포에서 meanAD → σ (= √(π/2))

_BP = Decimal(10_000)
_ONE = Decimal(1)
_ZERO = Decimal(0)

#: 🔒 컨텍스트를 고정한다. 전역 `getcontext()` 가 바뀐 채로 호출돼도 같은 답을 낸다 —
#:    골든 스냅샷이 호출 순서에 의존하면 그건 골든이 아니다.
_CONTEXT = Context(prec=34, rounding=ROUND_HALF_EVEN)


def _to_bp(value: Decimal | None) -> int | None:
    """bp 정수로 양자화. `None` 은 그대로."""
    if value is None:
        return None
    return int(value.quantize(_ONE, rounding=ROUND_HALF_EVEN))


def _ratio_bp(value: Decimal | None) -> int | None:
    """비율(0.03 = 3%)을 bp 정수로."""
    return None if value is None else _to_bp(value * _BP)


# ── 출력 행 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ScoreRow:
    """1행 = 1섹터 × 1영업일. **화면이 읽는 유일한 것이다.**

    `None` 은 "그날 그 축을 계산할 수 없었다"는 뜻이다 — 0 이 아니다.
    """

    bas_dd: str
    sector_id: str
    gics: str

    # ── 원시값 (bp) ── 축 분해 표가 "무엇을 보고 이렇게 됐나"를 보여줄 재료
    m_raw_bp: int | None      # 0.6·(r20_s−r20_mkt) + 0.4·(r60_s−r60_mkt), 로그수익률
    f_raw_bp: int | None      # 상장좌수 20영업일 변화율
    b_raw_bp: int | None      # 섹터 폭 − 시장 폭 (이미 bp 라 그대로)
    v_raw_bp: int | None      # −(지수/SMA120 − 1) · 이격도의 음수(과열 감점)

    # ── 표준화 (z × 10000) ── 🔒 앱의 슬라이더는 이것만 가중합한다
    m_z_bp: int | None
    f_z_bp: int | None
    b_z_bp: int | None
    v_z_bp: int | None

    n_axes_used: int          # 실제로 점수에 들어간 축 수 (0~4)
    axes_missing: str         # 계산 불가였던 축 — 예 `"V"` · `"BV"`
    axes_degraded: str        # MAD 가 0 이라 meanAD 로 표준화한 축

    # ── 프리셋 3종 (계획서 D-4) ──
    score_balanced_bp: int | None
    rank_balanced: int | None
    score_momentum_bp: int | None
    rank_momentum: int | None
    score_contrarian_bp: int | None
    rank_contrarian: int | None

    liquidity_ok: bool | None   # 20영업일 평균 거래대금 ≥ 1억. `None` = 판정 불가
    etf_n: int
    is_partial: bool            # 집계가 단 표시를 그대로 이어받는다

    # 🔴 정의 룩어헤드 차단 (계획서 D-2 ②) — 지금 정의로 과거를 채점한 것도 룩어헤드다
    config_version: str
    config_sha256: str
    fetched_at: str             # 🔒 빌드 시각. 멱등성 판정에서 빠진다(gate.BUILD_STAMP_COLUMN)


# ── 조회 판 ─────────────────────────────────────────────────────────────────

class _Panel:
    """날짜 × 섹터 조회 판. 프레임을 **한 번만** 훑어 파이썬 값으로 바꾼다.

    🔒 `as_of` 로 이미 잘려 있다. 그래서 아래 어떤 함수도 "미래를 보지 않는다"를
       따로 지킬 필요가 없다 — 미래가 **판에 없다.**
    """

    __slots__ = ("days", "sector_ids", "_sector", "_market", "_obs", "_mkt_obs", "_idx_sum")

    def __init__(self, sector_frame: Any, market_frame: Any, *, as_of: str) -> None:
        sector = _rows(sector_frame)
        market = _rows(market_frame)
        sector = [r for r in sector if str(r["bas_dd"]) <= as_of]
        market = [r for r in market if str(r["bas_dd"]) <= as_of]
        if not sector or not market:
            raise ScoringError(f"as_of={as_of} 이하의 행이 없다 — 집계 범위를 확인한다")

        sector_days = sorted({str(r["bas_dd"]) for r in sector})
        market_days = sorted({str(r["bas_dd"]) for r in market})
        if sector_days != market_days:
            # 🔴 날짜 축이 갈라지면 M 축이 **다른 기간끼리** 빼게 된다. 조용히
            #    교집합을 쓰지 않는다 — 그건 틀렸다고 말해 주지 않는 종류의 오류다.
            only_s = sorted(set(sector_days) - set(market_days))[:3]
            only_m = sorted(set(market_days) - set(sector_days))[:3]
            raise ScoringError(
                f"sector_daily 와 market_daily 의 날짜 축이 다르다 — "
                f"sector 에만 {len(set(sector_days) - set(market_days))}일{only_s} · "
                f"market 에만 {len(set(market_days) - set(sector_days))}일{only_m}"
            )

        self.days: list[str] = sector_days
        self.sector_ids: list[str] = sorted({str(r["sector_id"]) for r in sector})
        index = {day: i for i, day in enumerate(self.days)}

        self._sector: dict[tuple[int, str], Mapping[str, Any]] = {}
        for row in sector:
            key = (index[str(row["bas_dd"])], str(row["sector_id"]))
            if key in self._sector:
                raise ScoringError(
                    f"(bas_dd, sector_id) 가 중복이다: {self.days[key[0]]} · {key[1]}"
                )
            self._sector[key] = row
        self._market: list[Mapping[str, Any]] = [None] * len(self.days)  # type: ignore[list-item]
        for row in market:
            self._market[index[str(row["bas_dd"])]] = row

        # 관측 누적합 — "창 [t−k, t] 가 전부 관측일인가"를 뺄셈 한 번으로 답한다.
        # 🔒 지수가 전진한 날 = 그날 수익률이 있는 날이다(`aggregate._Series.advance`).
        self._obs: dict[str, list[int]] = {}
        self._idx_sum: dict[str, list[int]] = {}
        for sid in self.sector_ids:
            obs = [0] * (len(self.days) + 1)
            idx_sum = [0] * (len(self.days) + 1)
            for t in range(len(self.days)):
                row = self._sector.get((t, sid))
                seen = row is not None and row.get("etf_ret_1d_bp") is not None
                level = (row or {}).get("etf_idx_bp")
                obs[t + 1] = obs[t] + int(seen)
                idx_sum[t + 1] = idx_sum[t] + (int(level) if level is not None else 0)
            self._obs[sid] = obs
            self._idx_sum[sid] = idx_sum

        mkt_obs = [0] * (len(self.days) + 1)
        for t, row in enumerate(self._market):
            mkt_obs[t + 1] = mkt_obs[t] + int(
                row is not None and row.get("eqw_ret_1d_bp") is not None
            )
        self._mkt_obs = mkt_obs

    # ── 값 조회 ──
    def sector(self, t: int, sid: str, column: str) -> Any:
        if t < 0:
            return None
        row = self._sector.get((t, sid))
        return None if row is None else row.get(column)

    def market(self, t: int, column: str) -> Any:
        if t < 0:
            return None
        row = self._market[t]
        return None if row is None else row.get(column)

    # ── 창이 온전한가 ──
    def sector_window_ok(self, t: int, sid: str, span: int) -> bool:
        """`[t−span+1 .. t]` 의 **모든** 영업일에 섹터 관측이 있었는가."""
        start = t - span + 1
        if start < 0:
            return False
        return self._obs[sid][t + 1] - self._obs[sid][start] == span

    def market_window_ok(self, t: int, span: int) -> bool:
        start = t - span + 1
        if start < 0:
            return False
        return self._mkt_obs[t + 1] - self._mkt_obs[start] == span

    def sector_idx_mean(self, t: int, sid: str, span: int) -> Decimal | None:
        """`[t−span+1 .. t]` 의 지수 평균(SMA). 창이 온전할 때만 값을 준다."""
        start = t - span + 1
        if start < 0:
            return None
        total = self._idx_sum[sid][t + 1] - self._idx_sum[sid][start]
        return Decimal(total) / Decimal(span)


def _rows(frame: Any) -> list[Mapping[str, Any]]:
    """DataFrame → `None` 이 제대로 들어간 dict 목록.

    🔒 `pd.NA` 를 그대로 두면 `is None` 이 거짓이 되고, 결측이 조용히 값처럼 흐른다.
    """
    import pandas as pd

    out: list[Mapping[str, Any]] = []
    for record in frame.to_dict(orient="records"):
        clean: dict[str, Any] = {}
        for key, value in record.items():
            # `source_ids` 는 배열로 복원되므로 `pd.isna` 를 그대로 물으면 원소마다
            # 답이 나와 진릿값이 모호해진다. 스칼라일 때만 결측을 묻는다.
            missing = value is None or (pd.api.types.is_scalar(value) and pd.isna(value))
            clean[key] = None if missing else value
        out.append(clean)
    return out


# ── 축 ──────────────────────────────────────────────────────────────────────

def _log_return(now: Any, past: Any) -> Decimal | None:
    """`ln(now/past)`. 둘 중 하나라도 없거나 0 이하면 `None` — 0 을 돌려주지 않는다."""
    if now is None or past is None:
        return None
    now_d, past_d = Decimal(int(now)), Decimal(int(past))
    if now_d <= 0 or past_d <= 0:
        return None
    return (now_d / past_d).ln()


def _axis_momentum(panel: _Panel, t: int, sid: str) -> Decimal | None:
    """M — `0.6·(r20_s − r20_mkt) + 0.4·(r60_s − r60_mkt)`, 로그수익률.

    🔴 **섹터와 시장 양쪽의 창이 온전해야 한다.** 한쪽만 온전하면 두 계열이 다른
       기간을 보게 되고, 그 뺄셈은 "시장 대비"가 아니다.
    """
    total = _ZERO
    for span, weight in MOMENTUM_MIX.items():
        if not panel.sector_window_ok(t, sid, span + 1):
            return None
        if not panel.market_window_ok(t, span + 1):
            return None
        sector_r = _log_return(
            panel.sector(t, sid, "etf_idx_bp"), panel.sector(t - span, sid, "etf_idx_bp")
        )
        market_r = _log_return(
            panel.market(t, "eqw_idx_bp"), panel.market(t - span, "eqw_idx_bp")
        )
        if sector_r is None or market_r is None:
            return None
        total += weight * (sector_r - market_r)
    return total


def _axis_flow(panel: _Panel, t: int, sid: str) -> Decimal | None:
    """F — ETF 상장좌수의 20영업일 변화율. **이 도구의 존재 이유다.**

    좌수 증가 = LP 가 설정을 늘렸다 = 실제 순유입. 가격과 독립인 정보다.

    🔴 두 시점의 `etf_n` 이 다르면 계산하지 않는다. ETF 가 새로 편입되면 좌수 합이
       구성 변화만으로 점프하고, 그것을 "돈이 들어왔다"고 읽으면 거짓이다
       (계획서 D-2 ④). 좌수는 **수준값**이라 중간에 쉰 날이 있어도 무방하다 —
       그래서 이 축만 관측 창을 보지 않는다.
    """
    past = t - LOOKBACK_SHORT
    if past < 0:
        return None
    now_shares = panel.sector(t, sid, "etf_shares_sum")
    past_shares = panel.sector(past, sid, "etf_shares_sum")
    if now_shares is None or past_shares is None or int(past_shares) <= 0:
        return None
    now_n = panel.sector(t, sid, "etf_n")
    for i in range(past, t + 1):
        etf_n = panel.sector(i, sid, "etf_n")
        if etf_n is None or int(etf_n) != int(now_n):
            return None
    if int(now_n) == 0:
        # ETF 가 없는 섹터는 **0 으로 채우지 않는다.** 가중치를 재정규화한다.
        return None
    return Decimal(int(now_shares)) / Decimal(int(past_shares)) - _ONE


def _axis_breadth(panel: _Panel, t: int, sid: str) -> Decimal | None:
    """B — 섹터의 폭에서 시장 전체의 폭을 뺀다. 입력이 이미 bp 라 그대로 쓴다."""
    sector_b = panel.sector(t, sid, "breadth_up_bp")
    market_b = panel.market(t, "breadth_up_bp")
    if sector_b is None or market_b is None:
        return None
    return Decimal(int(sector_b) - int(market_b))


def _axis_value(panel: _Panel, t: int, sid: str) -> Decimal | None:
    """V — `−(지수/SMA120 − 1)`. 이격도의 음수이므로 **많이 오른 섹터가 감점된다.**

    M 축을 견제하는 축이다. M 만 있으면 꼭지에서 사는 도구가 된다.
    """
    if not panel.sector_window_ok(t, sid, VALUE_WINDOW):
        return None
    level = panel.sector(t, sid, "etf_idx_bp")
    sma = panel.sector_idx_mean(t, sid, VALUE_WINDOW)
    if level is None or sma is None or sma <= 0:
        return None
    return -(Decimal(int(level)) / sma - _ONE)


def _liquidity_ok(panel: _Panel, t: int, sid: str) -> bool | None:
    """20영업일 평균 거래대금이 1억 이상인가. 창이 안 차면 `None` — `False` 가 아니다.

    🔒 "아직 모른다"와 "미달이다"는 다른 말이다. 화면이 둘을 같게 그리면 신규 상장
       ETF 가 이유 없이 경고를 받는다.
    """
    start = t - LOOKBACK_SHORT + 1
    if start < 0:
        return None
    total = 0
    for i in range(start, t + 1):
        value = panel.sector(i, sid, "etf_value_sum")
        if value is None:
            return None
        total += int(value)
    return total >= LIQUIDITY_MIN_WON * LOOKBACK_SHORT


_AXIS_FN = {
    "M": _axis_momentum,
    "F": _axis_flow,
    "B": _axis_breadth,
    "V": _axis_value,
}


# ── 횡단면 표준화 ────────────────────────────────────────────────────────────

def _median(values: Sequence[Decimal]) -> Decimal:
    """중앙값. 짝수개면 가운데 둘의 평균 — 🔒 표준 정의를 임의로 바꾸지 않는다."""
    ordered = sorted(values)
    n = len(ordered)
    middle = n // 2
    if n % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _standardize(raw: Mapping[str, int | None]) -> tuple[dict[str, Decimal | None], str]:
    """같은 날 섹터들 사이의 표준화. 반환 `(z, scale_kind)`.

    🔒 **횡단면에서만** 한다. 시계열 z 는 그 자체가 룩어헤드다(계획서 D-2 ③).
    `scale_kind` 는 `"mad"` · `"meanad"` · `"none"` 중 하나다 — 머리주석 참조.
    """
    present = {sid: Decimal(v) for sid, v in raw.items() if v is not None}
    if len(present) < 2:
        # 한 섹터뿐이면 "다른 섹터 대비"라는 말 자체가 성립하지 않는다.
        return {sid: None for sid in raw}, "none"

    values = list(present.values())
    center = _median(values)
    mad = _median([abs(v - center) for v in values])
    if mad > 0:
        scale, kind = MAD_TO_SIGMA * mad, "mad"
    else:
        mean_ad = sum((abs(v - center) for v in values), _ZERO) / Decimal(len(values))
        if mean_ad > 0:
            scale, kind = MEANAD_TO_SIGMA * mean_ad, "meanad"
        else:
            # 모든 섹터가 정확히 같은 값이다. 변별할 것이 정말로 없다.
            return {sid: None for sid in raw}, "none"

    out: dict[str, Decimal | None] = {}
    for sid in raw:
        if sid not in present:
            out[sid] = None
            continue
        z = (present[sid] - center) / scale
        out[sid] = max(-CLIP_Z, min(CLIP_Z, z))
    return out, kind


def weighted_score_bp(
    z_bp: Mapping[str, int | None], weights: Mapping[str, int]
) -> int | None:
    """`Σ w·z / Σ w` 를 **게시되는 z(bp 정수)** 위에서. **결측 축은 분자·분모 양쪽에서 빠진다**
    (계획서 D-3) — 가중치를 재정규화하므로 3축 섹터와 4축 섹터가 같은 척도로 비교된다.

    ## 🔴 왜 양자화된 z 를 받는가 — 게시값이 재현되어야 한다

    바로 위 `_score_at` 이 `raw` 를 먼저 bp 로 양자화하는 이유가 *"게시된 raw 로 앱이
    z 를 다시 계산해도 같은 값이 나와야 한다"* 다. **z → 점수 단계만 그 원칙을 따르지
    않았다** — 점수를 양자화 *전* z(Decimal)로 계산해 게시하면서 z 는 양자화해 게시했다.
    그래서 게시된 `*_z_bp` 로 가중합을 다시 하면 5985행 중 670~827행이 ±1bp 어긋났고,
    285영업일 중 1일에서 순위가 실제로 뒤집혔다(2026-09-17 실측).

    앱의 가중치 슬라이더가 그 어긋남을 화면으로 끌어낸다 — 슬라이더를 프리셋 값에
    맞췄는데 프리셋과 다른 숫자가 나온다. 그래서 축당 0.0001σ 를 버리고 **재현성을**
    택했다. `gate._check_score_reproducible` 이 게시 때마다 이것을 검사한다.

    🔒 **가중치를 미리 합 100 으로 정규화하지 마라.** 정수 나눗셈이 끼면 스칼라배
       불변(35/30/20/15 == 70/60/40/30)이 깨진다. 여기서 한 번에 나눈다.
    🔒 컨텍스트를 스스로 고정한다 — 배치는 `score()` 안에서, 앱은 그 밖에서 부른다.
       전역 `getcontext()` 에 기대면 두 호출처가 다른 답을 낼 수 있다.
    """
    with localcontext(_CONTEXT):
        numerator, denominator = _ZERO, 0
        for axis, weight in weights.items():
            value = z_bp.get(axis)
            if value is None or weight <= 0:
                continue
            numerator += Decimal(weight) * Decimal(int(value))
            denominator += weight
        if denominator == 0:
            return None
        return _to_bp(numerator / Decimal(denominator))


def rank_scores(scores: Mapping[str, int | None]) -> dict[str, int | None]:
    """내림차순 순위. 🔒 동점은 `(−score_bp, sector_id)` 로 갈라 tie-break 를 명시한다.

    점수가 없는 섹터는 순위도 없다 — 맨 뒤에 두면 "꼴찌"로 읽힌다.

    🔒 배치와 앱이 **이 함수 하나**를 쓴다. 화면이 따로 정렬하면 tie-break 가 갈려
       같은 점수에서 다른 순위가 나온다.
    """
    ordered = sorted(
        (sid for sid, value in scores.items() if value is not None),
        key=lambda sid: (-int(scores[sid]), sid),  # type: ignore[arg-type]
    )
    out: dict[str, int | None] = {sid: None for sid in scores}
    for position, sid in enumerate(ordered, start=1):
        out[sid] = position
    return out


# ── 하루치 채점 ─────────────────────────────────────────────────────────────

def _score_at(panel: _Panel, t: int, config: SectorMaster, *, fetched_at: str) -> list[ScoreRow]:
    """`panel.days[t]` 하루의 21섹터 점수.

    🔒 `t` 보다 뒤의 인덱스를 **한 번도** 읽지 않는다. `test_asof_monotone` 이
       요구하는 성질이 여기서 나온다 — 규율이 아니라 자료 접근 방향에서.
    """
    bas_dd = panel.days[t]
    gics_of = {s.id: s.gics for s in config.sectors}

    raw_bp: dict[str, dict[str, int | None]] = {}
    for axis in AXES:
        fn = _AXIS_FN[axis]
        column: dict[str, int | None] = {}
        for sid in panel.sector_ids:
            value = fn(panel, t, sid)
            # 🔒 bp 로 **먼저** 양자화하고 그 위에서 z 를 만든다 — 게시된 raw 로
            #    앱이 z 를 다시 계산해도 같은 값이 나와야 한다(머리주석).
            column[sid] = _to_bp(value) if axis == "B" else _ratio_bp(value)
        raw_bp[axis] = column

    z_by_axis: dict[str, dict[str, Decimal | None]] = {}
    degraded: list[str] = []
    for axis in AXES:
        z_by_axis[axis], kind = _standardize(raw_bp[axis])
        if kind == "meanad":
            degraded.append(axis)

    # 🔒 **게시되는 z(bp 정수) 위에서** 가중합한다 — 양자화 전 Decimal 로 계산하면
    #    게시본만 가진 앱이 같은 점수를 낼 수 없다(`weighted_score_bp` 머리주석).
    z_bp_of = {
        sid: {a: _to_bp(z_by_axis[a][sid] * _BP) if z_by_axis[a][sid] is not None else None
              for a in AXES}
        for sid in panel.sector_ids
    }
    preset_scores: dict[str, dict[str, int | None]] = {
        name: {sid: weighted_score_bp(z_bp_of[sid], weights) for sid in panel.sector_ids}
        for name, weights in PRESETS.items()
    }
    preset_ranks = {name: rank_scores(values) for name, values in preset_scores.items()}

    rows: list[ScoreRow] = []
    for sid in panel.sector_ids:
        used = [a for a in AXES if z_by_axis[a][sid] is not None]
        missing = "".join(a for a in AXES if a not in used)
        etf_n = panel.sector(t, sid, "etf_n")
        partial = panel.sector(t, sid, "is_partial")
        rows.append(ScoreRow(
            bas_dd=bas_dd,
            sector_id=sid,
            gics=gics_of.get(sid, ""),
            m_raw_bp=raw_bp["M"][sid],
            f_raw_bp=raw_bp["F"][sid],
            b_raw_bp=raw_bp["B"][sid],
            v_raw_bp=raw_bp["V"][sid],
            # 🔒 점수를 낸 것과 **같은** z 를 싣는다. 따로 양자화하면 언젠가 갈라진다
            m_z_bp=z_bp_of[sid]["M"],
            f_z_bp=z_bp_of[sid]["F"],
            b_z_bp=z_bp_of[sid]["B"],
            v_z_bp=z_bp_of[sid]["V"],
            n_axes_used=len(used),
            axes_missing=missing,
            axes_degraded="".join(a for a in AXES if a in degraded),
            score_balanced_bp=preset_scores["balanced"][sid],
            rank_balanced=preset_ranks["balanced"][sid],
            score_momentum_bp=preset_scores["momentum"][sid],
            rank_momentum=preset_ranks["momentum"][sid],
            score_contrarian_bp=preset_scores["contrarian"][sid],
            rank_contrarian=preset_ranks["contrarian"][sid],
            liquidity_ok=_liquidity_ok(panel, t, sid),
            etf_n=int(etf_n) if etf_n is not None else 0,
            is_partial=bool(partial) if partial is not None else True,
            config_version=config.version,
            config_sha256=config.config_sha256,
            fetched_at=fetched_at,
        ))
    return rows


# ── 공개 API ────────────────────────────────────────────────────────────────

def score(
    sector_frame: Any,
    market_frame: Any,
    *,
    as_of: str,
    config: SectorMaster,
    fetched_at: str,
) -> tuple[ScoreRow, ...]:
    """`as_of` **하루치** 점수.

    🔒 `as_of` 에 기본값이 없다. 빠뜨린 호출이 조용히 전체 기간을 보는 일을 막는다
       (계획서 D-2 ①).
    """
    with localcontext(_CONTEXT):
        panel = _Panel(sector_frame, market_frame, as_of=as_of)
        return tuple(_score_at(panel, len(panel.days) - 1, config, fetched_at=fetched_at))


def score_history(
    sector_frame: Any,
    market_frame: Any,
    *,
    as_of: str,
    config: SectorMaster,
    fetched_at: str,
    days: int | None = None,
) -> tuple[ScoreRow, ...]:
    """`as_of` 이하 각 영업일의 점수. `days` 를 주면 **마지막 N 영업일만** 낸다.

    각 날짜는 **그 날짜까지의 자료로만** 채점된다 — 판을 한 번만 만들되 `t` 보다
    뒤를 읽지 않기 때문이다. 그래서 과거 행을 오늘 다시 계산해도 값이 같다.
    """
    with localcontext(_CONTEXT):
        panel = _Panel(sector_frame, market_frame, as_of=as_of)
        first = 0 if days is None else max(0, len(panel.days) - days)
        rows: list[ScoreRow] = []
        for t in range(first, len(panel.days)):
            rows += _score_at(panel, t, config, fetched_at=fetched_at)
        return tuple(rows)
