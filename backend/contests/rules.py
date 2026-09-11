"""대회 규칙 엔진 — **사전 차단** (F-04 2장 · 3장 · 4.1).

주문이 접수되기 전에 통과해야 하는 검증이다. 사후 판정(주간 회전율·관리 점수)은
정산 잡의 몫이라 여기 없다 (F-04 5·6장 → F-05).

    사전 차단은 주문을 거부하고, 사후 판정은 주문을 막지 않는다.

★★ **이 모듈은 `trading` 을 import 하지 않는다** ─────────────────────────────

규약 1.1 의 의존 방향은 `market ◀ trading ◀ contests` 다. 규칙 판정이
`trading.Position` 을 직접 읽으면 `trading → contests → trading` 순환이 생긴다.

그래서 **판정에 필요한 것은 전부 인자로 받는다.** 포지션을 조회하는 것은 호출자
(`trading.services`)의 몫이고, 이 모듈은 넘어온 숫자로 규칙만 본다.

    trading.services  ──▶  contests.rules.check_order(portfolio=…, …)
                           (여기서 trading 을 되짚지 않는다)

부수 효과로 **테스트가 쉬워진다** — DB 에 포지션을 만들지 않고 `Portfolio` 를 손으로
지어 규칙만 시험할 수 있다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 에서는 이런 검증을 Pydantic `validator` 나 의존성(`Depends`)에 넣곤 했다.
Django 에는 `Form`·`Serializer` 의 `clean_*` / `validate_*` 가 그 자리인데,
**여기서는 쓰지 않는다.** 이 규칙들은 입력값의 형식이 아니라 **계좌의 현재 상태와
대회 규칙의 관계**를 본다. 폼에 넣으면 폼이 DB 를 훑게 되고, 같은 판정을 API·잡·
Admin 에서 다시 쓸 수 없다. 평범한 함수가 맞다.
"""

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from contests.models import (
    Contest,
    ContestSectorWeight,
    ContestStatus,
    ContestUniverse,
    Participation,
    ParticipationStatus,
)
from core.constants import OrderSide
from core.time import today_kst
from market.models import StockMaster, StockType, TradingCalendar
from market.sessions import SessionState, session_state

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# 1. 판정 실패를 나르는 예외
# ─────────────────────────────────────────────────────────────────


class RuleRejection(Exception):
    """규칙 위반 — 주문을 거부한다.

    A-03 1.4 의 응답 형식을 그대로 담는다. 뷰는 이 예외를 잡아 JSON 으로 옮기기만 한다.

    Attributes:
        rule: `POSITION_LIMIT` 등. A-03 1.5 의 값 목록.
        field: 화면의 **어느 입력칸**에 빨간 글씨를 띄울지.
        message: 참가자가 읽을 한국어 문장.
        detail: 화면이 보조 정보로 쓰는 dict. 한도 규칙은 `max_additional_krw` 를 담는다.

    ★★ **`detail.max_additional_krw` 가 이 예외의 핵심이다** ────────────────

    "안 됩니다"만 말하면 참가자는 비중을 5% → 4% → 3% 로 바꿔가며 계속 시도한다.
    **"여기까지는 됩니다"를 함께 준다.** 벤치마크 결함 ②(주문 창에 정보가 없음)에
    대한 대응이기도 하다 (F-04 4.2).
    """

    def __init__(self, rule: str, field: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.rule = rule
        self.field = field
        self.message = message
        self.detail = detail or {}

    def as_payload(self) -> dict:
        """A-03 1.4 응답 본문."""
        return {
            "ok": False,
            "rule": self.rule,
            "field": self.field,
            "message": self.message,
            "detail": self.detail,
        }


class MarketClosed(Exception):
    """장이 닫혀 있다 — **거부가 아니라 `PENDING_OPEN` 접수** 신호다 (F-03 8장).

    `RuleRejection` 을 상속하지 않는 것이 의도다. 상속하면 호출부가
    `except RuleRejection` 한 줄로 뭉뚱그려 **장외 주문까지 거부**하게 된다.
    타입이 다르면 그 실수를 할 수 없다.
    """

    def __init__(self, state: str):
        super().__init__(state)
        self.state = state


# ─────────────────────────────────────────────────────────────────
# 2. 판정에 넘기는 입력
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Holding:
    """보유 한 종목의 **평가 기준** 값. `trading.Position` 의 사본이 아니다.

    규칙이 보는 것은 수량이 아니라 **평가액**이다 — 비중 계산의 분자이기 때문이다.
    """

    symbol: str
    value: int              # 현재가 기준 평가액(원)


@dataclass(frozen=True)
class Portfolio:
    """규칙 판정 시점의 계좌 상태.

    ★ **`total_asset` 을 호출자가 계산해 넘긴다.** 여기서 다시 구하면 체결 직전에
      읽은 시세와 판정이 쓰는 시세가 달라져, **주문 창에 보인 한도와 실제 판정이
      어긋난다.** 한 번 읽은 값을 끝까지 쓰는 것이 맞다.
    """

    cash: int
    holdings: list[Holding] = field(default_factory=list)

    @property
    def position_value(self) -> int:
        return sum(item.value for item in self.holdings)

    @property
    def total_asset(self) -> int:
        """순자산 = 현금 + 보유 평가액. 모든 비중의 분모다 (F-03 3.1)."""
        return self.cash + self.position_value

    def value_of(self, symbol: str) -> int:
        return sum(item.value for item in self.holdings if item.symbol == symbol)


# ─────────────────────────────────────────────────────────────────
# 3. 규칙 스냅샷 — 대회 하나를 판정하는 데 필요한 모든 값
# ─────────────────────────────────────────────────────────────────


@dataclass
class RuleContext:
    """대회 규칙을 **한 번에 읽어 둔 것**.

    ★ 주문 한 건을 판정하는 데 `Contest.rule_set` · `ContestUniverse` ·
      `ContestSectorWeight` · `StockMaster` 를 봐야 한다. 매 규칙마다 따로 조회하면
      쿼리가 흩어지고, 무엇보다 **판정 도중에 값이 바뀔 수 있다.**
      입구에서 한 번 모아 두고 그것만 본다.
    """

    contest: Contest
    rules: dict
    sector_code: str
    sector_limit_pct: Decimal | None
    stock: StockMaster | None

    @classmethod
    def load(cls, contest: Contest, symbol: str) -> "RuleContext":
        rules = contest.rule_set or {}

        # ★ 섹터는 **대회 시작 시점에 고정된 것**을 쓴다 (F-02 3.4).
        #   기간 중에 업종이 바뀌면 어제 합법이던 포트가 오늘 위반이 되기 때문이다.
        universe = ContestUniverse.objects.filter(contest=contest, symbol=symbol).first()
        sector_code = universe.sector_code if universe else ""

        sector_limit = None
        if sector_code:
            weight = ContestSectorWeight.objects.filter(
                contest=contest, sector_code=sector_code
            ).first()
            if weight is not None:
                # ★ `limit_pct` 는 대회 시작 때 계산해 저장해 둔 값이다 (E-02 5장).
                #   여기서 다시 계산하지 않는다 — 규칙을 바꿨을 때 과거 판정과 어긋난다.
                sector_limit = weight.limit_pct

        return cls(
            contest=contest,
            rules=rules,
            sector_code=sector_code,
            sector_limit_pct=sector_limit,
            stock=StockMaster.objects.filter(symbol=symbol).first(),
        )


# ─────────────────────────────────────────────────────────────────
# 4. 검증 파이프라인 (F-04 4.1)
# ─────────────────────────────────────────────────────────────────


def check_participation(participation: Participation | None) -> None:
    """① 대회가 진행 중이고 ② 참가가 승인 상태인가.

    Raises:
        RuleRejection: `CONTEST_NOT_ONGOING` · `NOT_PARTICIPANT`.
    """
    if participation is None:
        raise RuleRejection(
            "NOT_PARTICIPANT", "account", "이 대회의 참가자가 아닙니다."
        )

    contest = participation.contest
    if contest.status != ContestStatus.ONGOING:
        raise RuleRejection(
            "CONTEST_NOT_ONGOING",
            "account",
            f"대회가 진행 중이 아닙니다 (현재 상태: {contest.get_status_display()}).",
            {"status": contest.status},
        )

    if participation.status != ParticipationStatus.APPROVED:
        # ★ 실격(`DISQUALIFIED`)도 여기서 막힌다. 실격자는 랭킹에서만 빠지는 게 아니라
        #   **주문도 낼 수 없다** — 계좌·이력은 그대로 두되 거래는 멈춘다 (F-02 4.3).
        raise RuleRejection(
            "NOT_PARTICIPANT",
            "account",
            f"주문할 수 없는 참가 상태입니다 ({participation.get_status_display()}).",
            {"status": participation.status},
        )


def check_session(at=None) -> None:
    """② 거래 시간인가.

    Raises:
        MarketClosed: 장외. **거부가 아니다** — 호출부가 `PENDING_OPEN` 으로 접수한다.
    """
    state = session_state(at)
    if state != SessionState.OPEN:
        raise MarketClosed(state)


def check_symbol_tradable(context: RuleContext, symbol: str, side: str) -> None:
    """③ 거래 불가 종목인가 (F-04 2장).

    ★★ **매수·매도 비대칭 — 매도는 언제나 허용한다** (F-04 2.2) ────────────

    이미 보유한 종목이 기간 중에 관리종목으로 지정되거나 거래대금이 말라붙으면
    매수는 막히지만 **팔 길은 열어둬야 한다.** 막으면 참가자가 물린 채 대회를 끝낸다.

    Raises:
        RuleRejection: `SYMBOL_NOT_TRADABLE`. `detail.reasons` 에 **걸린 조건을 전부**
            담는다 — 여기서만은 하나만 알리지 않는다. 종목 자체의 성질이라
            하나를 고쳐도 다음이 걸릴 뿐이고, 참가자는 "이 종목은 안 되는구나"를
            한 번에 알아야 한다 (A-03 1.6).
    """
    if side == OrderSide.SELL:
        return

    stock = context.stock
    if stock is None:
        raise RuleRejection(
            "SYMBOL_NOT_TRADABLE",
            "symbol",
            f"{symbol} 은(는) 종목 마스터에 없습니다.",
            {"reasons": [{"code": "UNKNOWN_SYMBOL", "message": "종목 정보가 없습니다"}]},
        )

    rules = context.rules
    reasons: list[dict] = []

    # 조건 1 — 보통주가 아님 (우선주·ETF·ETN·리츠·스팩)
    if stock.stock_type != StockType.COMMON:
        reasons.append({
            "code": "NOT_COMMON_STOCK",
            "message": f"보통주가 아닙니다 ({stock.get_stock_type_display()})",
        })

    if stock.is_delisted:
        reasons.append({"code": "DELISTED", "message": "상장폐지 종목입니다"})

    # ★ 조건 6 (E-31) — **업종이 매겨지지 않은 종목은 유니버스에서 제외한다.**
    #   섹터 한도가 `max(시장 섹터 비중 × 2, 10%)` 인데 섹터를 모르면 넣을 값이 없다.
    #   면제하면 미분류 종목만 담아 **섹터 한도를 통째로 우회**할 수 있다.
    if not context.sector_code:
        reasons.append({
            "code": "NO_SECTOR",
            "message": "KRX 업종이 부여되지 않아 대회 유니버스에서 제외됩니다",
        })

    # 조건 2 — 5일 평균 거래대금
    min_turnover = int(rules.get("min_avg_turnover_krw") or 0)
    if min_turnover and stock.avg_turnover_5d <= min_turnover:
        reasons.append({
            "code": "LOW_TURNOVER",
            "message": (
                f"5일 평균 거래대금 {stock.avg_turnover_5d / 100_000_000:.1f}억 "
                f"(기준 {min_turnover / 100_000_000:.0f}억)"
            ),
        })

    # 조건 5 — 시가총액
    min_cap = int(rules.get("min_market_cap_krw") or 0)
    if min_cap and stock.market_cap < min_cap:
        reasons.append({
            "code": "SMALL_MARKET_CAP",
            "message": (
                f"시가총액 {stock.market_cap / 100_000_000:.0f}억 "
                f"(기준 {min_cap / 100_000_000:.0f}억 이상)"
            ),
        })

    # 조건 4 — 관리종목·시장경보
    if rules.get("block_supervised", True):
        if stock.is_supervised:
            reasons.append({"code": "SUPERVISED", "message": "관리종목"})
        if stock.alert_level:
            reasons.append({"code": "ALERT", "message": stock.alert_level})

    # 조건 3 — 신규상장·재상장 후 N영업일 미만
    block_days = int(rules.get("new_listing_block_days") or 0)
    if block_days and stock.listing_date:
        elapsed = _business_days_since(stock.listing_date)
        if elapsed is not None and elapsed < block_days:
            reasons.append({
                "code": "NEW_LISTING",
                "message": f"상장 후 {elapsed}영업일 (기준 {block_days}영업일 이상)",
            })

    if reasons:
        raise RuleRejection(
            "SYMBOL_NOT_TRADABLE",
            "symbol",
            f"{stock.name} 은(는) 대회에서 매수할 수 없는 종목입니다: "
            + " · ".join(item["message"] for item in reasons),
            {"reasons": reasons},
        )


def check_limits(
    context: RuleContext,
    portfolio: Portfolio,
    *,
    symbol: str,
    side: str,
    amount_krw: int,
) -> None:
    """⑥ 편입 한도 — 종목별 · 섹터 · 소형주 합산 (F-04 3장).

    Args:
        amount_krw: 이번에 **매수할 금액**(체결 예상 금액, 수수료 제외).

    ★★ **매도는 검사하지 않는다 — 해소 행위이기 때문이다** (F-04 3.4) ────────

    보유 중 주가가 급등해 한도를 넘는 것은 **허용**한다. 강제 매도하지 않고 경고만 한다.
    막아야 할 것은 **초과 상태에서 더 사는 것**이고, 파는 것은 오히려 권장이다.

        주문 시점에 체결 후 예상 비중이 한도 초과      →  거부
        보유 중 주가 급등으로 한도 초과               →  경고만 (여기서 안 본다)
        초과 상태에서 추가 매수                       →  거부
        초과 상태에서 매도                           →  허용

    Raises:
        RuleRejection: `POSITION_LIMIT` · `SECTOR_LIMIT` · `SMALL_CAP_LIMIT`.
    """
    if side == OrderSide.SELL:
        return

    total = portfolio.total_asset
    if total <= 0:
        return

    # ★★ **분모가 바뀌지 않는다** ────────────────────────────────────────────
    #
    #   매수는 현금이 주식으로 바뀌는 것이라 **순자산 총액은 그대로**다
    #   (수수료만큼 줄지만 비중 판정에서는 무시할 수준이다).
    #   분자만 늘어난다고 보는 것이 맞다. 분모를 함께 늘리면(`total + amount`)
    #   한도가 실제보다 느슨해져 규칙이 조용히 헐거워진다.
    rules = context.rules

    _check_position_limit(rules, portfolio, symbol, amount_krw, total)
    _check_sector_limit(context, portfolio, symbol, amount_krw, total)
    _check_small_cap_limit(context, portfolio, symbol, amount_krw, total)


def _check_position_limit(
    rules: dict, portfolio: Portfolio, symbol: str, amount: int, total: int
) -> None:
    """종목별 한도 — 기본 15%, 예외는 대회마다 다르다 (F-04 3.1)."""
    exceptions = rules.get("position_limit_exceptions") or {}
    limit_pct = Decimal(str(exceptions.get(symbol, rules.get("position_limit_pct", 15.0))))

    current = portfolio.value_of(symbol)
    after = current + amount
    after_pct = _pct(after, total)

    if after_pct <= limit_pct:
        return

    allowed = int(total * limit_pct / 100)
    raise RuleRejection(
        "POSITION_LIMIT",
        "weight_pct",
        f"이 종목 편입 비중이 {after_pct:.1f}% 가 되어 한도 {limit_pct}% 를 초과합니다.",
        _limit_detail(current, after, limit_pct, allowed, total),
    )


def _check_sector_limit(
    context: RuleContext, portfolio: Portfolio, symbol: str, amount: int, total: int
) -> None:
    """섹터 한도 — `max(시장 섹터 비중 × 2, 10%)` (F-04 3.2).

    한도값은 대회 시작 시점에 계산해 `ContestSectorWeight.limit_pct` 에 굳혀 두었다.

    ★ **`limit_pct` 가 없으면 검사하지 않는다.** 섹터 비중 스냅샷이 아직 안 만들어진
      대회(운영자가 유니버스를 고정하기 전)에서 규칙만 먼저 발동하면, 모든 주문이
      "섹터를 모른다"는 이유로 막힌다. 업종 자체가 없는 종목은 이미 3장(`NO_SECTOR`)
      에서 걸러졌으므로, 여기 남는 것은 **우리 쪽 준비가 덜 된 경우**뿐이다.
    """
    if context.sector_limit_pct is None or not context.sector_code:
        return

    # 같은 섹터에 속한 보유 종목들의 평가액 합
    sector_symbols = set(
        ContestUniverse.objects.filter(
            contest=context.contest, sector_code=context.sector_code
        ).values_list("symbol", flat=True)
    )
    current = sum(item.value for item in portfolio.holdings if item.symbol in sector_symbols)
    after = current + amount
    after_pct = _pct(after, total)
    limit_pct = context.sector_limit_pct

    if after_pct <= limit_pct:
        return

    allowed = int(total * limit_pct / 100)
    sector_name = (
        ContestSectorWeight.objects.filter(
            contest=context.contest, sector_code=context.sector_code
        ).values_list("sector_name", flat=True).first()
        or context.sector_code
    )
    raise RuleRejection(
        "SECTOR_LIMIT",
        "weight_pct",
        f"{sector_name} 섹터 비중이 {after_pct:.1f}% 가 되어 "
        f"한도 {limit_pct}% 를 초과합니다.",
        {
            **_limit_detail(current, after, limit_pct, allowed, total),
            "sector_code": context.sector_code,
            "sector_name": sector_name,
        },
    )


def _check_small_cap_limit(
    context: RuleContext, portfolio: Portfolio, symbol: str, amount: int, total: int
) -> None:
    """소형주 합산 한도 — 시총 1조 미만 종목 합계 ≤ 30% (F-04 3.3)."""
    threshold = int(context.rules.get("small_cap_threshold_krw") or 0)
    limit_pct = Decimal(str(context.rules.get("small_cap_total_limit_pct") or 0))
    if not threshold or not limit_pct:
        return

    # 이번에 사는 종목이 소형주가 아니면 합계가 늘지 않는다 — 검사할 필요가 없다.
    stock = context.stock
    if stock is None or stock.market_cap >= threshold:
        return

    held = [item.symbol for item in portfolio.holdings]
    small_caps = set(
        StockMaster.objects.filter(symbol__in=held, market_cap__lt=threshold)
        .values_list("symbol", flat=True)
    )
    current = sum(item.value for item in portfolio.holdings if item.symbol in small_caps)
    after = current + amount
    after_pct = _pct(after, total)

    if after_pct <= limit_pct:
        return

    allowed = int(total * limit_pct / 100)
    raise RuleRejection(
        "SMALL_CAP_LIMIT",
        "weight_pct",
        f"시가총액 {threshold / 1_000_000_000_000:.0f}조 미만 종목 합산 비중이 "
        f"{after_pct:.1f}% 가 되어 한도 {limit_pct}% 를 초과합니다.",
        {
            **_limit_detail(current, after, limit_pct, allowed, total),
            "threshold_krw": threshold,
        },
    )


# ─────────────────────────────────────────────────────────────────
# 5. 보조
# ─────────────────────────────────────────────────────────────────


def _pct(value: int, total: int) -> Decimal:
    """비중(%) — DB·화면 규약대로 **퍼센트 값**을 만든다 (규약 4.2)."""
    if total <= 0:
        return Decimal(0)
    return (Decimal(value) / Decimal(total) * 100).quantize(Decimal("0.0001"))


def _limit_detail(current: int, after: int, limit_pct: Decimal, allowed: int, total: int) -> dict:
    """한도 위반 응답의 `detail` — **"여기까지는 됩니다"를 함께 준다** (F-04 4.2).

    `max_additional_krw` 가 음수가 되는 경우가 있다 — **이미 한도를 넘긴 상태**다
    (주가 급등으로 초과된 뒤 추가 매수를 시도). 그때는 0 으로 깎는다.
    "-320만원까지 살 수 있습니다"는 화면에서 읽을 수 없는 문장이기 때문이다.
    """
    max_additional = max(allowed - current, 0)
    return {
        "current_pct": float(_pct(current, total)),
        "after_pct": float(_pct(after, total)),
        "limit_pct": float(limit_pct),
        "max_additional_krw": max_additional,
        "max_additional_weight_pct": float(_pct(max_additional, total)),
    }


def _business_days_since(listing_date) -> int | None:
    """상장일 이후 지난 영업일 수. 달력이 그 구간을 덮지 못하면 `None`.

    ★ **`None` 을 "조건 통과"로 다루는 이유** — 신규상장 판정은 달력이 있어야 하는데,
      `TradingCalendar` 는 미래를 못 채우고(E-20) 과거도 운영자가 언제 채웠느냐에
      달렸다. 달력이 비었다는 이유로 **정상 종목을 신규상장으로 몰아 막는 것**이
      더 나쁜 오류다. 상장 6영업일 이내 종목은 어차피 거래대금·시총 조건에서도
      대개 걸린다.
    """
    today = today_kst()
    if listing_date > today:
        return 0
    covered = TradingCalendar.objects.filter(date__gte=listing_date, date__lte=today).exists()
    if not covered:
        logger.info("상장일 판정 건너뜀 — TradingCalendar 가 %s~%s 를 덮지 않습니다", listing_date, today)
        return None
    return TradingCalendar.objects.filter(
        date__gt=listing_date, date__lte=today, is_open=True
    ).count()


def resolve_participation(account) -> Participation | None:
    """계좌에 붙은 참가 정보. `Participation.account` 가 O2O 라 역참조가 단수다.

    ★ 호출부가 `account.participation` 을 직접 쓰면 참가가 없을 때
      `RelatedObjectDoesNotExist` 가 난다 — `None` 이 아니다. Django O2O 역참조의
      함정이라 여기서 감싼다.
    """
    return Participation.objects.filter(account=account).select_related("contest").first()


__all__ = [
    "Holding",
    "MarketClosed",
    "Portfolio",
    "RuleContext",
    "RuleRejection",
    "check_limits",
    "check_participation",
    "check_session",
    "check_symbol_tradable",
    "resolve_participation",
]
