"""업로드 게이트 — **무엇이 이 기계를 떠나도 되는가**를 정하는 한 곳.

이 파일이 답하는 질문은 둘이다.

1. **어떤 열이 나가는가** (`SECTOR_PUBLISHED_COLUMNS` · `MARKET_PUBLISHED_COLUMNS`)
2. **나가도 되는 모양인가** (`check`)

## 🔴 왜 허용목록을 손으로 적는가

`data/derived/` 의 열을 그대로 올리면, 나중에 누가 집계에 열 하나를 더할 때
그 열이 **아무도 결정하지 않은 채** HF 로 나간다. 약관 제11조②("제공받은 정보를
제3자에게 제공할 수 없다")를 어기는 순간은 그렇게 온다 — 악의가 아니라 관성으로.

그래서 목록을 **손으로** 적고, `gate_test.py` 가 이 목록을 dataclass 필드와
대조한다. 집계에 열이 생기면 **테스트가 먼저 깨진다.** 사람이 "이 열이 나가도
되는가"를 한 번 판단해야 통과한다. 판단을 강제하는 것이 이 목록의 목적이다.

## 🔴 왜 좌수를 지수로 바꿔 내보내는가 (ADR-SC-0006 ②)

ADR 은 "집계값도 **원천 복원이 가능하면 지수화한다**" 고 정했고, 예로 ETF 가
하나뿐인 섹터의 가중 종가를 들었다. 같은 논리가 **합계 열에도** 걸린다 —
`telecom`·`transport` 는 ETF 가 1개라, 그 섹터의 `etf_shares_sum` 은 집계가
아니라 그 ETF 의 `LIST_SHRS` **그 값 자체**다. 순자산총액도 마찬가지다.

- `etf_nav_sum` → **내보내지 않는다.** 자금흐름 축이 쓰지 않기로 이미 정해져
  있고(좌수 × NAV 라 NAV 를 모멘텀 축과 이중 계산한다), 소비자가 없는 열을
  내보낼 이유가 없다.
- `etf_shares_sum` → `etf_shares_idx_bp` (**기준일 = 10000**). 자금흐름 축이
  필요로 하는 것은 `좌수(T)/좌수(T-20)` 라는 **비율**이지 수준이 아니다.
  지수는 그 비율을 그대로 보존하면서 수준을 감춘다. 실측 지수 범위가
  4982~116124bp 라 bp 해상도(최악 0.02%)는 20일 변화율에 충분하다.

- `etf_value_sum` → **내보내지 않는다** (2026-09-11 · M7 에서 회수했다). M6 때는
  유동성 게이트가 "일평균 1억" 을 원 단위로 판정해야 해서 수준 그대로 내보냈고,
  ETF 가 1개인 두 섹터에서 그 값이 원천과 같아지는 것을 **아는 노출**로 남겨
  뒀다. M7 이 판정을 로컬에서 끝내고 `score_daily.liquidity_ok`(bool) 로만
  앱에 넘기게 되면서 그 노출을 없앨 수 있게 됐다 — 예고한 대로 한 줄을 뺐다.

## 🔴 `score_daily` 는 왜 여기를 또 지나는가

점수는 파생의 파생이라 원천 복원 위험이 낮다. 그래도 같은 문을 지나게 하는 이유는
**열이 늘어나는 곳이 늘어나기 때문**이다. M9~M11 이 화면을 만들며 "이 값도 있으면
좋겠다" 로 열을 붙일 텐데, 그때 판단을 강제하는 장치가 여기 말고는 없다.
🔒 그리고 점수 열은 **범위가 알려져 있다** — z 는 clip(±3), 점수는 그 가중평균이라
   ±3σ 를 넘을 수 없다. 넘었다면 표준화가 깨진 것이므로 검사로 잡는다.

## 🔒 위반 메시지에 시세값을 찍지 않는다

게이트가 막았다는 사실을 알리는 데 값이 필요하지 않다. 열 이름·건수·날짜까지다.
터미널과 세션 기록도 `data/raw/` 와 같은 취급을 받아야 한다 (ADR-SC-0006).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

__all__ = [
    "GateReport",
    "REQUIRED_CHECKS",
    "SECTOR_PUBLISHED_COLUMNS",
    "MARKET_PUBLISHED_COLUMNS",
    "SCORE_PUBLISHED_COLUMNS",
    "PROJECTED_COLUMNS",
    "DELIBERATELY_WITHHELD",
    "DAILY_MOVE_LIMIT_BP",
    "SHARES_INDEX_BASE_BP",
    "check",
    "project_sector",
    "project_market",
    "project_score",
]

#: 게시되는 `sector_daily` 의 열 — **순서까지 계약이다** (parquet 바이트가 달라진다).
#: 🔒 여기에 열을 더하는 것은 "이 값을 제3자에게 준다" 는 결정이다. 가볍게 하지 마라.
SECTOR_PUBLISHED_COLUMNS: tuple[str, ...] = (
    "bas_dd", "sector_id", "gics",
    "etf_n",
    "etf_idx_bp",            # 순자산 가중 체인연결 지수 (기준일 10000)
    "etf_ret_1d_bp", "etf_ret_20d_bp",
    "etf_shares_idx_bp",     # ★ 원천의 `etf_shares_sum` 을 지수화한 것 (위 머리주석)
    "etf_premium_bp",
    "member_n",
    "member_idx_bp",
    "member_ret_1d_bp", "member_ret_20d_bp",
    "breadth_up_bp", "breadth_n",
    "source_ids", "is_partial", "fetched_at",
)

#: 게시되는 `market_daily` 의 열. 전부 지수·bp·건수라 절대가격이 없다.
MARKET_PUBLISHED_COLUMNS: tuple[str, ...] = (
    "bas_dd", "stock_n",
    "mkt_idx_bp", "mkt_ret_1d_bp", "mkt_ret_20d_bp",
    "eqw_idx_bp", "eqw_ret_1d_bp",
    "breadth_up_bp", "breadth_n",
    "is_partial", "fetched_at",
)

#: 게시되는 `score_daily` 의 열 (M7). 🔒 순서까지 계약이다.
#: 원시값·z·점수·순위와 **판정 결과**만 있다 — 판정의 재료(거래대금)는 로컬에 남는다.
SCORE_PUBLISHED_COLUMNS: tuple[str, ...] = (
    "bas_dd", "sector_id", "gics",
    "m_raw_bp", "f_raw_bp", "b_raw_bp", "v_raw_bp",
    "m_z_bp", "f_z_bp", "b_z_bp", "v_z_bp",
    "n_axes_used", "axes_missing", "axes_degraded",
    "score_balanced_bp", "rank_balanced",
    "score_momentum_bp", "rank_momentum",
    "score_contrarian_bp", "rank_contrarian",
    "liquidity_ok",          # ★ bool. 원 단위 거래대금을 대신해 나간다
    "etf_n", "is_partial",
    "config_version", "config_sha256",
    "fetched_at",
)

#: 🔴 집계에는 있으나 **일부러 내보내지 않는** 열. 목록에 없는 것과 구별한다 —
#:   "빠뜨렸나 / 뺐나" 를 코드가 답해야 나중에 되돌아와 고민하지 않는다.
DELIBERATELY_WITHHELD: dict[str, str] = {
    "etf_nav_sum": "소비자가 없다. ETF 1개 섹터에서 원천과 같아진다 (ADR-SC-0006 ②)",
    "etf_shares_sum": "`etf_shares_idx_bp` 로 지수화해서 내보낸다 (ADR-SC-0006 ②)",
    "etf_value_sum": (
        "ETF 1개 섹터에서 원천과 같아진다. 소비자였던 유동성 판정을 M7 이 로컬에서 "
        "끝내고 `score_daily.liquidity_ok` 로만 넘기므로 회수했다 (V31)"
    ),
}

#: 집계에 없고 **이 파일이 만들어 내보내는** 열. `gate_test.py` 가 허용목록을
#: dataclass 필드와 대조할 때 이 집합을 빼고 본다 — 빼지 않으면 "집계에 없는 열"
#: 로 잘못 잡힌다.
PROJECTED_COLUMNS: frozenset[str] = frozenset({"etf_shares_idx_bp"})

#: 좌수 지수의 기준일 값. 10000bp = 100.00 — 저장소의 다른 지수와 같은 척도다.
SHARES_INDEX_BASE_BP = 10_000

#: 일간 변동 상한(bp). 🔴 임의의 휴리스틱이 아니라 **KRX 가격제한폭 ±30%** 다.
#: 체인연결 지수는 `FLUC_RT` 의 가중평균이고, 가중평균은 원소의 범위를 벗어나지
#: 못한다. 그러므로 이 값을 넘는다는 것은 시장이 아니라 **파이프라인이 깨졌다**는
#: 뜻이다. (실측 최댓값 2962bp — 경계가 빠듯하지만 구조적 상한이라 안전하다)
DAILY_MOVE_LIMIT_BP = 3_000

#: `fetched_at` 은 데이터가 아니라 **빌드 시각**이다. 멱등성 판정에서 뺀다
#: (→ `batch/publish.py` 의 `content_sha256`).
BUILD_STAMP_COLUMN = "fetched_at"

_BAS_DD_RE = re.compile(r"^\d{8}$")


@dataclass(frozen=True, slots=True)
class GateReport:
    """검사 결과.

    🔴 `checks_run` 이 따로 있는 이유 — 이 저장소는 "0건 수집으로 통과"가
       **가장 나쁜 실패**라는 것을 이미 한 번 배웠다(`pytest.ini` 머리주석).
       검사기가 조용히 아무것도 검사하지 않고 `violations=()` 를 돌려주면
       그것은 통과가 아니다. 호출부는 `checks_run == REQUIRED_CHECKS` 를
       **직접 확인해야 한다.**
    """

    checks_run: frozenset[str]
    violations: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


# ── 투영 — 게이트가 통과시킬 모양으로 만든다 ────────────────────────────────

def _shares_index_bp(values: list[Any]) -> list[int | None]:
    """좌수 시계열을 기준일 10000 지수로 바꾼다. **정수 산술만 쓴다.**

    `round(10000 * v / base)` 를 float 없이 계산한다 —
    `floor((20000·v + base) / (2·base))` 가 양수에서 반올림과 같다.
    규약이 float 를 금지하는 이유는 누적 반올림이 새기 때문이고, 여기서 float 를
    쓰면 그 오차가 그대로 **게시되는 바이트**에 들어간다.

    🔒 기준은 **시계열의 첫 유효값**이다. 나중 날짜가 들어와도 기준이 움직이지
       않아, 지난달 샤드가 그대로 남는다 — 멱등성이 여기서 나온다.
    """
    base: int | None = None
    out: list[int | None] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        current = int(value)
        if base is None:
            if current <= 0:
                # 기준이 0 이하면 지수를 만들 수 없다. 지어내지 않는다 (ADR-SC-0007).
                out.append(None)
                continue
            base = current
        out.append((2 * SHARES_INDEX_BASE_BP * current + base) // (2 * base))
    return out


def project_sector(frame: Any) -> Any:
    """`data/derived/sector_daily` → 게시용 프레임.

    ★ 정렬을 여기서 못박는다. 게시 바이트가 결정적이어야 멱등성이 성립하고,
      "원본 프레임이 어떤 순서로 들어왔는가"에 바이트가 흔들리면 안 된다.
    """
    import pandas as pd

    out = frame.sort_values(["sector_id", "bas_dd"], kind="stable").copy()
    parts = []
    for _, group in out.groupby("sector_id", sort=True):
        indexed = group.copy()
        raw = [None if pd.isna(v) else v for v in group["etf_shares_sum"]]
        indexed["etf_shares_idx_bp"] = pd.array(_shares_index_bp(raw), dtype="Int64")
        parts.append(indexed)
    out = pd.concat(parts) if parts else out.assign(etf_shares_idx_bp=pd.array([], dtype="Int64"))
    out = out.sort_values(["bas_dd", "sector_id"], kind="stable").reset_index(drop=True)
    return out[list(SECTOR_PUBLISHED_COLUMNS)]


def project_market(frame: Any) -> Any:
    """`data/derived/market_daily` → 게시용 프레임. 열을 고르고 정렬만 한다."""
    out = frame.sort_values(["bas_dd"], kind="stable").reset_index(drop=True)
    return out[list(MARKET_PUBLISHED_COLUMNS)]


def project_score(frame: Any) -> Any:
    """`data/derived/score_daily` → 게시용 프레임. 열을 고르고 정렬만 한다.

    ★ 투영할 것이 없다 — 점수는 이미 파생의 파생이라 지수화할 수준값이 없다.
      그래도 같은 문을 지나게 한다(머리주석).
    """
    out = frame.sort_values(["bas_dd", "sector_id"], kind="stable").reset_index(drop=True)
    return out[list(SCORE_PUBLISHED_COLUMNS)]


# ── 검사 ────────────────────────────────────────────────────────────────────

def _check_rows_present(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """🔴 빈 프레임은 통과가 아니다. 0건을 올리면 앱이 조용히 빈 화면이 된다."""
    bad = []
    for name, frame, how in (
        ("sector_daily", sector, "python -m batch.build_sector_daily"),
        ("market_daily", market, "python -m batch.build_sector_daily"),
        ("score_daily", score, "python -m batch.build_scores"),
    ):
        if len(frame) == 0:
            bad.append(f"{name} 이 0행이다 — 올릴 것이 없다. `{how}` 를 먼저 돌린다")
    return bad


def _check_columns_allowlist(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """허용목록과 **정확히 같아야** 한다. 모자란 것도 위반이다.

    부분집합만 보면 열이 잘려 나간 프레임이 통과한다 — 앱은 그 결측을 "없는
    값"으로 읽고 화면에 `—` 를 그린다. 그건 사고가 아니라 조용한 손실이다.
    """
    bad = []
    for name, frame, want in (
        ("sector_daily", sector, SECTOR_PUBLISHED_COLUMNS),
        ("market_daily", market, MARKET_PUBLISHED_COLUMNS),
        ("score_daily", score, SCORE_PUBLISHED_COLUMNS),
    ):
        got = tuple(frame.columns)
        if got == tuple(want):
            continue
        extra = sorted(set(got) - set(want))
        missing = sorted(set(want) - set(got))
        if extra:
            withheld = [c for c in extra if c in DELIBERATELY_WITHHELD]
            note = ""
            if withheld:
                note = " · 일부러 뺀 열이다: " + ", ".join(
                    f"{c}({DELIBERATELY_WITHHELD[c]})" for c in withheld
                )
            bad.append(f"{name} 에 허용되지 않은 열 {extra} 가 있다{note}")
        if missing:
            bad.append(f"{name} 에 있어야 할 열 {missing} 이 없다")
        if not extra and not missing:
            bad.append(f"{name} 의 열 순서가 계약과 다르다 — 바이트가 달라진다")
    return bad


def _check_no_duplicate_keys(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    bad = []
    n = int(sector.duplicated(["bas_dd", "sector_id"]).sum())
    if n:
        bad.append(f"sector_daily 에 (bas_dd, sector_id) 중복 {n}행")
    n = int(market.duplicated(["bas_dd"]).sum())
    if n:
        bad.append(f"market_daily 에 bas_dd 중복 {n}행")
    n = int(score.duplicated(["bas_dd", "sector_id"]).sum())
    if n:
        bad.append(f"score_daily 에 (bas_dd, sector_id) 중복 {n}행")
    return bad


def _check_business_day(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """형식·주말·룩어헤드를 본다.

    공휴일은 여기서 보지 않는다 — 달력을 코드에 박지 않는 것이 이 저장소의
    규칙이고(대체공휴일이 매년 바뀐다), 휴일 판정은 집계가 **데이터로** 이미
    했다(`build_sector_daily._traded`). 주말만은 달력이 틀릴 수 없어 본다.
    """
    bad = []
    days = sorted(set(sector["bas_dd"]) | set(market["bas_dd"]) | set(score["bas_dd"]))
    malformed = [d for d in days if not _BAS_DD_RE.match(str(d))]
    if malformed:
        bad.append(f"bas_dd 형식이 YYYYMMDD 가 아닌 날 {len(malformed)}건: {malformed[:3]}")
    weekend, unparsable = [], []
    for day in days:
        if not _BAS_DD_RE.match(str(day)):
            continue
        try:
            parsed = datetime.strptime(str(day), "%Y%m%d")
        except ValueError:
            unparsable.append(day)
            continue
        if parsed.weekday() >= 5:
            weekend.append(day)
    if unparsable:
        bad.append(f"달력에 없는 날짜 {len(unparsable)}건: {unparsable[:3]}")
    if weekend:
        bad.append(f"주말이 영업일로 들어 있다 {len(weekend)}건: {weekend[:3]}")
    ahead = [d for d in days if str(d) > as_of]
    if ahead:
        # 🔴 룩어헤드. `--date` 는 "이 날짜까지의 세상"을 게시한다는 뜻이다.
        bad.append(f"--date({as_of}) 보다 뒤의 날이 {len(ahead)}건: {ahead[:3]}")
    return bad


def _check_positive_index(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """지수는 양수다. 0 이하면 체인연결이 끊긴 것이고, 그 위의 수익률은 전부 거짓이다."""
    bad = []
    for name, frame in (("sector_daily", sector), ("market_daily", market)):
        for column in frame.columns:
            if not column.endswith("_idx_bp"):
                continue
            values = frame[column].dropna()
            n = int((values <= 0).sum())
            if n:
                bad.append(f"{name}.{column} 에 0 이하 {n}행 — 지수가 될 수 없다")
    return bad


def _check_daily_move_limit(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """가격제한폭(±30%)을 넘는 일간 변동. 시장이 아니라 파이프라인이 깨진 것이다."""
    bad = []
    for name, frame in (("sector_daily", sector), ("market_daily", market)):
        for column in frame.columns:
            if not column.endswith("_ret_1d_bp"):
                continue
            values = frame[column].dropna().abs()
            n = int((values > DAILY_MOVE_LIMIT_BP).sum())
            if n:
                bad.append(
                    f"{name}.{column} 이 ±{DAILY_MOVE_LIMIT_BP}bp(가격제한폭)를 "
                    f"넘는 행 {n}건"
                )
    return bad


def _check_date_axis_matches(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """두 렌즈의 날짜 축이 같아야 한다.

    갈라지면 모멘텀 축의 `r20_섹터 − r20_시장` 이 **다른 기간끼리 빼는** 뺄셈이
    된다. 그 결과는 틀렸다고 말해 주지 않는다.
    """
    only_sector = sorted(set(sector["bas_dd"]) - set(market["bas_dd"]))
    only_market = sorted(set(market["bas_dd"]) - set(sector["bas_dd"]))
    bad = []
    if only_sector:
        bad.append(f"sector_daily 에만 있는 영업일 {len(only_sector)}건: {only_sector[:3]}")
    if only_market:
        bad.append(f"market_daily 에만 있는 영업일 {len(only_market)}건: {only_market[:3]}")
    # 🔒 점수는 집계의 **부분집합**이어야 한다 — 집계에 없는 날을 채점할 수는 없다.
    #    반대로 점수가 더 짧은 것은 정상이다(축이 설 때까지 기다린 창).
    orphan = sorted(set(score["bas_dd"]) - set(sector["bas_dd"]))
    if orphan:
        bad.append(f"score_daily 에만 있는 영업일 {len(orphan)}건: {orphan[:3]} — "
                   f"집계에 없는 날을 채점했다")
    return bad


def _check_sector_set_constant(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """날마다 같은 섹터 집합이어야 한다.

    집계는 설정 하나로 모든 날을 훑으므로 섹터 집합은 날짜에 의존하지 않는다.
    달라졌다면 절반만 만들어진 프레임이거나, 두 번의 집계 결과가 섞인 것이다.
    """
    bad = []
    for name, frame in (("sector_daily", sector), ("score_daily", score)):
        sizes = frame.groupby("bas_dd")["sector_id"].nunique()
        if sizes.empty or sizes.nunique() <= 1:
            continue
        expected = int(sizes.mode().iloc[0])
        odd = sorted(str(d) for d, n in sizes.items() if int(n) != expected)
        bad.append(
            f"{name} 은 영업일마다 섹터 수가 다르다 (대부분 {expected}개) — "
            f"어긋난 날 {len(odd)}건: {odd[:3]}"
        )
    return bad


#: 점수와 z 의 구조적 상한. z 는 `clip(±3)` 이고 점수는 그 **가중평균**이라
#: 원소의 범위를 벗어날 수 없다 — `DAILY_MOVE_LIMIT_BP` 와 같은 종류의 상한이다.
#: 넘었다면 시장이 아니라 표준화가 깨진 것이다.
SCORE_LIMIT_BP = 30_000

_Z_COLUMNS = ("m_z_bp", "f_z_bp", "b_z_bp", "v_z_bp")
_SCORE_COLUMNS = ("score_balanced_bp", "score_momentum_bp", "score_contrarian_bp")
_AXIS_LETTERS = "MFBV"


def _check_score_bounds(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """z·점수가 clip 범위 안인가. 🔴 넘으면 표준화가 깨진 것이다."""
    bad = []
    for column in _Z_COLUMNS + _SCORE_COLUMNS:
        if column not in score.columns:
            continue
        n = int((score[column].dropna().abs() > SCORE_LIMIT_BP).sum())
        if n:
            bad.append(f"score_daily.{column} 이 ±{SCORE_LIMIT_BP}bp(clip ±3σ)를 넘는 행 {n}건")
    if "n_axes_used" in score.columns:
        used = score["n_axes_used"].dropna()
        n = int(((used < 0) | (used > len(_AXIS_LETTERS))).sum())
        if n:
            bad.append(f"score_daily.n_axes_used 가 0~{len(_AXIS_LETTERS)} 밖인 행 {n}건")
    return bad


def _check_score_axes_consistent(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """`n_axes_used` · `axes_missing` · 실제 z 결측이 **서로 맞는가.**

    셋이 어긋나면 화면이 "3축으로 계산했다"고 말하면서 4축 가중치로 나눈 점수를
    보여주게 된다. 숫자는 그럴듯하고 설명만 거짓이 되는 종류의 오류다.
    """
    missing_columns = [c for c in (*_Z_COLUMNS, "n_axes_used", "axes_missing")
                       if c not in score.columns]
    if missing_columns:
        return [f"score_daily 에 {missing_columns} 이 없어 축 정합성을 볼 수 없다"]

    z_null = score[list(_Z_COLUMNS)].isna()
    bad = []
    n = int((z_null.sum(axis=1) + score["n_axes_used"] != len(_AXIS_LETTERS)).sum())
    if n:
        bad.append(f"score_daily 에서 n_axes_used 와 z 결측 수의 합이 "
                   f"{len(_AXIS_LETTERS)} 가 아닌 행 {n}건")

    expected = z_null.apply(
        lambda row: "".join(a for a, c in zip(_AXIS_LETTERS, _Z_COLUMNS) if row[c]), axis=1
    )
    n = int((expected != score["axes_missing"].fillna("")).sum())
    if n:
        bad.append(f"score_daily.axes_missing 이 실제 z 결측과 다른 행 {n}건")
    return bad


def _check_score_rank_consistent(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """순위가 점수와 맞고 날마다 1..N 으로 이어지는가.

    🔒 점수가 없으면 순위도 없어야 한다 — 맨 뒤에 매기면 화면이 "꼴찌"로 읽는다.
    """
    bad = []
    for score_column in _SCORE_COLUMNS:
        rank_column = "rank_" + score_column.removeprefix("score_").removesuffix("_bp")
        if score_column not in score.columns or rank_column not in score.columns:
            bad.append(f"score_daily 에 {score_column}/{rank_column} 짝이 없다")
            continue
        pair = score[["bas_dd", "sector_id", score_column, rank_column]]
        n = int((pair[score_column].isna() != pair[rank_column].isna()).sum())
        if n:
            bad.append(f"score_daily 에서 {score_column} 과 {rank_column} 의 "
                       f"결측이 어긋난 행 {n}건")
        graded = pair.dropna(subset=[score_column, rank_column])
        broken_days = []
        for day, group in graded.groupby("bas_dd", sort=True):
            ranks = sorted(int(r) for r in group[rank_column])
            if ranks != list(range(1, len(ranks) + 1)):
                broken_days.append(str(day))
                continue
            ordered = group.sort_values(
                [score_column, "sector_id"], ascending=[False, True], kind="stable"
            )
            if list(ordered[rank_column].astype(int)) != list(range(1, len(ranks) + 1)):
                broken_days.append(str(day))
        if broken_days:
            bad.append(f"score_daily.{rank_column} 이 점수 순서와 어긋난 날 "
                       f"{len(broken_days)}건: {broken_days[:3]}")
    return bad


def _check_score_reproducible(sector: Any, market: Any, score: Any, as_of: str) -> list[str]:
    """게시하는 점수·순위가 **게시하는 z 만으로 재현되는가.**

    ## 🔴 왜 이것이 게시 조건인가

    앱은 게시본(`*_z_bp`)밖에 못 본다. 가중치 슬라이더는 그 열을 가중합해 점수를
    다시 내는데, 게시된 `score_*_bp` 가 그 방식으로 나온 값이 아니면 **같은 화면의
    위(표)와 아래(근거)가 다른 숫자를 말한다.** 2026-09-17 실측에서 최근 20영업일
    창의 첫날이 갈려 한 섹터의 순위 진폭이 표에서 4.4, 근거에서 4.5 로 나왔다.

    🔒 이 검사는 배치의 산수를 되풀이하지 않는다 — `weighted_score_bp` · `rank_scores`
       라는 **배치가 실제로 쓴 함수**에 게시될 열을 도로 먹여 본다. 즉 검사하는 것은
       "계산이 맞나" 가 아니라 **"게시본만으로 이 값에 닿을 수 있나"** 다.
    """
    from sector.scoring import PRESETS, rank_scores, weighted_score_bp

    letters_of = dict(zip(_AXIS_LETTERS, _Z_COLUMNS))
    need = [c for c in (*_Z_COLUMNS, *_SCORE_COLUMNS, "bas_dd", "sector_id")
            if c not in score.columns]
    if need:
        return [f"score_daily 에 {need} 이 없어 재현성을 볼 수 없다"]

    bad = []
    for name, weights in PRESETS.items():
        score_column = f"score_{name}_bp"
        rank_column = f"rank_{name}"
        score_off, rank_off, off_days = 0, 0, []
        for day, group in score.groupby("bas_dd", sort=True):
            again: dict[str, int | None] = {}
            for row in group.itertuples(index=False):
                z_bp = {a: _int_or_none(getattr(row, column))
                        for a, column in letters_of.items()}
                again[str(row.sector_id)] = weighted_score_bp(z_bp, weights)
            ranked = rank_scores(again)
            hit = False
            for row in group.itertuples(index=False):
                sid = str(row.sector_id)
                if again[sid] != _int_or_none(getattr(row, score_column)):
                    score_off += 1
                    hit = True
                if rank_column in score.columns and \
                        ranked[sid] != _int_or_none(getattr(row, rank_column)):
                    rank_off += 1
                    hit = True
            if hit:
                off_days.append(str(day))
        if score_off or rank_off:
            bad.append(
                f"score_daily.{score_column} 을 게시되는 z 로 재현할 수 없다 — "
                f"점수 {score_off}행 · 순위 {rank_off}행 · {len(off_days)}일"
                f"{': ' + str(off_days[:3]) if off_days else ''}")
    return bad


def _int_or_none(value: Any) -> int | None:
    """pandas 의 `<NA>` · `NaN` · `None` 을 하나로 접는다.

    🔒 `bool(value != value)` 같은 요령을 쓰지 않는다 — `pd.NA` 는 그 비교에서
       진리값을 못 내고 예외를 던진다.
    """
    import pandas as pd

    return None if value is None or pd.isna(value) else int(value)


#: 🔴 **이 집합이 전부 돌아야 통과다.** 호출부가 `checks_run` 과 대조한다.
_CHECKS: tuple[tuple[str, Callable[[Any, Any, Any, str], list[str]]], ...] = (
    ("rows_present", _check_rows_present),
    ("columns_allowlist", _check_columns_allowlist),
    ("no_duplicate_keys", _check_no_duplicate_keys),
    ("business_day", _check_business_day),
    ("positive_index", _check_positive_index),
    ("daily_move_limit", _check_daily_move_limit),
    ("date_axis_matches", _check_date_axis_matches),
    ("sector_set_constant", _check_sector_set_constant),
    ("score_bounds", _check_score_bounds),
    ("score_axes_consistent", _check_score_axes_consistent),
    ("score_rank_consistent", _check_score_rank_consistent),
    ("score_reproducible", _check_score_reproducible),
)

REQUIRED_CHECKS: frozenset[str] = frozenset(name for name, _ in _CHECKS)


def check(*, sector: Any, market: Any, score: Any, as_of: str) -> GateReport:
    """게시 직전 검사. 위반 목록과 **실제로 돌린 검사 이름**을 함께 돌려준다.

    🔒 검사 함수가 예외를 던지면 **삼키지 않는다.** 검사기가 깨진 상태는
       "위반 없음"이 아니라 "판정 불가"이고, 판정 불가는 통과가 아니다
       (`validate_config` 가 종료코드 2 로 이미 세운 규칙과 같다).
    """
    violations: list[str] = []
    ran: list[str] = []
    for name, fn in _CHECKS:
        violations += fn(sector, market, score, as_of)
        ran.append(name)
    return GateReport(checks_run=frozenset(ran), violations=tuple(violations))
