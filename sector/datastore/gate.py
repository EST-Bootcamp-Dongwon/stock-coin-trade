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

⚠️ `etf_value_sum`(거래대금 합)은 **수준 그대로 내보낸다.** 유동성 게이트가
   "일평균 1억" 을 원 단위로 판정하기 때문이다. ETF 가 1개인 두 섹터에서는
   이 값도 원천과 같아진다 — 아는 노출이고, 줄이려면 이 목록에서 한 줄을
   빼면 된다(그러면 M7 의 유동성 판정은 로컬 `data/derived/` 로만 계산하고
   `score_daily` 의 `liquidity_ok` 로 앱에 전달해야 한다).

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
    "PROJECTED_COLUMNS",
    "DELIBERATELY_WITHHELD",
    "DAILY_MOVE_LIMIT_BP",
    "SHARES_INDEX_BASE_BP",
    "check",
    "project_sector",
    "project_market",
]

#: 게시되는 `sector_daily` 의 열 — **순서까지 계약이다** (parquet 바이트가 달라진다).
#: 🔒 여기에 열을 더하는 것은 "이 값을 제3자에게 준다" 는 결정이다. 가볍게 하지 마라.
SECTOR_PUBLISHED_COLUMNS: tuple[str, ...] = (
    "bas_dd", "sector_id", "gics",
    "etf_n",
    "etf_idx_bp",            # 순자산 가중 체인연결 지수 (기준일 10000)
    "etf_ret_1d_bp", "etf_ret_20d_bp",
    "etf_shares_idx_bp",     # ★ 원천의 `etf_shares_sum` 을 지수화한 것 (위 머리주석)
    "etf_value_sum",         # ⚠️ 수준 그대로 — 유동성 게이트가 원 단위를 요구한다
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
    "eqw_idx_bp",
    "breadth_up_bp", "breadth_n",
    "is_partial", "fetched_at",
)

#: 🔴 집계에는 있으나 **일부러 내보내지 않는** 열. 목록에 없는 것과 구별한다 —
#:   "빠뜨렸나 / 뺐나" 를 코드가 답해야 나중에 되돌아와 고민하지 않는다.
DELIBERATELY_WITHHELD: dict[str, str] = {
    "etf_nav_sum": "소비자가 없다. ETF 1개 섹터에서 원천과 같아진다 (ADR-SC-0006 ②)",
    "etf_shares_sum": "`etf_shares_idx_bp` 로 지수화해서 내보낸다 (ADR-SC-0006 ②)",
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


# ── 검사 ────────────────────────────────────────────────────────────────────

def _check_rows_present(sector: Any, market: Any, as_of: str) -> list[str]:
    """🔴 빈 프레임은 통과가 아니다. 0건을 올리면 앱이 조용히 빈 화면이 된다."""
    bad = []
    if len(sector) == 0:
        bad.append("sector_daily 가 0행이다 — 올릴 것이 없다. 집계를 먼저 돌린다")
    if len(market) == 0:
        bad.append("market_daily 가 0행이다 — 올릴 것이 없다. 집계를 먼저 돌린다")
    return bad


def _check_columns_allowlist(sector: Any, market: Any, as_of: str) -> list[str]:
    """허용목록과 **정확히 같아야** 한다. 모자란 것도 위반이다.

    부분집합만 보면 열이 잘려 나간 프레임이 통과한다 — 앱은 그 결측을 "없는
    값"으로 읽고 화면에 `—` 를 그린다. 그건 사고가 아니라 조용한 손실이다.
    """
    bad = []
    for name, frame, want in (
        ("sector_daily", sector, SECTOR_PUBLISHED_COLUMNS),
        ("market_daily", market, MARKET_PUBLISHED_COLUMNS),
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


def _check_no_duplicate_keys(sector: Any, market: Any, as_of: str) -> list[str]:
    bad = []
    n = int(sector.duplicated(["bas_dd", "sector_id"]).sum())
    if n:
        bad.append(f"sector_daily 에 (bas_dd, sector_id) 중복 {n}행")
    n = int(market.duplicated(["bas_dd"]).sum())
    if n:
        bad.append(f"market_daily 에 bas_dd 중복 {n}행")
    return bad


def _check_business_day(sector: Any, market: Any, as_of: str) -> list[str]:
    """형식·주말·룩어헤드를 본다.

    공휴일은 여기서 보지 않는다 — 달력을 코드에 박지 않는 것이 이 저장소의
    규칙이고(대체공휴일이 매년 바뀐다), 휴일 판정은 집계가 **데이터로** 이미
    했다(`build_sector_daily._traded`). 주말만은 달력이 틀릴 수 없어 본다.
    """
    bad = []
    days = sorted(set(sector["bas_dd"]) | set(market["bas_dd"]))
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


def _check_positive_index(sector: Any, market: Any, as_of: str) -> list[str]:
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


def _check_daily_move_limit(sector: Any, market: Any, as_of: str) -> list[str]:
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


def _check_date_axis_matches(sector: Any, market: Any, as_of: str) -> list[str]:
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
    return bad


def _check_sector_set_constant(sector: Any, market: Any, as_of: str) -> list[str]:
    """날마다 같은 섹터 집합이어야 한다.

    집계는 설정 하나로 모든 날을 훑으므로 섹터 집합은 날짜에 의존하지 않는다.
    달라졌다면 절반만 만들어진 프레임이거나, 두 번의 집계 결과가 섞인 것이다.
    """
    sizes = sector.groupby("bas_dd")["sector_id"].nunique()
    if sizes.empty or sizes.nunique() <= 1:
        return []
    expected = int(sizes.mode().iloc[0])
    odd = sorted(str(d) for d, n in sizes.items() if int(n) != expected)
    return [
        f"영업일마다 섹터 수가 다르다 (대부분 {expected}개) — 어긋난 날 "
        f"{len(odd)}건: {odd[:3]}"
    ]


#: 🔴 **이 집합이 전부 돌아야 통과다.** 호출부가 `checks_run` 과 대조한다.
_CHECKS: tuple[tuple[str, Callable[[Any, Any, str], list[str]]], ...] = (
    ("rows_present", _check_rows_present),
    ("columns_allowlist", _check_columns_allowlist),
    ("no_duplicate_keys", _check_no_duplicate_keys),
    ("business_day", _check_business_day),
    ("positive_index", _check_positive_index),
    ("daily_move_limit", _check_daily_move_limit),
    ("date_axis_matches", _check_date_axis_matches),
    ("sector_set_constant", _check_sector_set_constant),
)

REQUIRED_CHECKS: frozenset[str] = frozenset(name for name, _ in _CHECKS)


def check(*, sector: Any, market: Any, as_of: str) -> GateReport:
    """게시 직전 검사. 위반 목록과 **실제로 돌린 검사 이름**을 함께 돌려준다.

    🔒 검사 함수가 예외를 던지면 **삼키지 않는다.** 검사기가 깨진 상태는
       "위반 없음"이 아니라 "판정 불가"이고, 판정 불가는 통과가 아니다
       (`validate_config` 가 종료코드 2 로 이미 세운 규칙과 같다).
    """
    violations: list[str] = []
    ran: list[str] = []
    for name, fn in _CHECKS:
        violations += fn(sector, market, as_of)
        ran.append(name)
    return GateReport(checks_run=frozenset(ran), violations=tuple(violations))
