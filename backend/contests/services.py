"""정산 본체 — 스냅샷 · 랭킹 · 회전율 · 최종 확정 (F-05 2·3·4장 · F-04 5·6장).

`scoring.py` 가 숫자만 다룬다면, 여기는 **DB 를 읽어 숫자를 만들고 다시 DB 에 쓴다.**
잡 4·7·9·10 이 부르는 함수가 전부 이 파일에 있다.

    잡 4  snapshot_intraday   장중 10분   → IntradaySnapshot
    잡 7  settle_daily        영업일 15:40 → DailySnapshot · SnapshotHolding ·
                                            ContestRanking · RuleViolation · WeeklyTurnover(잠정)
    잡 9  settle_weekly       월 06:00     → WeeklyTurnover(확정) · 4회 위반 실격
    잡 10 settle_contest      매일 06:00   → 대회 상태 전이 · ContestResult · 계좌 동결

★★ **관통 원칙 — 정산은 멱등하다** ─────────────────────────────────────────

같은 날짜로 두 번 돌아도 결과가 같아야 한다 (F-05 4.3). 근거는 코드가 아니라
**테이블의 `UniqueConstraint`** 다 — `DailySnapshot(participation, date)` ·
`ContestRanking(contest, participation, date)` · `RuleViolation(participation, rule, date)` ·
`WeeklyTurnover(participation, week_start)`. 전부 upsert 로만 쓴다.

    v1.0 스케줄러는 `crypto_rank` 를 TRUNCATE 후 재적재했다(결함).
    동기화 중에 조회하면 빈 테이블이 보이는 창이 생긴다. v2.0 은 전부 upsert 다.

★★ **의존 방향** ───────────────────────────────────────────────────────────

규약 1.1 은 `market ◀ trading ◀ contests` 다. 이 파일은 가장 바깥이라
`trading.models` · `market.models` 를 마음껏 읽어도 된다. **반대는 안 된다** —
그래서 `contests/rules.py`(주문 경로가 부르는 쪽)는 `trading` 을 import 하지 않는다.
정산은 주문 경로가 부르지 않으므로 그 제약에서 자유롭다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI + SQLAlchemy 였다면 `session.bulk_save_objects()` 와 `ON CONFLICT` 를 손으로
적었을 자리다. Django 는 `bulk_create(update_conflicts=True, unique_fields=…)` 가
그 일을 한다 — **4.1 부터 PostgreSQL 에서 `ON CONFLICT DO UPDATE` 로 나간다.**
다만 `update_fields` 를 빠뜨리면 조용히 `DO NOTHING` 이 되어 **두 번째 실행부터
값이 갱신되지 않는다.** 멱등성 테스트가 그것을 잡는다.
"""

import logging
from dataclasses import dataclass, field
from datetime import date as date_type, datetime, time as time_type, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from accounts.models import Account
from contests import scoring
from contests.models import (
    ApprovalMode,
    Contest,
    ContestRanking,
    ContestResult,
    ContestSectorWeight,
    ContestStatus,
    ContestUniverse,
    DailySnapshot,
    IntradaySnapshot,
    Participation,
    ParticipationStatus,
    PortfolioVisibility,
    RuleViolation,
    SnapshotHolding,
    ViolationRule,
    ViolationSeverity,
    Visibility,
    WeeklyTurnover,
)
from core.constants import AccountMode, AssetClass, OrderSide
from core.time import KST, now_kst, today_kst, week_start_kst
from market.models import QuoteCache, StockMaster, StockType, TradingCalendar
from market.sessions import MARKET_CLOSE, MARKET_OPEN, is_business_day

logger = logging.getLogger(__name__)

# 장중 스냅샷 간격(분). F-05 2.3 이 정한 10분이다.
# 09:00~15:30 을 10분으로 나누면 하루 40행 — `IntradaySnapshot` 은 당일분만 보관한다.
INTRADAY_INTERVAL_MINUTES = 10

# 정산 대상이 되는 참가 상태.
#
# ★ **실격·포기도 스냅샷을 남긴다.** 랭킹에서만 빠질 뿐(`is_ranked=False`) 계좌와
#   이력은 그대로 살아 있고(F-02 4.3), 화면은 그들을 순위 `-` 로 표시한다(F-05 3.2).
#   스냅샷을 끊으면 그날부터 포트폴리오 조회가 빈 화면이 된다.
SETTLED_STATUSES = (
    ParticipationStatus.APPROVED,
    ParticipationStatus.DISQUALIFIED,
    ParticipationStatus.WITHDRAWN,
)


# ─────────────────────────────────────────────────────────────────
# 1. 평가 — "지금 이 계좌는 얼마인가"
# ─────────────────────────────────────────────────────────────────


@dataclass
class HoldingView:
    """종목 한 줄. `DailySnapshot.holdings`(JSONB) 와 `SnapshotHolding`(행)의 **공통 원본**.

    ★ 둘을 각각 만들면 어긋난다. 이중 저장 규약(E-02 6.2)이 "JSONB 가 정본" 이라고
      정한 것은 *어긋났을 때의 복구 기준*이지, 어긋나도 된다는 뜻이 아니다.
      한 객체에서 두 표현을 뽑는다.
    """

    symbol: str
    name: str
    sector_code: str
    sector_name: str
    qty: Decimal
    avg_price: Decimal
    close_price: Decimal
    value: int
    weight_pct: Decimal
    pnl: int
    pnl_pct: Decimal
    is_price_estimated: bool = False

    def as_json(self) -> dict:
        """JSONB 에 넣을 모양. **`Decimal` 은 JSON 이 모른다** — 문자열로 넘긴다.

        ★ `float` 로 바꾸지 않는다. 수량 0.00000001 BTC 같은 값이 부동소수로
          뭉개지면 감사용 원본이라는 이 컬럼의 존재 이유가 사라진다.
        """
        return {
            "symbol": self.symbol,
            "name": self.name,
            "sector_code": self.sector_code,
            "sector_name": self.sector_name,
            "qty": str(self.qty),
            "avg_price": str(self.avg_price),
            "close_price": str(self.close_price),
            "value": self.value,
            "weight_pct": str(self.weight_pct),
            "pnl": self.pnl,
            "pnl_pct": str(self.pnl_pct),
            "is_price_estimated": self.is_price_estimated,
        }


@dataclass
class Valuation:
    """계좌 하나의 평가 결과."""

    cash: int
    holdings: list[HoldingView] = field(default_factory=list)
    estimated_symbols: list[str] = field(default_factory=list)

    @property
    def position_value(self) -> int:
        return sum(item.value for item in self.holdings)

    @property
    def total_asset(self) -> int:
        return self.cash + self.position_value

    def sector_rows(self) -> list[dict]:
        """섹터별 비중. `DailySnapshot.sector_weights` 에 그대로 들어간다."""
        buckets: dict[str, dict] = {}
        for item in self.holdings:
            code = item.sector_code or "UNKNOWN"
            bucket = buckets.setdefault(
                code,
                {"sector_code": code, "sector_name": item.sector_name or "미분류", "value": 0},
            )
            bucket["value"] += item.value
        total = self.total_asset
        rows = sorted(buckets.values(), key=lambda row: row["value"], reverse=True)
        for row in rows:
            row["weight_pct"] = str(scoring.ratio_pct(row["value"], total))
        return rows


def closing_prices(symbols: list[str]) -> tuple[dict[str, Decimal], set[str]]:
    """정산에 쓸 종목별 가격을 모은다.

    Returns:
        `(가격 맵, 시세 캐시에서 얻지 못해 종목 마스터 종가로 대신한 종목들)`.

    ★★ **15:40 에는 "진짜 종가" 가 아직 없다** ────────────────────────────────

    F-05 4.1 은 *"15:40 ① 종가 확정 대기 → 최종가 수집"* 이라고 적었지만, 우리의
    종가 원천인 pykrx 일배치는 **16:00 (잡 8)** 에 돈다. 20분 앞서 도는 정산이
    그 값을 볼 수는 없다. 그래서 순서를 이렇게 둔다::

        ① QuoteCache        15:30 직전 마지막 폴링값 — 사실상 종가다
        ② StockMaster.close_price  전 영업일 종가 — 폴링이 닿지 않은 종목의 대타
        ③ (호출부) 포지션 평단      ①②가 다 없을 때. 평가액이 0 이 되는 것보다 낫다

    ★ **왜 잡 순서를 바꿔 16:05 에 정산하지 않는가** — 참가자는 장이 끝나면 바로
      순위를 보고 싶어 한다. 20분의 정확도 차이(15:30 값 vs 15:30 확정 종가)는
      **대개 0 이다.** 시간외 거래는 우리 대회의 대상이 아니기 때문이다.
      더 정확한 값이 필요하면 16:00 이후에 `settle_daily` 를 한 번 더 돌리면 된다 —
      멱등하므로 덮어쓰기만 한다.

    ★ **시뮬레이션 가격은 쓰지 않는다** (F-16 4.1). 대회 계좌에는 애초에 들어오지
      않지만, 연습 계좌와 캐시 테이블을 공유하므로 여기서 한 번 더 거른다.
    """
    if not symbols:
        return {}, set()

    prices: dict[str, Decimal] = {}
    for row in QuoteCache.objects.filter(
        asset_class=AssetClass.STOCK, symbol__in=symbols, is_simulated=False
    ).values_list("symbol", "price"):
        if row[1] and row[1] > 0:
            prices[row[0]] = row[1]

    missing = [symbol for symbol in symbols if symbol not in prices]
    fallback: set[str] = set()
    if missing:
        for symbol, close in StockMaster.objects.filter(
            symbol__in=missing
        ).values_list("symbol", "close_price"):
            if close and close > 0:
                prices[symbol] = close
                fallback.add(symbol)
    return prices, fallback


def universe_map(contest: Contest) -> dict[str, tuple[str, str, str]]:
    """`{종목: (종목명, 업종코드, 업종명)}` — **대회 시작 시점에 얼린 것**을 쓴다.

    기간 중에 업종이 바뀌면 어제 합법이던 포트가 오늘 위반이 된다 (F-02 3.4).
    섹터 한도 판정과 스냅샷의 섹터 비중이 같은 기준을 보게 하려면 여기서도 얼린 값이다.
    """
    return {
        row[0]: (row[1], row[2], row[3])
        for row in ContestUniverse.objects.filter(contest=contest).values_list(
            "symbol", "name", "sector_code", "sector_name"
        )
    }


def evaluate_account(
    account: Account,
    positions: list,
    prices: dict[str, Decimal],
    names: dict[str, tuple[str, str, str]],
    *,
    estimated: set[str] | None = None,
) -> Valuation:
    """계좌 하나를 평가한다.

    Args:
        positions: 이 계좌의 `trading.Position` 목록. 호출부가 미리 읽어 넘긴다
            (참가자 100명마다 쿼리를 날리면 N+1 이다).
        prices: `closing_prices()` 의 결과.
        names: `universe_map()` 의 결과. 없으면 종목 마스터로 보완한다.
        estimated: 시세 캐시가 아니라 종목 마스터 종가로 대신한 종목들.

    ★ **가격을 못 얻은 종목은 평단으로 평가한다.** 0 으로 두면 순자산이 줄어
      다른 종목의 비중이 부풀고, **엉뚱한 종목이 한도 위반으로 기록된다.**
      `trading.services._build_portfolio` 가 주문 경로에서 하는 판단과 같다.
    """
    estimated = estimated or set()
    total_cash = account.cash
    rows: list[HoldingView] = []
    estimated_symbols: list[str] = []

    for position in positions:
        price = prices.get(position.symbol)
        is_estimated = position.symbol in estimated
        if price is None or price <= 0:
            price = position.avg_price
            is_estimated = True
        if is_estimated:
            estimated_symbols.append(position.symbol)

        value = scoring.round_krw(position.qty * price)
        cost = scoring.round_krw(position.qty * position.avg_price)
        name, sector_code, sector_name = names.get(position.symbol, ("", "", ""))
        rows.append(
            HoldingView(
                symbol=position.symbol,
                name=name,
                sector_code=sector_code,
                sector_name=sector_name,
                qty=position.qty,
                avg_price=position.avg_price,
                close_price=price,
                value=value,
                weight_pct=Decimal(0),          # 총자산이 확정된 뒤 아래에서 채운다
                pnl=value - cost,
                pnl_pct=(
                    ((price / position.avg_price - 1) * 100).quantize(scoring.PCT_QUANT)
                    if position.avg_price > 0
                    else Decimal(0)
                ),
                is_price_estimated=is_estimated,
            )
        )

    valuation = Valuation(cash=total_cash, holdings=rows, estimated_symbols=estimated_symbols)
    # ★ 비중의 분모는 **순자산**(현금 포함)이다 — 편입 한도와 같은 자로 재야
    #   화면의 "현재 비중" 과 규칙 위반 판정이 어긋나지 않는다 (F-03 3.1).
    total = valuation.total_asset
    for row in rows:
        row.weight_pct = scoring.ratio_pct(row.value, total)
    return valuation


def positions_by_account(account_ids: list[int]) -> dict[int, list]:
    """`{계좌 id: [Position, …]}` — **한 번의 쿼리로** 참가자 전원의 보유를 읽는다.

    ★ 참가자마다 `account.positions.all()` 을 부르면 100명이면 쿼리 100번이다(N+1).
      정산은 배치라 응답 시간이 문제되지 않는다고 생각하기 쉽지만, 잡 4 는
      **장중 10분마다** 같은 일을 한다.
    """
    from trading.models import Position       # noqa: PLC0415 — 순환 임포트 회피

    grouped: dict[int, list] = {account_id: [] for account_id in account_ids}
    for position in Position.objects.filter(account_id__in=account_ids).order_by("symbol"):
        grouped.setdefault(position.account_id, []).append(position)
    return grouped


def _fill_missing_names(rows: list[HoldingView]) -> None:
    """유니버스에 없는 종목의 이름·업종을 종목 마스터로 메운다.

    ★ **유니버스에 없는 종목을 보유할 수 있는가** — 있다. 유니버스는 대회 시작
      시점의 스냅샷이고, 그 뒤에 상장된 종목은 들어 있지 않다. 매수는 막히지만
      (`rules.check_symbol_tradable` 의 `NO_SECTOR`), 운영자가 Admin 에서 넣어준
      포지션이나 유니버스 재고정 이전의 보유가 남아 있을 수 있다.
      **이름이 빈 채로 화면에 나가는 것보다 마스터에서 채우는 편이 낫다.**
    """
    unknown = [row.symbol for row in rows if not row.name]
    if not unknown:
        return
    master = {
        item[0]: (item[1], item[2], item[3])
        for item in StockMaster.objects.filter(symbol__in=unknown).values_list(
            "symbol", "name", "sector_code", "sector_name"
        )
    }
    for row in rows:
        if row.name:
            continue
        name, sector_code, sector_name = master.get(row.symbol, (row.symbol, "", ""))
        row.name = name
        if not row.sector_code:
            row.sector_code, row.sector_name = sector_code, sector_name


# ─────────────────────────────────────────────────────────────────
# 2. 당일 거래 집계 — 회전율의 재료 (F-04 5.3)
# ─────────────────────────────────────────────────────────────────


def day_bounds(day: date_type) -> tuple[datetime, datetime]:
    """KST 하루의 시작·끝(aware). `[start, end)` 반개구간이다.

    ★ **KST 로 잘라야 한다.** DB 는 UTC 로 저장하므로 UTC 날짜로 자르면 한국의
      09:00~15:30 거래가 전날/다음날로 흩어진다 (`core/time.py` 참조).
    """
    start = datetime.combine(day, time_type.min, tzinfo=KST)
    return start, start + timedelta(days=1)


def daily_trade_amounts(account_ids: list[int], day: date_type) -> dict[int, dict]:
    """계좌별 당일 매수·매도 금액과 수수료·세금.

    Returns:
        `{계좌 id: {"buy": …, "sell": …, "fee": …, "tax": …}}`

    ★★ **왜 `Order` 가 아니라 `Execution` 에서 세는가** ──────────────────────

    주문은 **접수된 날과 체결된 날이 다를 수 있다.** 장외에 낸 주문은
    `PENDING_OPEN` 으로 대기하다 다음 영업일 09:00 에 접수된다 (F-03 8장).
    `Order.created_at` 으로 세면 그 주문의 거래대금이 하루 앞당겨진다.
    회전율은 **실제로 사고판 날**의 금액이어야 한다.

    ★★ **수수료·세금은 조각별 기록이 없어 안분한다** ──────────────────────

    `Execution` 에는 금액만 있고 수수료가 없다 — 수수료는 체결 회차마다 계산해
    `Order.fee` 에 **누적**된다 (`trading.services.apply_fills`). 그래서 그날 체결된
    금액의 비율로 나눈다::

        그날 수수료 = 주문 수수료 × (그날 체결금액 / 주문 총 체결금액)

    ★ **대회 주문은 날짜를 넘기지 않으므로 이 안분은 사실상 항등식이다.**
      장 마감 잡(F-20 잡 6)이 15:35 에 미체결 대회 주문을 전부 취소하기 때문이다
      (F-03 2장). 연습 계좌에서만 여러 날에 걸친 주문이 생길 수 있고, 그때도
      **오차는 원 단위 반올림 한 번**이다.
    """
    if not account_ids:
        return {}

    from trading.models import Execution, Order      # noqa: PLC0415 — 순환 임포트 회피

    start, end = day_bounds(day)
    per_order = list(
        Execution.objects.filter(
            order__account_id__in=account_ids, executed_at__gte=start, executed_at__lt=end
        )
        .values("order_id")
        .annotate(day_gross=Sum("amount"))
    )
    if not per_order:
        return {}

    order_rows = {
        row["id"]: row
        for row in Order.objects.filter(
            id__in=[item["order_id"] for item in per_order]
        ).values("id", "account_id", "side", "fee", "tax", "gross_amount")
    }

    totals: dict[int, dict] = {}
    for item in per_order:
        order = order_rows.get(item["order_id"])
        if order is None:
            continue
        bucket = totals.setdefault(
            order["account_id"], {"buy": 0, "sell": 0, "fee": 0, "tax": 0}
        )
        day_gross = int(item["day_gross"] or 0)
        bucket["buy" if order["side"] == OrderSide.BUY else "sell"] += day_gross

        total_gross = int(order["gross_amount"] or 0)
        share = (
            Decimal(day_gross) / Decimal(total_gross) if total_gross > 0 else Decimal(1)
        )
        bucket["fee"] += scoring.round_krw(Decimal(order["fee"]) * share)
        bucket["tax"] += scoring.round_krw(Decimal(order["tax"]) * share)
    return totals


# ─────────────────────────────────────────────────────────────────
# 3. 한도 위반 판정 — **사후 경고** (F-04 3.4)
# ─────────────────────────────────────────────────────────────────


def evaluate_violations(
    contest: Contest, valuation: Valuation, sector_limits: dict[str, Decimal],
    small_caps: set[str],
) -> list[dict]:
    """보유 상태가 한도를 넘었는지 본다. **주문을 막지 않는다.**

    주문 시점의 판정은 `contests/rules.py` 가 이미 했다. 여기서 보는 것은
    **가격이 움직여 생긴 초과**다 — F-04 3.4 가 *"일시적으로 허용"* 한다고 명시한 것.

        강제 매도하지 않는다. 경고만 표시하고 위반일수를 누적해
        **운영자에게 근거 데이터를 준다.** 자동 실격은 하지 않는다.

    Returns:
        `DailySnapshot.violations` 에 그대로 들어갈 dict 목록.
        비어 있으면 그날은 깨끗한 것이다.
    """
    rules = contest.rule_set or {}
    total = valuation.total_asset
    if total <= 0:
        return []

    found: list[dict] = []

    # ── 종목별 한도 ─────────────────────────────────────────────
    exceptions = rules.get("position_limit_exceptions") or {}
    default_limit = Decimal(str(rules.get("position_limit_pct", 15.0)))
    for row in valuation.holdings:
        limit = Decimal(str(exceptions.get(row.symbol, default_limit)))
        if row.weight_pct > limit:
            found.append({
                "rule": ViolationRule.POSITION_LIMIT,
                "symbol": row.symbol,
                "name": row.name,
                "current_pct": str(row.weight_pct),
                "limit_pct": str(limit),
                "excess_krw": row.value - scoring.round_krw(Decimal(total) * limit / 100),
            })

    # ── 섹터 한도 ───────────────────────────────────────────────
    #
    # ★ `sector_limits` 가 비어 있으면 검사하지 않는다. 섹터 비중 스냅샷이 아직
    #   만들어지지 않은 대회에서 규칙만 먼저 발동하면 **모든 섹터가 위반**이 된다
    #   (`rules._check_sector_limit` 과 같은 판단).
    if sector_limits:
        for row in valuation.sector_rows():
            limit = sector_limits.get(row["sector_code"])
            if limit is None:
                continue
            current = Decimal(row["weight_pct"])
            if current > limit:
                found.append({
                    "rule": ViolationRule.SECTOR_LIMIT,
                    "sector_code": row["sector_code"],
                    "sector_name": row["sector_name"],
                    "current_pct": str(current),
                    "limit_pct": str(limit),
                    "excess_krw": row["value"] - scoring.round_krw(Decimal(total) * limit / 100),
                })

    # ── 소형주 합산 한도 ────────────────────────────────────────
    small_limit = Decimal(str(rules.get("small_cap_total_limit_pct") or 0))
    if small_limit > 0 and small_caps:
        value = sum(row.value for row in valuation.holdings if row.symbol in small_caps)
        current = scoring.ratio_pct(value, total)
        if current > small_limit:
            found.append({
                "rule": ViolationRule.SMALL_CAP_LIMIT,
                "current_pct": str(current),
                "limit_pct": str(small_limit),
                "threshold_krw": int(rules.get("small_cap_threshold_krw") or 0),
                "excess_krw": value - scoring.round_krw(Decimal(total) * small_limit / 100),
            })

    return found


def small_cap_symbols(contest: Contest) -> set[str]:
    """이 대회 기준으로 소형주인 종목들 (시총 < `small_cap_threshold_krw`).

    ★ **시총은 최신값을 본다.** 유니버스가 얼리는 것은 *업종*이지 시총이 아니다
      (`ContestUniverse` docstring). "지금 소형주인가" 는 최신 마스터가 답한다.
    """
    threshold = int((contest.rule_set or {}).get("small_cap_threshold_krw") or 0)
    if threshold <= 0:
        return set()
    return set(
        StockMaster.objects.filter(market_cap__lt=threshold, market_cap__gt=0)
        .values_list("symbol", flat=True)
    )


def sector_limit_map(contest: Contest) -> dict[str, Decimal]:
    """`{업종코드: 허용 한도 %}` — 대회 시작 시 굳혀 둔 값."""
    return {
        row[0]: row[1]
        for row in ContestSectorWeight.objects.filter(contest=contest).values_list(
            "sector_code", "limit_pct"
        )
    }


# ─────────────────────────────────────────────────────────────────
# 4. 일별 스냅샷 기록 (F-05 2장 · 4.1)
# ─────────────────────────────────────────────────────────────────


@transaction.atomic
def write_daily_snapshot(
    participation: Participation,
    day: date_type,
    valuation: Valuation,
    trades: dict,
    violations: list[dict],
) -> tuple[DailySnapshot, bool]:
    """참가자 1명 × 하루 1행. **upsert 다.**

    Returns:
        `(스냅샷, 새로 만들었는가)`. 두 번째 인자를 돌려주는 이유는 잡이
        `created` 와 `updated` 를 나눠 세기 위해서다 — **재실행인데 created 가 0이
        아니면 upsert 키가 잘못 잡힌 것**이고, 합계만 보면 그 사고가 안 보인다
        (`core/jobs.py` 의 `SyncResult` 주석).

    ★★ **JSONB 원본과 정규화 파생을 같은 트랜잭션에서 쓴다** (E-02 6.2) ────────

    `DailySnapshot.holdings`(jsonb) 가 감사용 정본이고 `SnapshotHolding`(행)이
    조회용 파생이다. 정합성 규약 3줄 중 두 줄이 여기서 지켜진다::

        ① 같은 트랜잭션에서만 쓴다
        ② 파생은 **delete + bulk_create** — 부분 갱신 금지

    부분 갱신을 금지하는 이유는 **판 종목이 남기 때문**이다. 어제 5종목이었다가
    오늘 3종목이면, upsert 만 해서는 사라진 2종목의 어제 행이 오늘 날짜로 남는다.
    """
    initial_capital = participation.contest.initial_capital
    if participation.account is not None and participation.account.initial_capital:
        # ★ 계좌의 시작 자본이 우선이다. 대회 설정을 중간에 바꿔도 이미 만들어진
        #   계좌의 기준선은 흔들리면 안 된다 — 수익률이 통째로 달라진다.
        initial_capital = participation.account.initial_capital

    total_asset = valuation.total_asset
    nav = scoring.nav_for(total_asset, initial_capital)
    prev_nav = (
        DailySnapshot.objects.filter(participation=participation, date__lt=day)
        .order_by("-date")
        .values_list("nav", flat=True)
        .first()
    )

    _fill_missing_names(valuation.holdings)

    snapshot, created = DailySnapshot.objects.update_or_create(
        participation=participation,
        date=day,
        defaults={
            "cash": valuation.cash,
            "position_value": valuation.position_value,
            "total_asset": total_asset,
            "nav": nav,
            "daily_return_pct": scoring.daily_return_pct(nav, prev_nav),
            "cumulative_return_pct": scoring.cumulative_return_pct(nav),
            "position_count": len(valuation.holdings),
            "invested_ratio_pct": scoring.ratio_pct(valuation.position_value, total_asset),
            "buy_amount": trades.get("buy", 0),
            "sell_amount": trades.get("sell", 0),
            "fee_amount": trades.get("fee", 0),
            "tax_amount": trades.get("tax", 0),
            "holdings": [row.as_json() for row in valuation.holdings],
            "sector_weights": valuation.sector_rows(),
            "violations": violations,
        },
    )

    SnapshotHolding.objects.filter(snapshot=snapshot).delete()
    SnapshotHolding.objects.bulk_create([
        SnapshotHolding(
            snapshot=snapshot,
            contest_id=participation.contest_id,
            participation_id=participation.pk,
            date=day,
            symbol=row.symbol,
            name=row.name,
            sector_code=row.sector_code,
            qty=row.qty,
            avg_price=row.avg_price,
            close_price=row.close_price,
            value=row.value,
            weight_pct=row.weight_pct,
            pnl=row.pnl,
            pnl_pct=row.pnl_pct,
        )
        for row in valuation.holdings
    ])

    _record_violations(participation, day, violations)
    return snapshot, created


def _record_violations(participation: Participation, day: date_type, violations: list[dict]) -> None:
    """`RuleViolation` 을 하루 단위로 upsert 한다.

    ★ `UNIQUE(participation, rule, date)` 이므로 **규칙 하나당 하루 1행**이다.
      종목 3개가 동시에 종목한도를 넘었어도 행은 하나이고, 상세는 `detail` 에 모은다.
      규칙 종류가 위반의 단위이기 때문이다 — 운영자는 "이 사람이 며칠 동안 종목한도를
      어겼나" 를 세지, "종목별로 몇 번" 을 세지 않는다.

    ★★ **해소된 위반은 지우지 않고 `is_resolved` 로 표시한다.** 지우면
      "3일 연속 위반했다가 오늘 풀었다" 는 사실이 사라진다. F-04 3.4 가 운영자에게
      주려는 것이 바로 그 이력이다.
    """
    by_rule: dict[str, list[dict]] = {}
    for item in violations:
        by_rule.setdefault(item["rule"], []).append(item)

    for rule, items in by_rule.items():
        RuleViolation.objects.update_or_create(
            participation=participation,
            rule=rule,
            date=day,
            defaults={
                "severity": ViolationSeverity.WARN,
                "detail": {"items": items},
                "is_resolved": False,
                "resolved_at": None,
            },
        )

    # 오늘 안 걸린 규칙 중 **오늘 행이 이미 있는 것**은 재실행으로 상태가 바뀐
    # 경우다(16:00 에 다시 돌려 종가가 갱신됐다든지). 해소로 표시한다.
    stale = RuleViolation.objects.filter(participation=participation, date=day).exclude(
        rule__in=list(by_rule)
    )
    if stale.exists():
        stale.update(is_resolved=True, resolved_at=timezone.now())


# ─────────────────────────────────────────────────────────────────
# 5. 랭킹 (F-05 3.1 · 7장)
# ─────────────────────────────────────────────────────────────────


@transaction.atomic
def rebuild_rankings(contest: Contest, day: date_type) -> int:
    """그날의 `ContestRanking` 을 다시 만든다. **조회 시점에 정렬하지 않기 위해서다** (F-05 7장).

    Returns:
        기록한 행 수.

    ★ **진행 중 순위는 단순 누적 수익률**이다 (F-05 3.1). 관리 점수는 최종 정산에만
      들어간다 — *"진행 중에 복잡한 점수를 보여주면 참가자가 전략을 세울 수 없다."*

    ★ `is_ranked=False`(실격·포기)는 **행은 만들되 `rank=None`** 으로 둔다.
      목록에서 지우면 "내가 왜 없지?" 라는 문의가 온다 (F-05 3.2).
    """
    snapshots = list(
        DailySnapshot.objects.filter(participation__contest=contest, date=day)
        .select_related("participation")
        .order_by("participation_id")
    )
    if not snapshots:
        return 0

    prev_ranks = dict(
        ContestRanking.objects.filter(contest=contest, date__lt=day)
        .order_by("participation_id", "-date")
        .distinct("participation_id")
        .values_list("participation_id", "rank")
    )

    ranked = [item for item in snapshots if item.participation.is_ranked]
    ranks = scoring.competition_ranks([item.cumulative_return_pct for item in ranked])
    rank_by_participation = {
        item.participation_id: rank for item, rank in zip(ranked, ranks)
    }

    ContestRanking.objects.filter(contest=contest, date=day).delete()
    ContestRanking.objects.bulk_create([
        ContestRanking(
            contest=contest,
            participation=item.participation,
            date=day,
            rank=rank_by_participation.get(item.participation_id),
            prev_rank=prev_ranks.get(item.participation_id),
            nav=item.nav,
            cumulative_return_pct=item.cumulative_return_pct,
            daily_return_pct=item.daily_return_pct,
            position_count=item.position_count,
            invested_ratio_pct=item.invested_ratio_pct,
        )
        for item in snapshots
    ])
    return len(snapshots)


# ─────────────────────────────────────────────────────────────────
# 6. 주간 회전율 (F-04 5장)
# ─────────────────────────────────────────────────────────────────


def compute_weekly_turnover(participation: Participation, week_start: date_type) -> dict:
    """한 주 회전율을 **계산만** 한다. 저장하지 않는다.

    Returns:
        `WeeklyTurnover` 의 defaults 로 그대로 쓸 수 있는 dict.

    ★★ **저장과 나눈 이유는 `--dry-run` 때문이다.** 커맨드가 "DB 에 아무것도 쓰지
      않았습니다" 라고 말하는데 회전율 행이 늘어나 있으면, 그 약속이 거짓이 된다.
      dry-run 은 *무엇이 바뀔지 보는 것*이지 *실행한 것*이 아니다
      (`core/jobs.py` 의 `job_run(dry_run=…)` 과 같은 원칙).

    ★ 재료는 `DailySnapshot` 이다. 체결 테이블을 매번 훑지 않는다 — v1.0 결함 D-3
      (전 회원 전 포지션 매번 재계산)의 교훈이다 (F-04 5.3).
    """
    week_end = week_start + timedelta(days=6)
    rows = DailySnapshot.objects.filter(
        participation=participation, date__gte=week_start, date__lte=week_end
    )
    aggregate = rows.aggregate(
        buy=Sum("buy_amount"), sell=Sum("sell_amount"), asset=Sum("total_asset")
    )
    buy = int(aggregate["buy"] or 0)
    sell = int(aggregate["sell"] or 0)
    day_count = rows.count()

    # ★ 평균 운용금액은 **스냅샷이 있는 날로만** 나눈다. 주 5일로 고정해 나누면
    #   대회 첫 주(수요일 시작)나 공휴일이 낀 주에서 분모가 부풀어 회전율이
    #   실제보다 낮게 나오고, **없던 위반이 생긴다.**
    avg_asset = int(Decimal(aggregate["asset"] or 0) / day_count) if day_count else 0

    minimum = (participation.contest.rule_set or {}).get("weekly_turnover_min_pct", 5.0)
    pct = scoring.turnover_pct(buy, sell, avg_asset)

    return {
        "buy_amount": buy,
        "sell_amount": sell,
        "avg_asset": avg_asset,
        "turnover_pct": pct,
        # ★ 스냅샷이 하루도 없는 주는 위반으로 세지 않는다. 대회가 시작되기 전이거나
        #   참가 승인 전이라 **평가할 대상이 없었던** 주다.
        "is_violation": day_count > 0 and scoring.is_turnover_violation(pct, minimum),
    }


def refresh_weekly_turnover(
    participation: Participation, week_start: date_type, *, confirm: bool
) -> tuple[WeeklyTurnover, bool]:
    """계산 결과를 `WeeklyTurnover` 에 upsert 한다.

    Args:
        confirm: `True` 면 **확정**(지난 주), `False` 면 이번 주 잠정치.

    Returns:
        `(행, 새로 만들었는가)`.

    ★★ **"확정 위반"과 "예상 위반"을 나누는 이유** (F-04 5.2) ─────────────────

    화면이 둘을 나눠 보여준다. *"새로운 주가 시작하는 월요일 아침 장시작 전에는
    매매가 있을 수 없으므로 금주의 예상값은 1회 위반으로 보이는 게 정상입니다."*
    이 안내를 넣지 않으면 월요일마다 문의가 들어온다.
    """
    return WeeklyTurnover.objects.update_or_create(
        participation=participation,
        week_start=week_start,
        defaults={**compute_weekly_turnover(participation, week_start), "is_confirmed": confirm},
    )


def renumber_violations(participation: Participation) -> int:
    """확정 위반에 **누적 번호**를 다시 매기고 총 위반 횟수를 돌려준다.

    `violation_seq` 는 "이게 몇 번째 확정 위반인가" 다. 매주 새로 세는 이유는
    운영자가 Admin 에서 `is_violation` 을 손으로 되돌릴 수 있기 때문이다 —
    한 번 매긴 번호를 그대로 두면 **되돌린 뒤에도 번호가 4번까지 남는다.**
    """
    rows = list(
        WeeklyTurnover.objects.filter(participation=participation, is_confirmed=True)
        .order_by("week_start")
    )
    seq = 0
    changed = []
    for row in rows:
        expected = seq + 1 if row.is_violation else 0
        if row.is_violation:
            seq += 1
        if row.violation_seq != expected:
            row.violation_seq = expected
            changed.append(row)
    if changed:
        WeeklyTurnover.objects.bulk_update(changed, ["violation_seq"])
    return seq


def turnover_violation_limit(participation: Participation) -> int:
    """허용 위반 횟수. 기본 3 — **4회째에 정지**다 (F-04 5.1)."""
    return int((participation.contest.rule_set or {}).get("weekly_turnover_violation_limit", 3))


def projected_violation_total(
    participation: Participation, week_start: date_type, is_violation: bool
) -> int:
    """이번 주를 반영했을 때의 확정 위반 누적 — **읽기만 한다.**

    `--dry-run` 이 "이대로 가면 누가 잘리는가" 를 미리 보여주기 위한 것이다.
    `renumber_violations` 는 같은 답을 내지만 행을 저장하므로 dry-run 에서 쓸 수 없다.
    """
    prior = (
        WeeklyTurnover.objects.filter(
            participation=participation, is_confirmed=True, is_violation=True
        )
        .exclude(week_start=week_start)
        .count()
    )
    return prior + (1 if is_violation else 0)


def apply_turnover_disqualification(participation: Participation, total_violations: int) -> bool:
    """확정 위반이 허용 횟수를 넘으면 실격 처리한다 (F-04 5.4).

    Returns:
        이번 호출로 상태가 바뀌었으면 `True`.

    ★★ **회전율만이 유일한 자동 실격이다.** 편입 한도 초과는 *"적극적으로 해소하려
      노력했는가"* 라는 정성 판단이 필요해 자동화하지 않는다 (F-04 3.4).
      회전율은 숫자 하나로 판정되고 규칙에 횟수까지 못박혀 있어 다르다.

    ★ **계좌·주문 이력은 그대로 둔다.** 랭킹에서만 빠진다. 운영자는 Admin 에서
      되돌릴 수 있다 (F-04 5.4).
    """
    limit = turnover_violation_limit(participation)
    if total_violations <= limit:
        return False
    if participation.status == ParticipationStatus.DISQUALIFIED:
        return False

    participation.status = ParticipationStatus.DISQUALIFIED
    participation.is_ranked = False
    participation.disqualified_at = timezone.now()
    participation.disqualified_reason = (
        f"주간 회전율 기준 미달 {total_violations}회 (허용 {limit}회) — 자동 정지"
    )
    participation.save(update_fields=[
        "status", "is_ranked", "disqualified_at", "disqualified_reason", "updated_at",
    ])
    return True


# ─────────────────────────────────────────────────────────────────
# 7. 유니버스 고정 (F-02 3.4 · F-04 3.2)
# ─────────────────────────────────────────────────────────────────


@transaction.atomic
def freeze_universe(contest: Contest) -> tuple[int, int]:
    """대회 시작 시점의 **종목 업종**과 **시장 섹터 비중**을 얼린다.

    Returns:
        `(종목 수, 섹터 수)`.

    ★★ **왜 얼리는가** — 기간 중에 업종 분류가 바뀌면 어제 합법이던 포트가 오늘
      위반이 된다. 참가자가 손쓸 수 없는 이유로 규칙을 어기게 만들면 안 된다.

    ★ **시총·거래대금·관리종목 지정은 얼리지 않는다.** 그건 "지금 살 수 있는가" 의
      판정이라 최신값이 맞고, `StockMaster` 가 매일 갱신한다 (`ContestUniverse` docstring).

    ★ **업종이 없는 종목은 넣지 않는다** (E-31). 유니버스에 없으면 매수가 막히는데
      (`rules.check_symbol_tradable` 의 `NO_SECTOR`), 그것이 의도다 — 섹터 한도가
      `max(시장 섹터 비중 × 2, 10%)` 인데 섹터를 모르면 넣을 값이 없고, 면제하면
      미분류 종목만 담아 **섹터 한도를 통째로 우회**할 수 있다.
    """
    frozen_at = timezone.now()
    rows = list(
        StockMaster.objects.filter(
            stock_type=StockType.COMMON, is_delisted=False
        ).exclude(sector_code="").values_list(
            "symbol", "name", "market", "sector_code", "sector_name", "market_cap"
        )
    )

    ContestUniverse.objects.filter(contest=contest).delete()
    ContestUniverse.objects.bulk_create([
        ContestUniverse(
            contest=contest,
            symbol=symbol,
            name=name,
            market=market,
            sector_code=sector_code,
            sector_name=sector_name,
            is_tradable_at_start=True,
            frozen_at=frozen_at,
        )
        for symbol, name, market, sector_code, sector_name, _cap in rows
    ])

    # ── 시장 섹터 비중 → 허용 한도 ──────────────────────────────
    rules = contest.rule_set or {}
    multiplier = Decimal(str(rules.get("sector_limit_multiplier", 2.0)))
    floor_pct = Decimal(str(rules.get("sector_limit_floor_pct", 10.0)))
    threshold = Decimal(str(rules.get("sector_limit_floor_threshold_pct", 5.0)))

    caps: dict[str, dict] = {}
    for _symbol, _name, _market, sector_code, sector_name, market_cap in rows:
        bucket = caps.setdefault(sector_code, {"name": sector_name, "cap": 0})
        bucket["cap"] += int(market_cap or 0)
    total_cap = sum(item["cap"] for item in caps.values())

    ContestSectorWeight.objects.filter(contest=contest).delete()
    weights = []
    for sector_code, bucket in caps.items():
        market_weight = scoring.ratio_pct(bucket["cap"], total_cap)
        # ★★ **하한 10% 는 작은 섹터에만 준다** (F-04 3.2 의 괄호) ────────────
        #    "에너지 2% → 2×2=4% 가 아니라 하한 10%". 큰 섹터에까지 하한을 주면
        #    의미가 없다(이미 배수가 하한보다 크다). 기본값(배수 2 · 하한 10 ·
        #    임계 5)에서는 두 식이 같지만, 배수를 바꾸면 갈라진다 —
        #    **명세의 문장 그대로** 적어 둔다.
        limit = market_weight * multiplier
        if market_weight <= threshold:
            limit = max(limit, floor_pct)
        weights.append(
            ContestSectorWeight(
                contest=contest,
                sector_code=sector_code,
                sector_name=bucket["name"],
                market_weight_pct=market_weight,
                limit_pct=limit.quantize(scoring.PCT_QUANT),
            )
        )
    ContestSectorWeight.objects.bulk_create(weights)

    contest.universe_frozen_at = frozen_at
    contest.save(update_fields=["universe_frozen_at", "updated_at"])
    return len(rows), len(weights)


# ─────────────────────────────────────────────────────────────────
# 8. 최종 정산 (F-05 4.3 · F-04 6장)
# ─────────────────────────────────────────────────────────────────


def symbol_profits(participation: Participation) -> dict[str, int]:
    """종목별 총손익 = **실현손익 + 최종 평가손익**.

    관리 점수의 수익 집중도(HHI_pnl)가 쓰는 재료다.

    ★ 실현손익만 보면 **끝까지 들고 있어 이익이 난 종목이 통째로 빠진다.**
      평가손익만 보면 이미 판 종목의 성과가 사라진다. 둘을 합쳐야
      "이 대회에서 이 종목으로 얼마를 벌었나" 가 된다.
    """
    from trading.models import Order        # noqa: PLC0415 — 순환 임포트 회피

    profits: dict[str, int] = {}
    if participation.account_id:
        realized = (
            Order.objects.filter(
                account_id=participation.account_id, realized_pnl__isnull=False
            )
            .values("symbol")
            .annotate(total=Sum("realized_pnl"))
        )
        for row in realized:
            profits[row["symbol"]] = profits.get(row["symbol"], 0) + int(row["total"] or 0)

    last = (
        DailySnapshot.objects.filter(participation=participation)
        .order_by("-date")
        .values_list("pk", flat=True)
        .first()
    )
    if last is not None:
        for symbol, pnl in SnapshotHolding.objects.filter(snapshot_id=last).values_list(
            "symbol", "pnl"
        ):
            profits[symbol] = profits.get(symbol, 0) + int(pnl or 0)
    return profits


def management_metrics(participation: Participation) -> tuple[Decimal, Decimal, Decimal]:
    """관리 점수와 그 재료 두 개를 낸다.

    Returns:
        `(관리 원점수, 포트 HHI, 손익 HHI)`.

    ★ **중간 값(HHI 둘)을 함께 돌려주는 이유** — `ContestResult` 에 저장하기
      위해서다. 산식은 1회 대회 후 조정을 전제하므로(F-04 6.2), 점수만 남기면
      **왜 그 점수가 나왔는지 되짚을 수 없다** (`ContestResult` docstring).
    """
    daily_values: list[dict[str, int]] = []
    for holdings in DailySnapshot.objects.filter(participation=participation).order_by(
        "date"
    ).values_list("holdings", flat=True):
        daily_values.append(
            {row["symbol"]: int(row["value"]) for row in (holdings or []) if row.get("value")}
        )

    weights = scoring.average_weights(daily_values)
    port_hhi = scoring.herfindahl(weights.values()) if weights else Decimal(1)

    profits = symbol_profits(participation)
    winners = [value for value in profits.values() if value > 0]
    pnl_hhi = scoring.herfindahl(winners) if winners else Decimal(1)

    port_score = scoring.portfolio_dispersion_score(port_hhi)
    pnl_score = scoring.pnl_dispersion_score(pnl_hhi, len(winners))
    return scoring.management_score(port_score, pnl_score), port_hhi, pnl_hhi


@transaction.atomic
def finalize_contest(contest: Contest) -> int:
    """대회 하나를 최종 확정한다 (F-05 4.3).

        ② 최종 스냅샷 확정 → ③ 관리 점수 → ④ 백분위·최종 점수·등급
        → ⑤ ContestResult → ⑥ 계좌 동결 → ⑦ SETTLING → CLOSED

    Returns:
        확정한 참가자 수.

    ★ **`is_ranked=False` 도 `ContestResult` 행을 만든다.** 다만 `final_rank` 는
      비운다 — 실격자에게도 "내 최종 수익률이 얼마였나" 를 보여줘야 하고,
      점수·등급은 상대평가라 대상에서 빼는 것이 맞다.

    ★★ **계좌 동결은 마지막이다.** 먼저 얼리면 정산 도중 예외가 났을 때
      **거래는 막혔는데 결과는 없는 상태**로 남는다. 한 트랜잭션 안이라 롤백되지만,
      순서를 이렇게 두면 그 사고 자체를 생각할 필요가 없다.
    """
    participations = list(
        Participation.objects.filter(
            contest=contest, status__in=SETTLED_STATUSES
        ).select_related("contest")
    )
    if not participations:
        contest.status = ContestStatus.CLOSED
        contest.save(update_fields=["status", "updated_at"])
        return 0

    rules = contest.rule_set or {}
    return_weight = rules.get("score_weight_return", 0.7)
    management_weight = rules.get("score_weight_management", 0.3)

    rows = []
    for participation in participations:
        snapshot = (
            DailySnapshot.objects.filter(participation=participation)
            .order_by("-date")
            .first()
        )
        return_score = snapshot.cumulative_return_pct if snapshot else Decimal(0)
        score, port_hhi, pnl_hhi = management_metrics(participation)
        rows.append({
            "participation": participation,
            "return_score": return_score,
            "management_score": score,
            "port_hhi": port_hhi,
            "pnl_hhi": pnl_hhi,
        })

    # ── 백분위는 **랭킹 대상자끼리만** 낸다 ─────────────────────
    #
    # ★ 실격자를 모수에 넣으면 남은 참가자의 백분위가 실격자 수만큼 올라간다.
    #   "누구와 겨뤘는가" 가 흔들리면 상대평가가 아니다.
    ranked = [row for row in rows if row["participation"].is_ranked]
    return_pcts = scoring.percentiles([row["return_score"] for row in ranked])
    management_pcts = scoring.percentiles([row["management_score"] for row in ranked])
    finals = [
        scoring.final_score(
            return_pct, management_pct,
            return_weight=return_weight, management_weight=management_weight,
        )
        for return_pct, management_pct in zip(return_pcts, management_pcts)
    ]
    final_ranks = scoring.competition_ranks(finals)
    final_pcts = scoring.percentiles(finals)

    for index, row in enumerate(ranked):
        row["return_percentile"] = return_pcts[index]
        row["management_percentile"] = management_pcts[index]
        row["final_score"] = finals[index]
        row["final_rank"] = final_ranks[index]
        row["grade"] = scoring.grade_for(final_pcts[index])

    confirmed_at = timezone.now()
    for row in rows:
        ContestResult.objects.update_or_create(
            contest=contest,
            participation=row["participation"],
            defaults={
                "final_rank": row.get("final_rank"),
                "return_score": row["return_score"],
                "return_percentile": row.get("return_percentile", Decimal(0)),
                "management_score": row["management_score"],
                "management_percentile": row.get("management_percentile", Decimal(0)),
                "port_hhi": row["port_hhi"],
                "pnl_hhi": row["pnl_hhi"],
                "final_score": row.get("final_score", Decimal(0)),
                "grade": row.get("grade", ""),
                "confirmed_at": confirmed_at,
            },
        )

    Account.objects.filter(contest=contest, is_frozen=False).update(is_frozen=True)
    contest.status = ContestStatus.CLOSED
    contest.save(update_fields=["status", "updated_at"])
    return len(rows)


# ─────────────────────────────────────────────────────────────────
# 9. 보조 — 영업일 · 장중 시각
# ─────────────────────────────────────────────────────────────────


def previous_business_day(day: date_type, *, limit: int = 10) -> date_type | None:
    """`day` 직전의 영업일. 달력이 그 구간을 덮지 못하면 요일로 판정한다.

    ★ 달력에 행이 없을 때 요일로 답하는 것은 `market.sessions.is_business_day` 의
      정책을 그대로 따른 것이다 — 달력이 하루 뒤처졌다고 정산이 멈추면 안 된다.
    """
    cursor = day - timedelta(days=1)
    for _ in range(limit):
        if is_business_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    return None


def intraday_slot(at: datetime | None = None) -> datetime:
    """장중 스냅샷의 시각 칸을 10분 단위로 내림한다.

    ★★ **내림이 곧 멱등성이다** — `UNIQUE(participation, at)` 아래에서 09:07 과
      09:09 에 두 번 돌아도 둘 다 09:00 칸에 떨어져 **한 행을 덮어쓴다.**
      pg_cron 이 지연돼 한 회차가 밀려도 행이 늘지 않는다.
    """
    moment = (at or now_kst()).astimezone(KST).replace(second=0, microsecond=0)
    return moment.replace(minute=(moment.minute // INTRADAY_INTERVAL_MINUTES)
                          * INTRADAY_INTERVAL_MINUTES)


def is_intraday_window(at: datetime | None = None) -> bool:
    """장중 스냅샷을 찍을 시간인가 (영업일 09:00~15:30 KST).

    ★ `market.sessions.is_market_open()` 을 쓰지 않는 이유 — 같은 판정이지만
      **잡 4 는 15:30 정각도 포함**해야 마지막 칸이 남는다. `is_market_open` 은
      체결용이라 경계 처리가 이쪽과 다를 수 있어, 여기서 명시적으로 본다.
    """
    moment = (at or now_kst()).astimezone(KST)
    if not is_business_day(moment.date()):
        return False
    return MARKET_OPEN <= moment.time() <= MARKET_CLOSE


def contests_in_progress(day: date_type) -> list[Contest]:
    """그날 정산 대상인 대회 — `ONGOING` 이고 기간 안에 있는 것.

    ★ 상태만 보고 기간을 안 보면, 운영자가 상태를 손으로 `ONGOING` 으로 바꿔 둔
      **시작 전 대회에도 스냅샷이 쌓인다.** 그러면 첫날 NAV 가 1000 이 아니게 된다.
    """
    return list(
        Contest.objects.filter(
            status=ContestStatus.ONGOING, start_date__lte=day, end_date__gte=day
        ).order_by("pk")
    )


def business_days_in(start: date_type, end: date_type) -> list[date_type]:
    """`[start, end]` 안의 영업일. 달력이 비면 주말만 걸러 답한다."""
    known = dict(
        TradingCalendar.objects.filter(date__gte=start, date__lte=end).values_list(
            "date", "is_open"
        )
    )
    days, cursor = [], start
    while cursor <= end:
        if known.get(cursor, cursor.weekday() < 5):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


# ─────────────────────────────────────────────────────────────────
# 8. 참가 신청 · 승인 · 포기 (F-02 6장 · A-02 2장)
# ─────────────────────────────────────────────────────────────────
#
# ★★ **이 절이 왜 이 파일에 있는가** ─────────────────────────────────────────
#
# 위쪽 1~7 절은 전부 **잡이 부르는 정산**이고, 이 절만 **화면이 부르는 신청**이다.
# 성격이 다른데도 같은 파일에 두는 이유는 규약이 정한 자리가 여기이기 때문이다 —
# `accounts/services.py` 머리말이 "여러 테이블을 함께 바꾸는 일은 전부 이 계층에
# 둔다" 라고 못박았고, 참가 신청이 정확히 그 일이다(`Participation` + `Account`).
#
# 뷰에 인라인하면 같은 로직이 화면·Admin·관리 커맨드마다 복제되고, 그중 하나가
# 계좌 생성을 빠뜨리면 **계좌 없는 APPROVED 참가**가 만들어진다. 그런 참가는
# `jobs._participations_with_account()` 가 `account__isnull=False` 로 걸러내므로
# 그 사람만 조용히 정산에서 빠지고 랭킹에 영원히 나오지 않는다.


class JoinRejected(Exception):
    """참가 신청이 거절됐다.

    `rule` 은 [api/A-02] 5.5 가 정한 코드다 — 화면과 API 가 같은 어휘를 쓴다.

        ENTRY_CLOSED         신청 마감(상태·마감일)
        CAPACITY_FULL        정원 초과
        ALREADY_JOINED       이미 참가
        NICKNAME_TAKEN       별칭 중복
        REQUIREMENT_NOT_MET  참가 조건 미달

    ★ `rules.RuleRejection`(주문 거부)과 **다른 예외**다. 둘을 한 클래스로 합치면
      "주문이 거부됐다" 와 "참가할 수 없다" 가 같은 except 로 잡혀 엉뚱한 안내가
      나간다. 발생 경로도 서로 만나지 않는다.
    """

    def __init__(self, rule: str, message: str, *, field: str = ""):
        super().__init__(message)
        self.rule = rule
        self.message = message
        # 어느 입력칸에 빨간 글씨를 띄울지. 폼과 무관한 사유면 빈 문자열이다.
        self.field = field

    def as_payload(self) -> dict:
        """A-03 1.4 의 에러 본문 모양. 나중에 DRF 뷰가 그대로 쓴다."""
        return {"rule": self.rule, "field": self.field, "message": self.message}


# 신청을 받아 주는 대회 상태. `DRAFT` 는 참가자에게 보이지도 않고,
# `SETTLING` 이후는 이미 성적을 매기는 중이라 새 참가자가 낄 자리가 없다.
JOINABLE_STATUSES = (ContestStatus.UPCOMING, ContestStatus.ONGOING)

# 정원을 셀 때 "자리를 차지한 것으로 보는" 상태.
#
# ★★ **`APPROVED` 만 세면 안 된다.** 운영자 승인(MANUAL) 대회에서는 승인 대기가
#   길게 쌓이는데, 그동안 `APPROVED` 만 세면 정원 100명짜리 대회에 200명이
#   신청을 마치고 운영자가 승인 단계에서 절반을 되돌려보내야 한다.
#   반면 **화면에 찍는 "참가자 N명"** 은 `APPROVED` 만 센다(F-21 · web/services.py)
#   — 실제로 뛰고 있는 사람 수라는 뜻이라 세는 기준이 다르다.
OCCUPYING_STATUSES = (ParticipationStatus.PENDING, ParticipationStatus.APPROVED)


def entry_deadline_of(contest: Contest) -> date_type:
    """이 대회의 실제 신청 마감일.

    `entry_deadline` 이 비어 있으면 **종료일까지** 받는다는 뜻이다
    (`Contest.entry_deadline` help_text — "비우면 종료까지").
    """
    return contest.entry_deadline or contest.end_date


def occupied_seats(contest: Contest) -> int:
    """정원 계산용 인원 — 승인 대기까지 포함해 센다 (`OCCUPYING_STATUSES` 주석)."""
    return Participation.objects.filter(
        contest=contest, status__in=OCCUPYING_STATUSES
    ).count()


def approved_count(contest: Contest) -> int:
    """화면에 찍는 참가자 수 — 승인된 사람만."""
    return Participation.objects.filter(
        contest=contest, status=ParticipationStatus.APPROVED
    ).count()


def is_entry_open(contest: Contest, *, today: date_type | None = None) -> bool:
    """지금 이 대회에 신청할 수 있는가 (정원·중복은 보지 않는다).

    화면이 [참가 신청] 버튼을 그릴지 정할 때 쓴다. 실제 신청은
    `check_join_eligibility()` 가 다시 전부 검사한다 — **버튼을 감추는 것은
    보안이 아니다.** 주소를 직접 치는 경로가 항상 남는다.
    """
    today = today or today_kst()
    if contest.status not in JOINABLE_STATUSES:
        return False
    return today <= entry_deadline_of(contest)


def check_entry_requirement(contest: Contest, member) -> None:
    """`Contest.entry_requirement` 게이트 (예: `{"min_learning_points": 30}`).

    Raises:
        JoinRejected: `REQUIREMENT_NOT_MET`.

    ★ 비어 있으면(기본값 `{}`) 아무 제한이 없다. 1차 대회의 기본이 그렇다.
    ★ 모르는 키는 **조용히 무시**한다. Admin 이 원시 JSON 을 편집하는 구조라
      오타가 들어올 수 있는데, 오타 하나로 아무도 참가하지 못하게 되는 편보다
      제한이 안 걸리는 편이 낫다(운영자가 화면에서 인원을 보고 알아챈다).
    """
    requirement = contest.entry_requirement or {}
    minimum = requirement.get("min_learning_points")
    if not minimum:
        return

    # 지연 import — `contests` 는 `learning` 을 모르는 것이 기본이고, 이 게이트
    # 하나 때문에 모듈 최상단에 의존을 만들지 않는다 (web/services.py 와 같은 방식).
    from learning.models import LearningProgress      # noqa: PLC0415

    earned = (
        LearningProgress.objects.filter(member=member).aggregate(
            total=Sum("progress_points")
        )["total"]
        or 0
    )
    if earned < int(minimum):
        raise JoinRejected(
            "REQUIREMENT_NOT_MET",
            f"학습 포인트가 {minimum}점 이상이어야 참가할 수 있습니다. 현재 {earned}점입니다.",
        )


def check_join_eligibility(
    contest: Contest, member, *, today: date_type | None = None
) -> None:
    """참가 신청 자격을 전부 검사한다 (F-02 6장의 검증 순서 그대로).

    Raises:
        JoinRejected: 사유별 `rule` 코드와 **한국어 완성 문장**을 담는다.

    ★ 검사 순서가 곧 사용자에게 보이는 우선순위다. "정원이 찼습니다" 보다
      "이미 참가 중입니다" 가 먼저 나와야 참가자가 헷갈리지 않는다.
    """
    today = today or today_kst()

    if contest.status not in JOINABLE_STATUSES:
        raise JoinRejected(
            "ENTRY_CLOSED",
            f"지금은 신청을 받지 않습니다 (대회 상태: {contest.get_status_display()}).",
        )

    existing = Participation.objects.filter(contest=contest, member=member).first()
    if existing is not None:
        # ★ `UNIQUE(contest, member)` 제약이 있어 **행은 대회당 하나뿐**이다.
        #   포기·거절 뒤 재신청은 새 행을 만들 수 없으므로 운영자가 Admin 에서
        #   상태를 되돌려야 한다. 그 사실을 문구로 알려 준다.
        if existing.status == ParticipationStatus.WITHDRAWN:
            raise JoinRejected(
                "ALREADY_JOINED", "포기한 대회입니다. 다시 참가하려면 운영자에게 문의하세요."
            )
        if existing.status == ParticipationStatus.REJECTED:
            raise JoinRejected(
                "ALREADY_JOINED", "신청이 거절된 대회입니다. 운영자에게 문의하세요."
            )
        raise JoinRejected("ALREADY_JOINED", "이미 참가 신청한 대회입니다.")

    if today > entry_deadline_of(contest):
        raise JoinRejected(
            "ENTRY_CLOSED",
            f"신청이 마감됐습니다 (마감일 {entry_deadline_of(contest):%Y-%m-%d}).",
        )

    if contest.capacity and occupied_seats(contest) >= contest.capacity:
        raise JoinRejected(
            "CAPACITY_FULL", f"정원({contest.capacity}명)이 모두 찼습니다."
        )

    check_entry_requirement(contest, member)


def default_nickname(contest: Contest, member) -> str:
    """별칭 미입력 시 자동 부여 — `참가자{번호}` (F-02 5.2).

    ★ 번호는 **현재 인원 + 1** 로 시작하되, 이미 쓰이고 있으면 하나씩 올린다.
      포기자가 있으면 인원과 번호가 어긋나는데, `UNIQUE(contest, nickname)` 에
      걸려 신청이 통째로 실패하는 것보다 번호가 건너뛰는 편이 낫다.
    """
    taken = set(
        Participation.objects.filter(contest=contest).values_list("nickname", flat=True)
    )
    seq = Participation.objects.filter(contest=contest).count() + 1
    while f"참가자{seq}" in taken:
        seq += 1
    return f"참가자{seq}"


def create_contest_account(contest: Contest, member) -> Account:
    """대회 계좌 하나 (F-02 6장 · E-01 3장).

    ★★ **`mode` 와 `contest` 는 반드시 함께 채운다.** `accounts.Account` 의 CHECK
      제약 `account_ck_mode_contest_match` 가 `mode=CONTEST ⇔ contest IS NOT NULL`
      을 양방향으로 강제한다. 한쪽만 채우면 `IntegrityError` 다.

    ★★ **시작 자본은 `contest.initial_capital` 이다.** `Account.initial_capital` 의
      기본값은 연습 계좌용 1억이라, 명시하지 않으면 대회 시작 자본이 1억이 아닐 때
      **조용히 틀린 수익률 기준선**이 박힌다 — 그리고 그 값이 NAV 계산의 분모다.
    """
    return Account.objects.create(
        member=member,
        contest=contest,
        mode=AccountMode.CONTEST,
        cash=contest.initial_capital,
        initial_capital=contest.initial_capital,
    )


@transaction.atomic
def join_contest(*, contest: Contest, member, nickname: str = "") -> Participation:
    """참가 신청 (F-02 6장).

    자동 승인 대회면 `APPROVED` + **대회 계좌 생성**까지 한 트랜잭션에서 끝낸다.
    운영자 승인 대회면 `PENDING` 으로 남고 계좌는 아직 없다.

    Returns:
        만들어진 `Participation`.

    Raises:
        JoinRejected: 자격 미달 · 별칭 중복.

    ★★ **왜 트랜잭션이 필수인가** — 계좌를 만든 뒤 참가 행 생성이 실패하면
      주인 없는 대회 계좌가 남고, `UNIQUE(member, contest)` 때문에 그 사람은
      **다시는 이 대회에 참가할 수 없게** 된다. 되돌릴 방법이 Admin 수동 삭제뿐이다.

    Django 관점 — FastAPI + SQLAlchemy 였다면 `async with session.begin():` 으로
    감쌌을 자리다. `@transaction.atomic` 은 데코레이터 하나로 같은 일을 하고,
    **예외가 밖으로 나가면 자동으로 롤백**한다.
    """
    check_join_eligibility(contest, member)

    nickname = (nickname or "").strip() or default_nickname(contest, member)
    if Participation.objects.filter(contest=contest, nickname=nickname).exists():
        raise JoinRejected(
            "NICKNAME_TAKEN", "이미 사용 중인 별칭입니다.", field="nickname"
        )

    auto = contest.approval_mode == ApprovalMode.AUTO
    account = create_contest_account(contest, member) if auto else None

    try:
        return Participation.objects.create(
            contest=contest,
            member=member,
            account=account,
            nickname=nickname,
            status=(
                ParticipationStatus.APPROVED if auto else ParticipationStatus.PENDING
            ),
            approved_at=now_kst() if auto else None,
        )
    except IntegrityError as exc:
        # ★ 위에서 이미 검사했지만 **두 사람이 같은 순간에 신청하면** 검사와 INSERT
        #   사이를 DB 제약만이 막는다(경쟁 상태). 500 대신 사용자가 읽을 수 있는
        #   문장으로 바꾼다. 어느 제약인지는 메시지 문자열로 구분한다.
        if "nickname" in str(exc):
            raise JoinRejected(
                "NICKNAME_TAKEN", "이미 사용 중인 별칭입니다.", field="nickname"
            ) from exc
        raise JoinRejected("ALREADY_JOINED", "이미 참가 신청한 대회입니다.") from exc


@transaction.atomic
def approve_participation(participation: Participation) -> Participation:
    """운영자 승인 — `PENDING` → `APPROVED` + 대회 계좌 생성.

    ★ **멱등하다.** 이미 승인된 참가를 다시 넘겨도 계좌를 두 번 만들지 않는다
      (`account_uniq_contest` 제약에 걸려 터지는 대신 그냥 통과한다).
      Admin 에서 실수로 두 번 눌러도 안전해야 한다.
    """
    if participation.account_id is None:
        participation.account = create_contest_account(
            participation.contest, participation.member
        )
    if participation.status != ParticipationStatus.APPROVED:
        participation.status = ParticipationStatus.APPROVED
        participation.approved_at = now_kst()
    participation.save(
        update_fields=["account", "status", "approved_at", "updated_at"]
    )
    return participation


@transaction.atomic
def withdraw_participation(participation: Participation) -> Participation:
    """참가 포기 — `WITHDRAWN` + 랭킹 제외 (F-02 5.4).

    ★★ **계좌도 주문 이력도 지우지 않는다.** 지우면 회고가 불가능하고, 판정이
      잘못됐을 때 되돌릴 수 없다. `is_ranked=False` 로만 만든다 — 정산은 계속
      스냅샷을 남기고(`SETTLED_STATUSES`), 랭킹 화면은 순위 칸을 `-` 로 그린다.
    """
    participation.status = ParticipationStatus.WITHDRAWN
    participation.is_ranked = False
    participation.save(update_fields=["status", "is_ranked", "updated_at"])
    return participation


# ─────────────────────────────────────────────────────────────────
# 9. 포트폴리오 공개 범위 (F-05 3.3 · A-02 4.2)
# ─────────────────────────────────────────────────────────────────
#
# ★★ **`Contest.visibility` 와 헷갈리지 말 것** — 이름이 비슷한 필드가 둘이다.
#
#     visibility            PUBLIC / LINK / PRIVATE   대회가 **목록에 뜨는가**
#     portfolio_visibility  ALL / TOP_N / SELF_ONLY   **남의 포트폴리오**를 보는가
#
#   한쪽으로 필터를 걸면 비공개 대회가 목록에 뜨거나, 공개 대회의 포트폴리오가
#   통째로 잠긴다.
#
# ★★ **이 설정은 랭킹 표 자체를 가리지 않는다.** 순위·별칭·기준가·편입비·종목수는
#   `SELF_ONLY` 라도 전원 보인다. 가리는 것은 **상세(보유 종목·수익 종목·거래
#   이력)** 뿐이다. 목록을 통째로 잠그면 "실격자도 목록에는 표시한다"(F-05 3.2)와
#   충돌한다.


def can_view_portfolio(
    *,
    contest: Contest,
    viewer,
    participation: Participation,
    rank: int | None,
) -> bool:
    """`viewer` 가 `participation` 의 포트폴리오 상세를 볼 수 있는가.

    Args:
        rank: 대상자의 **최신 순위**. 없으면(정산 전·실격·포기) `None`.

    ★★ **판정을 가장 관대한 쪽으로 정했다** ─────────────────────────────────

    v2.0 의 존재 이유 중 하나가 벤치마크의 결함 ③ "50등 이후 참가자는 포트폴리오
    조회가 불가능하다" 를 없애는 것이다(F-05 3.3). 같은 동아리인데 서로 배울 수가
    없다면 대회를 여는 의미가 절반은 사라진다. 그래서 기본값이 `ALL` 이고,
    `TOP_N` 을 골랐을 때에도 아래 두 경우는 **막지 않는다.**

        · 본인 것          — 어떤 설정에서도 자기 포트폴리오는 본다
        · `rank` 가 없는 사람 — 실격·포기자와 정산 전 참가자. 숨기면 회고 자료가
                              통째로 사라지고, "내가 왜 안 보이지" 문의가 온다

    운영자(`is_staff`)도 통과시킨다. 부정행위 신고를 확인하려면 봐야 한다.

    설정 자체를 무시하지 않는 이유 — Admin 에 `TOP_N`·`SELF_ONLY` 선택지가 남아
    있는데 골라도 아무 일이 없으면, 나중에 외부 공개 대회를 열 때 **전원의
    포트폴리오가 조용히 새어 나간다.** "지켜지지 않는 설정" 은 없는 설정보다 나쁘다.
    """
    if viewer is not None and getattr(viewer, "is_authenticated", False):
        if participation.member_id == viewer.pk:
            return True
        if getattr(viewer, "is_staff", False):
            return True

    mode = contest.portfolio_visibility
    if mode == PortfolioVisibility.ALL:
        return True
    if mode == PortfolioVisibility.SELF_ONLY:
        return False
    # TOP_N
    if rank is None:
        return True
    return rank <= contest.portfolio_visible_top_n


def portfolio_block_reason(contest: Contest) -> str:
    """왜 못 보는지 화면에 적을 문장.

    ★ 규약 원칙 2 — "화면에 보이지 않는 사실은 없는 것과 같다". 버튼만 회색으로
      만들어 두면 참가자는 고장인 줄 안다.
    """
    if contest.portfolio_visibility == PortfolioVisibility.SELF_ONLY:
        return "이 대회는 본인 포트폴리오만 볼 수 있도록 설정돼 있습니다."
    if contest.portfolio_visibility == PortfolioVisibility.TOP_N:
        return (
            f"이 대회는 상위 {contest.portfolio_visible_top_n}명의 포트폴리오만 "
            "공개하도록 설정돼 있습니다."
        )
    return ""


def latest_ranking_date(contest: Contest) -> date_type | None:
    """이 대회 랭킹이 마지막으로 정산된 영업일.

    ★ 랭킹 화면이 "언제 기준 숫자인가" 를 표시하는 데 쓴다. `ContestRanking` 은
      영업일 15:40 정산(잡 7)에만 갱신되므로, 장중에 60초 폴링을 걸어도 값은
      **전 영업일 15:40 것**이다. 그 사실을 화면에 적지 않으면 참가자는 방금 낸
      주문이 순위에 반영되지 않는 것을 버그로 읽는다.
    """
    row = (
        ContestRanking.objects.filter(contest=contest)
        .order_by("-date")
        .values_list("date", flat=True)
        .first()
    )
    return row


def ranking_rows(contest: Contest, *, date: date_type | None = None) -> list[ContestRanking]:
    """랭킹 표 한 벌 — `ContestRanking` 을 **그대로** 읽는다 (F-05 7장).

    조회 시점에 수익률을 다시 계산하거나 순위를 매기지 않는다. 그러면 홈 위젯과
    랭킹 화면이 **서로 다른 숫자**를 말한다.

    정렬은 `rank` 오름차순이고, 인덱스가 그 하나에 맞춰져 있다
    (`ranking_idx_contest_date` = `(contest, date, rank)`).

    ★ 실격·포기자는 `rank=None` 이라 정렬 끝으로 보낸다. PostgreSQL 의 기본은
      `NULLS LAST`(오름차순)라 `order_by("rank")` 만으로도 뒤로 가지만,
      **의도를 코드에 적어 둔다** — DB 를 바꿨을 때 조용히 순서가 뒤집히는 것을
      막는다.
    """
    date = date or latest_ranking_date(contest)
    if date is None:
        return []
    return list(
        ContestRanking.objects.filter(contest=contest, date=date)
        .select_related("participation", "participation__member")
        .order_by(F("rank").asc(nulls_last=True), "participation_id")
    )


# ─────────────────────────────────────────────────────────────────
# 10. 화면 조립 (U-02 대회 화면군)
# ─────────────────────────────────────────────────────────────────
#
# ★ `web/services.py` 와 같은 역할이다 — 뷰와 모델 사이에서 "화면 한 벌" 을
#   만든다. 대회 화면이 쓰는 것만 여기 두는 이유는, 홈 위젯(`web`)과 대회 목록이
#   **세는 기준이 달라서** 한 함수로 합칠 수 없기 때문이다(정원 계산 주석 참조).
#
# 절대 규칙 둘은 `web/services.py` 와 같다.
#   ① 외부 API 를 부르지 않는다 — 전부 우리 DB 에서 읽는다
#   ② 순위·수익률을 조회 시점에 계산하지 않는다 (F-05 7장)

# 목록 탭 — 명세(U-02 4.1)가 정한 3종. 값은 URL 쿼리(`?tab=`)로도 쓴다.
#
# ★ `CANCELLED` 는 어느 탭에도 넣지 않는다. 취소된 대회는 참가자가 할 일이
#   아무것도 없어서, 목록에 남으면 "왜 못 들어가지" 만 만든다.
LIST_TABS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ongoing", "진행 중", (ContestStatus.ONGOING,)),
    ("upcoming", "모집 중", (ContestStatus.UPCOMING,)),
    # 정산 중은 참가자 입장에서 "끝났고 결과를 기다리는" 상태라 종료 탭에 함께 둔다.
    ("closed", "종료", (ContestStatus.SETTLING, ContestStatus.CLOSED)),
)
DEFAULT_TAB = "ongoing"


@dataclass
class ContestCard:
    """대회 목록 카드 하나 (U-02 4.1 와이어프레임)."""

    contest: Contest
    participant_count: int
    d_day: int
    d_day_label: str
    entry_open: bool
    participation: Participation | None
    ranking: ContestRanking | None

    @property
    def is_recruiting(self) -> bool:
        return self.contest.status == ContestStatus.UPCOMING

    @property
    def is_joined(self) -> bool:
        """내가 참가 중인가 — 승인 대기도 '참가 중' 으로 본다.

        ★ `PENDING` 을 빼면 승인을 기다리는 사람에게 [참가 신청] 버튼이 계속
          보이고, 눌러도 `ALREADY_JOINED` 로 튕긴다.
        """
        return self.participation is not None and self.participation.status in (
            ParticipationStatus.PENDING,
            ParticipationStatus.APPROVED,
        )

    @property
    def is_pending(self) -> bool:
        """운영자 승인을 기다리는 중인가.

        ★ 템플릿에서 `{% if card.participation.status == "PENDING" %}` 처럼 값
          문자열을 직접 비교하지 않기 위해 프로퍼티로 노출한다. 열거형 값이
          바뀌면 파이썬 쪽은 에러로 드러나지만 템플릿 문자열은 **조용히 거짓**이 된다.
        """
        return (
            self.participation is not None
            and self.participation.status == ParticipationStatus.PENDING
        )

    @property
    def rank_delta(self) -> int | None:
        """전일 대비 순위 변동. 양수면 올랐다(등수 숫자가 줄었다).

        ★ `rank - prev_rank` 가 아니다. 순위는 **작을수록 좋다.** 반대로 쓰면
          화살표가 거꾸로 뜬다 (홈 위젯 `web/services.py` 와 같은 규약).
        """
        if not self.ranking or not self.ranking.prev_rank or not self.ranking.rank:
            return None
        return self.ranking.prev_rank - self.ranking.rank


def contest_cards(*, member=None, tab: str = DEFAULT_TAB) -> list[ContestCard]:
    """대회 목록 한 벌.

    Args:
        member: 로그인 사용자. `None` 이면 참가 배지·순위가 비어 있다.
        tab: `LIST_TABS` 의 첫 원소 값. 모르는 값이면 기본 탭으로 되돌린다.

    ★ `visibility=PUBLIC` 만 목록에 넣는다. `LINK`(링크 있는 사람만)·`PRIVATE`
      대회가 목록에 뜨면 비공개의 의미가 없다. 다만 **상세 화면은 링크로 들어온
      사람에게 열어 준다** (`views._visible_contest` 참조) — 그것이 `LINK` 다.
    """
    statuses = dict((key, value) for key, _, value in LIST_TABS).get(tab)
    if statuses is None:
        statuses = dict((key, value) for key, _, value in LIST_TABS)[DEFAULT_TAB]

    today = today_kst()
    contests = list(
        Contest.objects.filter(status__in=statuses, visibility=Visibility.PUBLIC)
        # ★ 참가자 수를 SQL 의 COUNT 로 센다. 대회마다 `participations.count()` 를
        #   부르면 대회 수만큼 쿼리가 난다(N+1). `filter=` 는
        #   `COUNT(*) FILTER (WHERE status='APPROVED')` 로 번역된다.
        .annotate(
            participant_count=Count(
                "participations",
                filter=Q(participations__status=ParticipationStatus.APPROVED),
            )
        )
        # 진행 중은 끝이 가까운 순, 모집 중은 곧 시작하는 순 — 둘 다 "임박한 것부터".
        .order_by("start_date" if tab == "upcoming" else "-start_date")
    )
    if not contests:
        return []

    # 내 참가 + 내 최신 순위를 각각 **한 번의 쿼리**로 읽는다.
    mine: dict[int, Participation] = {}
    latest: dict[int, ContestRanking] = {}
    if member is not None and getattr(member, "is_authenticated", False):
        mine = {
            row.contest_id: row
            for row in Participation.objects.filter(member=member, contest__in=contests)
        }
        if mine:
            for row in ContestRanking.objects.filter(
                participation_id__in=list(p.pk for p in mine.values())
            ).order_by("participation_id", "-date"):
                # 참가별 가장 최근 행 하나만 취한다.
                latest.setdefault(row.participation_id, row)

    cards = []
    for contest in contests:
        participation = mine.get(contest.pk)
        if contest.status == ContestStatus.UPCOMING:
            # 모집 중에는 "언제 시작하는가" 가 궁금하다.
            d_day, label = (contest.start_date - today).days, "시작"
        else:
            d_day, label = (contest.end_date - today).days, "종료"
        cards.append(
            ContestCard(
                contest=contest,
                participant_count=contest.participant_count,
                d_day=d_day,
                d_day_label=label,
                entry_open=is_entry_open(contest, today=today),
                participation=participation,
                ranking=latest.get(participation.pk) if participation else None,
            )
        )
    return cards


def rule_summary(contest: Contest) -> list[tuple[str, str]]:
    """대회 상세의 **규칙 요약 표** (U-02 4.2).

    `Contest.rule_set` 은 Admin 이 원시 JSON 으로 편집하는 자리라 **키가 없을 수
    있다.** 템플릿에서 `{{ contest.rule_set.position_limit_pct }}` 로 직접 꺼내면
    없는 키에서 조용히 빈칸이 되고, 참가자는 한도가 없는 줄 안다.
    여기서 기본값과 함께 문자열로 정리해 넘긴다.
    """
    rules = contest.rule_set or {}

    def pct(key: str, fallback) -> str:
        value = rules.get(key, fallback)
        try:
            return f"{Decimal(str(value)).normalize():f}%"
        except (ArithmeticError, TypeError, ValueError):
            return str(value)

    exceptions = rules.get("position_limit_exceptions") or {}
    exception_note = ""
    if isinstance(exceptions, dict) and exceptions:
        # 예: "삼성전자 40% · SK하이닉스 30%" — 종목명을 붙이려면 마스터 조회가
        # 필요하므로 코드만 적는다. 상세 화면 한 줄에 종목명까지 넣을 자리도 없다.
        exception_note = " (예외: " + " · ".join(
            f"{symbol} {value}%" for symbol, value in exceptions.items()
        ) + ")"

    return [
        ("시작 자본", f"{contest.initial_capital:,}원"),
        ("종목별 한도", pct("position_limit_pct", 15) + exception_note),
        (
            "섹터별 한도",
            f"시장 섹터 비중의 {rules.get('sector_limit_multiplier', 2)}배 "
            f"또는 {pct('sector_limit_floor_pct', 10)} 중 큰 값",
        ),
        ("소형주 합계", f"시총 1조 미만 종목 합계 {pct('small_cap_total_limit_pct', 30)} 이하"),
        (
            "주간 회전율",
            f"매주 {pct('weekly_turnover_min_pct', 5)} 이상 유지 "
            f"(위반 {rules.get('weekly_turnover_violation_limit', 3)}회까지 허용)",
        ),
        ("거래 비용", f"수수료 {contest.fee_bp}bp · 매도세 {contest.tax_bp}bp"),
        ("거래 시간", "평일 09:00 ~ 15:30 (KST)"),
    ]


@dataclass
class RankingRow:
    """랭킹 표 한 줄 — 표시에 필요한 판단까지 **미리 끝낸** 상태로 넘긴다.

    ★ 템플릿에서 `{% if row.rank <= contest.portfolio_visible_top_n %}` 같은
      비교를 하지 않는다. Django 템플릿은 비교 연산이 제한적이고, 무엇보다
      **공개 범위 판정이 화면 곳곳에 흩어지면** 한 곳을 고칠 때 다른 곳이 남는다.
    """

    ranking: ContestRanking
    participation: Participation
    is_me: bool
    can_view_portfolio: bool

    @property
    def rank_delta(self) -> int | None:
        """`prev_rank - rank`. 양수면 올랐다 (등수 숫자가 줄었다)."""
        if not self.ranking.prev_rank or not self.ranking.rank:
            return None
        return self.ranking.prev_rank - self.ranking.rank


def ranking_table(contest: Contest, viewer=None, *, date: date_type | None = None) -> list[RankingRow]:
    """랭킹 표 한 벌 + 각 줄의 포트폴리오 열람 가능 여부.

    ★ 실격·포기자(`rank=None`)도 **목록에 남긴다.** 순위 칸만 `-` 로 그린다.
      아예 숨기면 "내가 왜 없지?" 라는 문의가 온다 (F-05 3.2).
    """
    viewer_id = viewer.pk if (viewer is not None and getattr(viewer, "is_authenticated", False)) else None
    rows = []
    for ranking in ranking_rows(contest, date=date):
        participation = ranking.participation
        rows.append(
            RankingRow(
                ranking=ranking,
                participation=participation,
                is_me=participation.member_id == viewer_id,
                can_view_portfolio=can_view_portfolio(
                    contest=contest,
                    viewer=viewer,
                    participation=participation,
                    rank=ranking.rank,
                ),
            )
        )
    return rows


def participation_notice(participation: Participation | None) -> dict | None:
    """내 참가 상태를 화면에 그대로 찍을 **한 덩어리**로 정리한다.

    Returns:
        `{"tone", "title", "detail"}` 또는 참가하지 않았으면 `None`.
        `tone` 은 색을 고르는 데 쓰는 우리 어휘다 — `brand`·`warn`·`danger`·`muted`.

    ★★ **왜 템플릿에서 상태를 비교하지 않는가** ─────────────────────────────

    `{% if participation.status == "APPROVED" %}` 처럼 값 문자열을 템플릿에 적으면,
    나중에 열거형 값이 바뀌었을 때 파이썬 쪽은 에러로 드러나지만 **템플릿은
    조용히 거짓**이 된다. 화면에서 참가 상태가 통째로 사라지는데 아무도 모른다.
    모양이 제각각인 것을 한 모양으로 정리하는 것은 서비스 계층의 일이다
    (`web/services.py` 의 `violation_lines` 와 같은 이유).
    """
    if participation is None:
        return None

    status = participation.status
    if status == ParticipationStatus.APPROVED:
        return {
            "tone": "brand",
            "title": "참가 중",
            "detail": f"별칭 {participation.nickname}",
        }
    if status == ParticipationStatus.PENDING:
        return {
            "tone": "warn",
            "title": "승인 대기 중",
            "detail": "운영자가 승인하면 대회 계좌가 만들어집니다.",
        }
    if status == ParticipationStatus.DISQUALIFIED:
        return {
            "tone": "danger",
            "title": "실격",
            # ★ 실격돼도 계좌·이력은 남는다는 사실을 함께 적는다 (F-02 5.4).
            #   이 안내가 없으면 "내 기록이 지워졌나" 라는 문의가 온다.
            "detail": (
                f"{participation.disqualified_reason or '운영자 판단'} — "
                "계좌와 거래 이력은 그대로 남습니다."
            ),
        }
    if status == ParticipationStatus.WITHDRAWN:
        return {
            "tone": "muted",
            "title": "참가 포기",
            "detail": "순위에서만 빠지고 기록은 남습니다.",
        }
    return {
        "tone": "muted",
        "title": participation.get_status_display(),
        "detail": "",
    }


@dataclass
class MyContestRow:
    """'내 대회' 표 한 줄 (U-04 8장).

    ★ 진행 중이면 `ranking`(오늘 순위)을, 종료됐으면 `result`(최종 확정)를 본다.
      **둘을 섞으면 안 된다** — 진행 중 순위는 단순 수익률이고, 최종 순위는
      관리 점수까지 반영한 값이다(F-05 3.1). 같은 열에 나란히 놓고 "순위가
      바뀌었다" 는 오해를 만들지 않도록, 화면도 확정 여부를 함께 적는다.
    """

    participation: Participation
    contest: Contest
    ranking: ContestRanking | None
    result: ContestResult | None

    @property
    def is_closed(self) -> bool:
        return self.contest.status == ContestStatus.CLOSED

    @property
    def rank(self) -> int | None:
        """표에 찍을 순위 — 종료됐으면 최종 순위, 아니면 오늘 순위."""
        if self.result is not None and self.result.final_rank:
            return self.result.final_rank
        return self.ranking.rank if self.ranking else None

    @property
    def return_pct(self) -> Decimal | None:
        if self.ranking is not None:
            return self.ranking.cumulative_return_pct
        if self.result is not None:
            return self.result.return_score
        return None

    @property
    def grade(self) -> str:
        """등급은 **정산이 확정된 뒤에만** 존재한다. 그 전에는 빈 문자열."""
        return self.result.grade if self.result else ""


def my_contest_rows(member) -> list[MyContestRow]:
    """참가 이력 전체 — 진행 중과 종료를 한 표에 (U-04 8장).

    ★ **종료된 대회도 계속 보여준다.** 회고 자료가 된다. 실격·포기한 참가도
      숨기지 않는다 — 무슨 일이 있었는지 본인은 볼 수 있어야 한다.
    """
    participations = list(
        Participation.objects.filter(member=member)
        .select_related("contest")
        .order_by("-contest__start_date")
    )
    if not participations:
        return []

    ids = [participation.pk for participation in participations]

    latest: dict[int, ContestRanking] = {}
    for row in ContestRanking.objects.filter(participation_id__in=ids).order_by(
        "participation_id", "-date"
    ):
        latest.setdefault(row.participation_id, row)

    results = {
        row.participation_id: row
        for row in ContestResult.objects.filter(participation_id__in=ids)
    }

    return [
        MyContestRow(
            participation=participation,
            contest=participation.contest,
            ranking=latest.get(participation.pk),
            result=results.get(participation.pk),
        )
        for participation in participations
    ]


# 데이터 출처 고지 (U-02 4.2 · U-01 6장) — **문서가 그대로 넣으라고 못박은 문구다.**
# 섹터가 GICS 가 아니라는 사실을 빼면, 참가자가 다른 서비스의 섹터 비중과 비교하다
# 우리 숫자가 틀렸다고 생각한다.
DATA_SOURCES: tuple[tuple[str, str], ...] = (
    ("호가", "한국투자증권 KIS Developers"),
    ("종목정보", "pykrx / KRX"),
    ("섹터", "KRX 업종분류 (GICS 아님)"),
)


__all__ = [
    "DATA_SOURCES",
    "DEFAULT_TAB",
    "ContestCard",
    "HoldingView",
    "JOINABLE_STATUSES",
    "JoinRejected",
    "LIST_TABS",
    "MyContestRow",
    "OCCUPYING_STATUSES",
    "RankingRow",
    "SETTLED_STATUSES",
    "Valuation",
    "contest_cards",
    "my_contest_rows",
    "participation_notice",
    "ranking_table",
    "rule_summary",
    "apply_turnover_disqualification",
    "approve_participation",
    "approved_count",
    "business_days_in",
    "can_view_portfolio",
    "check_entry_requirement",
    "check_join_eligibility",
    "closing_prices",
    "compute_weekly_turnover",
    "contests_in_progress",
    "create_contest_account",
    "daily_trade_amounts",
    "day_bounds",
    "default_nickname",
    "entry_deadline_of",
    "evaluate_account",
    "evaluate_violations",
    "finalize_contest",
    "freeze_universe",
    "intraday_slot",
    "is_entry_open",
    "is_intraday_window",
    "join_contest",
    "latest_ranking_date",
    "management_metrics",
    "occupied_seats",
    "portfolio_block_reason",
    "positions_by_account",
    "previous_business_day",
    "projected_violation_total",
    "ranking_rows",
    "rebuild_rankings",
    "refresh_weekly_turnover",
    "renumber_violations",
    "sector_limit_map",
    "turnover_violation_limit",
    "small_cap_symbols",
    "symbol_profits",
    "universe_map",
    "withdraw_participation",
    "write_daily_snapshot",
]
