"""주문 · 체결 엔진 (F-03 · F-04 4.1).

**돈이 걸린 유일한 경로다.** 여기가 틀리면 화면은 멀쩡한데 잔고가 조용히 어긋난다.
그래서 이 모듈은 다른 어디보다 촘촘히 주석을 단다.

    주문 입력 ─▶ 검증(F-04) ─▶ 지정가 결정 ─▶ 호가 소진 ─▶ 반영 ─▶ Order/Execution
                    │                                          │
                 거부(REJECTED)                            현금·포지션

★★ **한 벌 원칙 — `apply_fills()` 가 이 모듈의 심장이다** ────────────────────

체결은 두 시점에 일어난다.

    ① 주문 접수 즉시            `place_order()`      호가를 소진해 조각을 만든다
    ② 시세 폴링마다 추종        `fill_open_order()`  조건이 맞으면 남은 잔량을 채운다

**조각을 만드는 방법은 다르지만, 만들어진 조각을 반영하는 방법은 같아야 한다.**
현금 차감·평단 재계산·수수료·실현손익을 두 곳에 각각 적으면 언젠가 어긋나고,
어긋난 쪽이 어느 쪽인지 아무도 모르게 된다. 그래서 **반영은 `apply_fills()` 하나**이고
①과 ②는 그 함수에 조각 목록을 넘길 뿐이다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI + SQLAlchemy 에서는 `db.refresh(member, with_for_update=True)` 로 행을 잠갔다.
Django 는 `select_for_update()` 인데 **`transaction.atomic()` 블록 안에서만** 유효하다.
밖에서 부르면 `TransactionManagementError` 가 난다. autocommit 이 기본이라
"명시적으로 연 구간만 트랜잭션" 이기 때문이다 (F-03 7.1).

또 하나 — SQLAlchemy 는 객체를 고치고 `session.commit()` 하면 바뀐 필드를 알아서
찾아 UPDATE 했다. Django 는 `save()` 가 **모든 컬럼을 다시 쓴다.**
그래서 여기서는 `save(update_fields=[...])` 를 항상 명시한다. 동시에 다른 경로가
같은 행의 다른 컬럼을 고쳤을 때 그것을 되돌리지 않기 위해서다.
"""

import logging
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

from django.db import transaction
from django.utils import timezone

from accounts.models import Account
from contests import rules as contest_rules
from core.constants import AssetClass, OrderSide
from core.models import AppSetting
from market import quotes as market_quotes
from market.quotes import Orderbook, PriceUnavailable, Quote, tick_size
from trading.models import Execution, Order, OrderSource, OrderStatus, OrderType, Position

logger = logging.getLogger(__name__)

# ★ 10호가를 넘어선 **가정 체결**을 몇 단계까지 이어갈 것인가 (F-03 5.1 4단계).
#
#   가정 체결은 "1~10호가의 평균 잔량이 1틱 간격으로 계속 있다" 는 낙관적 전제다.
#   시장가 주문(STOP 발동)은 지정가가 없어 조건만으로는 멈추지 않으므로 상한이 필요하다.
#   50단계면 삼성전자 기준 5,000원 폭이라 어떤 주문이든 소진된다.
MAX_ASSUMED_LEVELS = 50

# 연습 모드 요율 설정 키 (F-03 6장). 자산군마다 다르다 — 코인은 매도세가 없다.
PRACTICE_RATE_KEYS = {
    AssetClass.STOCK: ("practice.stock.fee_bp", "practice.stock.tax_bp"),
    AssetClass.CRYPTO: ("practice.crypto.fee_bp", "practice.crypto.tax_bp"),
    AssetClass.ALT: ("practice.alt.fee_bp", "practice.alt.tax_bp"),
}


class OrderRejected(Exception):
    """주문을 받아들일 수 없다 — 규칙 위반·잔고 부족·잘못된 입력.

    `contests.rules.RuleRejection` 과 같은 모양(`rule`/`field`/`message`/`detail`)을 쓴다.
    화면은 둘을 구분할 필요가 없고, **구분해야 하는 것은 우리 쪽 코드뿐**이라
    타입만 나눠 둔다 (대회 규칙인가 / 주문 자체의 문제인가).
    """

    def __init__(self, rule: str, field_name: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.rule = rule
        self.field = field_name
        self.message = message
        self.detail = detail or {}

    def as_payload(self) -> dict:
        return {
            "ok": False,
            "rule": self.rule,
            "field": self.field,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class Fill:
    """체결 조각 하나. `Execution` 행이 되기 전의 순수 값.

    ★ 모델이 아니라 dataclass 인 이유 — **미리보기(`POST /api/orders/preview`)는
      체결하지 않고 계산만 한다.** 조각을 만드는 계산과 그것을 저장하는 일이
      분리돼 있어야 미리보기가 같은 코드를 쓸 수 있다 (A-03 2장).
    """

    qty: Decimal
    price: Decimal
    price_level: int | None = None
    is_assumed_depth: bool = False

    @property
    def amount(self) -> int:
        """체결금액(원). **원 단위로 절사한다** — 금액은 전부 정수다 (규약 2.2)."""
        return int((self.qty * self.price).to_integral_value(rounding=ROUND_DOWN))


@dataclass
class FillResult:
    """체결 계산 결과. 미리보기와 실제 체결이 함께 쓴다."""

    fills: list[Fill] = field(default_factory=list)
    remaining_qty: Decimal = Decimal(0)

    @property
    def filled_qty(self) -> Decimal:
        return sum((fill.qty for fill in self.fills), Decimal(0))

    @property
    def gross_amount(self) -> int:
        return sum(fill.amount for fill in self.fills)

    @property
    def avg_price(self) -> Decimal:
        """가중평균 체결가. 조각마다 가격이 다르므로 평균만으로는 못 되짚는다 —
        그래서 조각도 함께 저장한다 (`Execution`)."""
        qty = self.filled_qty
        if qty <= 0:
            return Decimal(0)
        return (Decimal(self.gross_amount) / qty).quantize(Decimal("0.00000001"))

    @property
    def has_assumed_depth(self) -> bool:
        return any(fill.is_assumed_depth for fill in self.fills)


# ─────────────────────────────────────────────────────────────────
# 1. 거래 비용 (F-03 6장)
# ─────────────────────────────────────────────────────────────────


def cost_rates(account: Account) -> tuple[Decimal, Decimal]:
    """이 계좌에 적용할 (수수료 bp, 매도세 bp).

    ★★ **대회 요율은 `Contest` 에서 읽는다. 전역 설정이 아니다** ──────────────

    `AppSetting` 에 두면 값을 고치는 순간 **진행 중인 대회의 규칙이 바뀐다.**
    타임폴리오도 SK하이닉스 편입 예외를 대회 중간에 도입한 전례가 있어(F-04 3.1),
    "규칙은 대회마다 다르고 기간 중에도 다를 수 있다" 가 전제다.

    연습 모드만 `AppSetting` 을 쓰고, **자산군마다 키가 다르다** — 코인은 매도세가
    없고 대체자산은 비용 자체가 없다.
    """
    if account.is_contest and account.contest_id:
        contest = account.contest
        return Decimal(contest.fee_bp), Decimal(contest.tax_bp)

    fee_key, tax_key = PRACTICE_RATE_KEYS.get(
        account.asset_class, PRACTICE_RATE_KEYS[AssetClass.STOCK]
    )
    return _setting_bp(fee_key), _setting_bp(tax_key)


def _setting_bp(key: str) -> Decimal:
    """`AppSetting` 의 bp 값. 없으면 0 — **비용을 지어내지 않는다.**

    ★ 기본값을 코드에 박아 두면 시드가 빠진 것을 아무도 모른 채 넘어간다.
      0 이면 화면의 수수료 칸이 0원으로 보여 **바로 눈에 띈다.**
    """
    row = AppSetting.objects.filter(key=key).values_list("value", flat=True).first()
    if row is None:
        logger.warning("요율 설정이 없습니다: %s — 0bp 로 계산합니다", key)
        return Decimal(0)
    return Decimal(str(row))


def calc_cost(gross: int, side: str, fee_bp: Decimal, tax_bp: Decimal) -> tuple[int, int]:
    """(수수료, 매도세). **원 단위 floor 절사** (F-03 6장).

    매도세는 매도할 때만 붙는다. 매수는 세금이 0 이다.

        수수료 = floor(체결금액 × 수수료율)
        매도세 = floor(체결금액 × 매도세율)

    ★ bp(베이시스포인트)는 1/10000 이다. `10bp = 0.10%`.
      연습(주식)의 `1.5bp = 0.015%` 처럼 **소수 bp 가 있어서** Decimal 로 다룬다.
    """
    fee = int((Decimal(gross) * fee_bp / 10000).to_integral_value(rounding=ROUND_DOWN))
    tax = 0
    if side == OrderSide.SELL:
        tax = int((Decimal(gross) * tax_bp / 10000).to_integral_value(rounding=ROUND_DOWN))
    return fee, tax


# ─────────────────────────────────────────────────────────────────
# 2. 지정가 결정 (F-03 4장)
# ─────────────────────────────────────────────────────────────────


def resolve_limit_price(
    *,
    order_type: str,
    side: str,
    price_level: int | None,
    limit_price: Decimal | None,
    book: Orderbook | None,
    quote: Quote | None,
) -> Decimal | None:
    """가격 유형 4종을 **하나의 지정가**로 환산한다.

    | 유형 | 매수 | 매도 | 성격 |
    |---|---|---|---|
    | `RELATIVE` n (상대호가) | 매도 n호가 | 매수 n호가 | **공격적** — 상대를 찾아간다 |
    | `OWN` n (자기호가) | 매수 n호가 | 매도 n호가 | **소극적** — 줄을 선다 |
    | `LIMIT` | 입력값 | 입력값 | 그대로 |
    | `MARKET` · `STOP` 발동 | — | — | `None` = 가격 제한 없음 |

    Returns:
        지정가. `None` 이면 **제한 없음**(시장가)이라 호가를 끝까지 먹는다.

    ★ **매수의 자기호가 = 매수호가**라는 게 헷갈리는 지점이다. "내 편"이라고 읽으면
      쉽다 — 내가 사려는 쪽 줄(매수 대기열)에 서는 것이고, 그래서 기존 잔량이 먼저
      체결된 뒤에야 내 차례가 온다.
    """
    if order_type == OrderType.LIMIT:
        if limit_price is None or limit_price <= 0:
            raise OrderRejected("INVALID_ORDER", "limit_price", "지정가를 입력해 주십시오.")
        return limit_price

    if order_type in (OrderType.MARKET, OrderType.STOP):
        return None

    if book is None:
        raise PriceUnavailable(
            "", "호가가 없어 가격 유형을 계산할 수 없습니다.", recoverable=True
        )

    level = price_level or 1
    if not 1 <= level <= 10:
        raise OrderRejected("INVALID_ORDER", "price_level", "호가 단계는 1~10 입니다.")

    rows = book.opposite(side) if order_type == OrderType.RELATIVE else book.own(side)
    if len(rows) >= level:
        return rows[level - 1][0]

    # ── 호가가 그 단계까지 없다 ──────────────────────────────────
    #
    # ★ **상·하한가 특례** (F-03 4.1) — "상(하)한가에 들어간 종목의 자기호가
    #   매도(수) 주문은 상(하)한가에 체결된다."
    #
    #   상한가에 갇히면 파는 사람이 없어 **매도호가가 통째로 빈다.** 그때 자기호가
    #   매도(= 매도호가에 줄서기)는 갈 곳이 없는데, 규칙은 상한가에 체결된다고 본다.
    #
    #   ★ `price_limits()` 는 전일 종가 기반 **근사값**이다(그 함수의 주석 참조).
    #     그래서 여기서는 *체결 여부*를 정할 뿐이고, 실제 체결가는 아래 매칭 단계가
    #     호가창을 보고 다시 정한다. 근사가 빗나가도 "특례가 안 걸릴" 뿐이다.
    if order_type == OrderType.OWN and quote is not None and quote.prev_close > 0:
        upper, lower = market_quotes.price_limits(quote.prev_close)
        limit = upper if side == OrderSide.SELL else lower
        if limit > 0:
            logger.info(
                "%s: %s %d호가가 비어 상·하한가 특례를 적용합니다 (%s)",
                quote.symbol, "매도" if side == OrderSide.SELL else "매수", level, limit,
            )
            return limit

    # 상대호가가 비었다는 것은 **받아 줄 사람이 없다**는 뜻이다. 거부하지 않고
    # 미체결로 남긴다 — 잠시 뒤 호가가 채워지면 추종 체결이 가져간다.
    raise PriceUnavailable(
        quote.symbol if quote else "",
        f"{level}호가가 비어 있어 지금은 체결할 수 없습니다. 주문은 접수된 상태로 유지됩니다.",
        recoverable=True,
    )


# ─────────────────────────────────────────────────────────────────
# 3. 호가 소진 체결 (F-03 5.1)
# ─────────────────────────────────────────────────────────────────


def match_orderbook(
    *,
    book: Orderbook,
    side: str,
    limit_price: Decimal | None,
    qty: Decimal,
    quote: Quote | None = None,
    asset_class: str = AssetClass.STOCK,
) -> FillResult:
    """상대호가 잔량을 순서대로 소진하며 체결 조각을 만든다.

    ```
    1. 상대 최우선호가부터, 내 지정가 조건을 만족하는 단계의 잔량만큼 체결
    2. 10호가를 다 먹고도 남으면 → 11호가부터를 **가정**해 계속 (F-03 5.1 4단계)
    3. 그래도 남으면 → PARTIAL. 시장 체결 추종 대상이 된다
    ```

    ★★ **왜 한 번에 전부 체결하지 않는가** ─────────────────────────────────

    v1.0 은 항상 현재가에 전량 즉시 체결했다. 그러면 100억을 넣어도 체결가가 같아
    **시장 충격이라는 개념 자체가 없다.** v2.0 대회는 실호가를 쓰므로
    "큰 주문은 불리한 가격에 체결된다" 는 실제 감각이 생긴다.
    이것이 대회 모드를 연습 모드와 가르는 핵심이다.
    """
    result = FillResult(remaining_qty=qty)
    remaining = qty
    levels = book.opposite(side)

    # ── ① 실제 호가 1~10단계 ────────────────────────────────────
    for index, (price, available) in enumerate(levels, start=1):
        if remaining <= 0:
            break
        if not _price_ok(side, price, limit_price):
            # 지정가 조건을 벗어났다. 뒤 단계는 더 불리하므로 볼 필요가 없다.
            break
        take = min(remaining, available)
        if take <= 0:
            continue
        result.fills.append(Fill(qty=take, price=price, price_level=index))
        remaining -= take

    # ── ② 10호가를 넘어선 가정 체결 ──────────────────────────────
    if remaining > 0 and levels:
        remaining = _fill_assumed_depth(
            result, levels, side, limit_price, remaining, quote, asset_class
        )

    result.remaining_qty = remaining
    return result


def _price_ok(side: str, price: Decimal, limit_price: Decimal | None) -> bool:
    """이 호가가 내 지정가 조건을 만족하는가. `limit_price=None` 은 시장가라 항상 참."""
    if limit_price is None:
        return True
    if side == OrderSide.BUY:
        return price <= limit_price          # 이 가격 이하로만 산다
    return price >= limit_price              # 이 가격 이상으로만 판다


def _fill_assumed_depth(
    result: FillResult,
    levels: list[tuple[Decimal, Decimal]],
    side: str,
    limit_price: Decimal | None,
    remaining: Decimal,
    quote: Quote | None,
    asset_class: str = AssetClass.STOCK,
) -> Decimal:
    """11호가부터를 **가정**해 계속 체결한다 (F-03 5.1 4단계).

        1~10호가의 평균 잔량이 1틱 간격으로 11호가부터 형성되어 있다고 본다.

    ★★ **이것은 실제 시장보다 유리한 가정이다** ────────────────────────────

    진짜 호가창은 멀어질수록 잔량이 얇아지는 게 보통이라, 평균 잔량을 계속
    가정하면 **실제보다 좋은 가격에 더 많이 체결된다.** 타임폴리오 규칙이 그렇게
    정의돼 있어 따르되, **그 사실을 숨기지 않는다** — 이 가정으로 만들어진 조각은
    `is_assumed_depth=True` 로 표시돼 체결 상세에 배지로 뜬다 (E-03 5.1).

    ★ **상·하한가에서 멈춘다.** 가격 제한 없는 시장가(STOP 발동)는 지정가 조건으로
      멈추지 않으므로, 실재하지 않는 가격까지 파고들지 않도록 상·하한가를 벽으로 쓴다.
      전일 종가를 모르면 `MAX_ASSUMED_LEVELS` 가 대신 막는다.
    """
    # ★★ **평균 잔량을 자산군 단위로 절사한다** ─────────────────────────────
    #
    #   평균은 대개 소수가 나온다(60·40·30주 → 43.33주). 그대로 쓰면
    #   **주식이 43.33주 체결되는 조각**이 만들어진다. 0.33주는 존재하지 않는다.
    #   실측에서 실제로 나왔던 값이라 여기에 남긴다 (→ 변경노트 E-38).
    #
    #   `_round_qty` 는 주식·대체자산을 정수로, 코인만 소수 8자리로 깎는다.
    #   1주 미만으로 내려가면 가정 체결을 멈춘다 — 0주짜리 조각은 의미가 없다.
    avg_qty = _round_qty(
        sum((qty for _, qty in levels), Decimal(0)) / Decimal(len(levels)), asset_class
    )
    if avg_qty <= 0:
        return remaining

    price = levels[-1][0]
    upper = lower = Decimal(0)
    if quote is not None and quote.prev_close > 0:
        upper, lower = market_quotes.price_limits(quote.prev_close)

    level_no = len(levels)
    for _ in range(MAX_ASSUMED_LEVELS):
        if remaining <= 0:
            break
        tick = tick_size(price)
        # 매수는 위로(비싸게), 매도는 아래로(싸게) 밀며 체결을 이어간다.
        price = price + tick if side == OrderSide.BUY else price - tick
        level_no += 1

        if price <= 0:
            break
        if side == OrderSide.BUY and upper > 0 and price > upper:
            break
        if side == OrderSide.SELL and lower > 0 and price < lower:
            break
        if not _price_ok(side, price, limit_price):
            break

        take = min(remaining, avg_qty)
        result.fills.append(
            Fill(qty=take, price=price, price_level=level_no, is_assumed_depth=True)
        )
        remaining -= take

    return remaining


def match_at_price(*, price: Decimal, qty: Decimal) -> FillResult:
    """호가창 없이 **한 가격에 전량** 체결한다 — 연습 모드와 추종 체결이 쓴다.

    연습 모드는 v1.0 동작(현재가 전량 즉시)을 유지한다 (F-03 5.2 주석).
    추종 체결도 이 형태다 — 시장 체결가가 내 지정가 조건을 넘은 순간
    **그 가격으로** 남은 잔량을 채운다 (F-03 5.2).
    """
    if qty <= 0 or price <= 0:
        return FillResult(remaining_qty=max(qty, Decimal(0)))
    return FillResult(fills=[Fill(qty=qty, price=price)], remaining_qty=Decimal(0))


# ─────────────────────────────────────────────────────────────────
# 4. 반영 — **즉시 체결과 추종 체결이 공유하는 유일한 경로** ★★
# ─────────────────────────────────────────────────────────────────


def apply_fills(order: Order, result: FillResult, *, at=None) -> Order:
    """체결 조각을 계좌·포지션·주문에 반영한다.

    **이 함수만이 현금과 포지션을 바꾼다.** 접수 즉시 체결이든 폴링 추종 체결이든
    여기를 지난다 (모듈 docstring 참조).

    호출 전제:
      · `transaction.atomic()` 안이어야 한다
      · `order.account` 가 `select_for_update()` 로 잠겨 있어야 한다
      · 매도라면 `Position` 도 잠겨 있어야 한다

    Returns:
        갱신된 `order` (저장까지 끝난 상태).
    """
    if not result.fills:
        return order

    at = at or timezone.now()
    account = order.account
    fee_bp, tax_bp = cost_rates(account)

    gross = result.gross_amount
    fee, tax = calc_cost(gross, order.side, fee_bp, tax_bp)

    position = _lock_position(account, order.symbol)

    if order.side == OrderSide.BUY:
        _apply_buy(account, order, position, result, gross, fee, at)
    else:
        _apply_sell(account, order, position, result, gross, fee, tax, at)

    # ── 주문 집계 갱신 ───────────────────────────────────────────
    #
    # ★ **누적한다.** 추종 체결은 이미 일부 체결된 주문에 조각을 더하는 것이라,
    #   덮어쓰면 앞선 체결이 사라진다. 평균가도 금액 합÷수량 합으로 다시 낸다.
    prev_qty = order.filled_qty
    prev_gross = order.gross_amount
    order.filled_qty = prev_qty + result.filled_qty
    order.gross_amount = prev_gross + gross
    order.fee += fee
    order.tax += tax
    order.avg_fill_price = (
        (Decimal(order.gross_amount) / order.filled_qty).quantize(Decimal("0.00000001"))
        if order.filled_qty > 0
        else Decimal(0)
    )
    order.net_amount = (
        -(order.gross_amount + order.fee)
        if order.side == OrderSide.BUY
        else order.gross_amount - order.fee - order.tax
    )

    if order.filled_qty >= order.requested_qty:
        order.status = OrderStatus.FILLED
        order.filled_at = at
    else:
        order.status = OrderStatus.PARTIAL

    order.save(update_fields=[
        "filled_qty", "gross_amount", "fee", "tax", "avg_fill_price",
        "net_amount", "realized_pnl", "status", "filled_at",
    ])

    _record_executions(order, result, at)
    return order


def _apply_buy(account, order, position, result, gross, fee, at) -> None:
    """매수 반영 — 현금 차감 · 평단 재계산 (F-03 7.2)."""
    spend = gross + fee
    if account.cash < spend:
        # 여기까지 왔다는 것은 접수 시 검증을 통과했다는 뜻이다. 그 사이에 다른
        # 주문이 현금을 썼다면 여기서 걸린다 — **계좌 잠금이 있어 실제로는 오지 않지만,
        # 오면 데이터가 깨지므로 조용히 넘기지 않는다.**
        raise OrderRejected(
            "INSUFFICIENT_CASH",
            "qty",
            f"체결 시점 현금이 부족합니다 (필요 {spend:,}원 · 보유 {account.cash:,}원).",
            {"required_krw": spend, "cash": account.cash},
        )

    account.cash -= spend
    account.save(update_fields=["cash", "updated_at"])

    filled = result.filled_qty
    if position is None:
        position = Position(
            account=account,
            symbol=order.symbol,
            asset_class=order.asset_class,
            qty=Decimal(0),
            avg_price=Decimal(0),
            principal=0,
        )

    new_qty = position.qty + filled
    # 새 평단 = (기존 수량 × 기존 평단 + 체결 수량 × 체결가) / 전체 수량
    new_avg = (position.qty * position.avg_price + Decimal(gross)) / new_qty
    position.qty = new_qty
    position.avg_price = _round_price(new_avg, order.asset_class)
    # ★ `principal` 에 수수료를 넣지 않는다 — 평단이 체결가 기반이라
    #   `principal ≒ 수량 × 평단` 이 성립해야 매도 시 원금 차감이 어긋나지 않는다.
    position.principal += gross
    position.save()


def _apply_sell(account, order, position, result, gross, fee, tax, at) -> None:
    """매도 반영 — 현금 가산 · 원금 차감 · **실현손익 기록** (F-03 7.2 · E-03 4.4)."""
    filled = result.filled_qty
    if position is None or position.qty < filled:
        held = position.qty if position else Decimal(0)
        raise OrderRejected(
            "INSUFFICIENT_POSITION",
            "qty",
            f"체결 시점 보유 수량이 부족합니다 (필요 {filled} · 보유 {held}).",
            {"required_qty": str(filled), "held_qty": str(held)},
        )

    proceeds = gross - fee - tax
    account.cash += proceeds
    account.save(update_fields=["cash", "updated_at"])

    # ★★ **실현손익은 지금 계산해 저장한다** (E-03 4.4) ─────────────────────
    #
    #   매도 직전 평단이 있어야 계산할 수 있는데, `Position` 은 현재 상태만 갖고
    #   있어 **사후에는 과거 평단을 복원할 수 없다.** 거래이력 상단의 실현손익 합계를
    #   `SUM(realized_pnl)` 한 줄로 끝내기 위해 체결 시점에 남긴다.
    #
    #       실현손익 = (체결가 - 매도 직전 평단) × 체결수량 - 수수료 - 매도세
    avg_before = position.avg_price
    pnl = int(
        (Decimal(gross) - avg_before * filled).to_integral_value(rounding=ROUND_DOWN)
    ) - fee - tax
    order.realized_pnl = (order.realized_pnl or 0) + pnl

    remaining_qty = position.qty - filled
    if remaining_qty <= 0:
        # ★ **수량이 0 이면 행을 삭제한다** (F-03 7.2). 0 행을 남기면 "보유 종목 수"
        #   집계마다 `qty > 0` 을 달아야 하고, 빠뜨리면 조용히 틀린다.
        #   원금 잔차(규약 2.2)도 이때 함께 사라진다 — 수량 0 에 원금 3원이 남는
        #   유령을 만들지 않는다.
        position.delete()
        return

    # 평단은 불변. 매수원금만 매도 비율만큼 덜어낸다.
    used_principal = int(
        (Decimal(position.principal) * filled / position.qty).to_integral_value(
            rounding=ROUND_DOWN
        )
    )
    position.qty = remaining_qty
    position.principal = max(position.principal - used_principal, 0)
    position.save(update_fields=["qty", "principal", "updated_at"])


def _record_executions(order: Order, result: FillResult, at) -> None:
    """체결 조각을 `Execution` 행으로 남긴다.

    ★ `seq` 는 **주문 안에서 이어진다.** 추종 체결로 조각이 나중에 붙어도 번호가
      겹치면 `UNIQUE(order, seq)` 에 걸린다. 이미 저장된 마지막 번호부터 이어 붙인다.
    """
    last_seq = (
        Execution.objects.filter(order=order).order_by("-seq").values_list("seq", flat=True).first()
        or 0
    )
    Execution.objects.bulk_create([
        Execution(
            order=order,
            seq=last_seq + offset,
            qty=fill.qty,
            price=fill.price,
            amount=fill.amount,
            price_level=fill.price_level,
            is_assumed_depth=fill.is_assumed_depth,
            executed_at=at,
        )
        for offset, fill in enumerate(result.fills, start=1)
    ])


def _lock_account(account_id: int) -> Account:
    """계좌 행을 잠그고 읽는다. `transaction.atomic()` 안에서만 유효하다 (F-03 7.1).

    ★★ **`of=("self",)` 가 없으면 Postgres 가 거부한다** ──────────────────────

        NotSupportedError: FOR UPDATE cannot be applied to the nullable side
                           of an outer join

    `Account.contest` 는 nullable FK 라(연습 계좌는 비어 있다) `select_related("contest")`
    가 **LEFT OUTER JOIN** 을 만든다. Postgres 는 그 outer join 의 nullable 쪽에
    행 잠금을 걸 수 없다 — 잠글 행이 없을 수도 있기 때문이다.

    `of=("self",)` 는 `FOR UPDATE OF "account"` 로 나가 **계좌 행만** 잠근다.
    우리가 잠그고 싶은 것도 그것뿐이다 — 대회 설정은 읽기만 한다.

    Django 관점 — SQLAlchemy 의 `with_for_update(of=Account)` 와 같은 뜻이다.
    다만 SQLAlchemy 는 조인 전략을 손으로 고르는 일이 많아 이 조합을 만들 일이
    드물었고, Django 는 `select_related` 가 조인을 자동으로 만들어 **더 쉽게 밟는다.**
    """
    return (
        Account.objects.select_for_update(of=("self",))
        .select_related("contest")
        .get(pk=account_id)
    )


def _lock_position(account: Account, symbol: str) -> Position | None:
    """같은 종목에 동시 주문이 들어올 수 있다 — 행을 잠그고 읽는다 (E-03 9장).

    ★ 행이 없으면 잠글 것도 없다. 그때는 두 요청이 동시에 새 포지션을 만들려 할 수
      있는데, `UNIQUE(account, symbol)` 이 한쪽을 `IntegrityError` 로 막는다.
      **DB 제약이 마지막 방어선**이고, 그래서 그 제약이 거기 있다.
    """
    return (
        Position.objects.select_for_update()
        .filter(account=account, symbol=symbol)
        .first()
    )


def _round_price(price: Decimal, asset_class: str) -> Decimal:
    """평단 반올림 — **주식은 정수, 코인은 소수점 8자리** (F-03 7.2).

    주식 평단에 소수가 남으면 화면마다 다르게 반올림돼 값이 흔들린다.
    코인은 8자리가 거래 단위라 그대로 둔다.
    """
    if asset_class == AssetClass.CRYPTO:
        return price.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    return price.quantize(Decimal("1"), rounding=ROUND_DOWN)


def _round_qty(qty: Decimal, asset_class: str) -> Decimal:
    """주문 수량 — **주식·대체자산은 정수 주(계약), 코인만 소수** (F-03 3.1).

    주식을 0.5주 살 수는 없다. `floor` 로 깎는다 — 반올림하면 잔고보다 많이 사려는
    주문이 만들어질 수 있다. **틀리는 방향을 안전한 쪽으로** 둔다.
    """
    if asset_class == AssetClass.CRYPTO:
        return qty.quantize(Decimal("0.00000001"), rounding=ROUND_DOWN)
    return qty.quantize(Decimal("1"), rounding=ROUND_DOWN)


# ─────────────────────────────────────────────────────────────────
# 5. 주문 접수 — 진입점 (F-04 4.1 파이프라인)
# ─────────────────────────────────────────────────────────────────


@transaction.atomic
def place_order(
    *,
    account: Account,
    symbol: str,
    side: str,
    order_type: str,
    qty: Decimal | None = None,
    weight_pct: Decimal | None = None,
    amount_krw: int | None = None,
    price_level: int | None = None,
    limit_price: Decimal | None = None,
    stop_price: Decimal | None = None,
    source: str = OrderSource.WEB,
) -> Order:
    """주문 접수 → 검증 → 즉시 체결 시도 → 잔고·포지션 반영.

    **검증 순서가 곧 F-04 4.1 의 7단계다.** 하나라도 실패하면 즉시 중단하고
    **첫 번째 이유만** 반환한다 — 한꺼번에 다 보여주면 참가자가 무엇부터 고쳐야
    할지 모른다 (예외: 거래 불가 종목은 사유를 전부 준다. `check_symbol_tradable` 참조).

    Args:
        weight_pct: **대회 모드의 주문 단위** — 수량이 아니라 순자산 대비 %다 (F-03 3.1).
        qty · amount_krw: 연습 모드. 수량 직접 입력 또는 금액 입력.

    Returns:
        `Order`. 상태는 `FILLED` · `PARTIAL` · `ACCEPTED` · `PENDING_OPEN` 중 하나.
        **거부는 반환이 아니라 예외**다 (`OrderRejected` · `RuleRejection`).

    Raises:
        OrderRejected · contests.rules.RuleRejection: 규칙 위반·잔고 부족.
        market.quotes.PriceUnavailable: 수량을 환산할 시세조차 없는 경우 (503).

    ★★ **거부를 `Order(status=REJECTED)` 행으로 남기지 않는다** ────────────────

    모델에 `REJECTED` 상태와 `reject_rule` · `reject_message` 컬럼이 있지만,
    **접수 단계에서 걸린 것은 행을 만들지 않는다.** 이유는 두 가지다.

      ① 한도를 넘겨 거부된 시도는 **주문이 아니라 입력 실수**다. 거래이력에 섞이면
         `SUM(gross_amount)` 같은 집계마다 `status != REJECTED` 를 달아야 한다
      ② 미리보기(`/api/orders/preview`)를 붙이면 참가자가 한도를 탐색하며 여러 번
         시도한다. 그때마다 행이 쌓이면 이력이 실패 로그가 된다

    `REJECTED` 는 **접수된 뒤 체결 단계에서 무너진 경우**를 위해 남겨 둔다.
    """
    # ① 계좌 잠금 — `transaction.atomic()` 안에서만 유효하다 (F-03 7.1)
    account = _lock_account(account.pk)

    if account.is_frozen:
        raise OrderRejected(
            "ACCOUNT_FROZEN", "account", "동결된 계좌라 주문할 수 없습니다."
        )

    asset_class = account.asset_class
    is_contest = account.is_contest
    pending_open = False

    # ② 대회 규칙 — 참가 상태 · 거래 시간 · 종목
    context = None
    if is_contest:
        participation = contest_rules.resolve_participation(account)
        contest_rules.check_participation(participation)
        context = contest_rules.RuleContext.load(participation.contest, symbol)
        contest_rules.check_symbol_tradable(context, symbol, side)
        try:
            contest_rules.check_session()
        except contest_rules.MarketClosed:
            # ★ **거부가 아니다.** 장외 주문은 접수해 두고 다음 장 시작에 처리한다
            #   (F-03 8장). 잡 5 `open_market` 이 가져간다.
            pending_open = True

    # ③ 시세·호가 조달
    quote = market_quotes.get_quote(asset_class, symbol, for_contest=is_contest)
    book = _load_book_if_needed(symbol, is_contest, pending_open)

    # ④ 지정가 결정 — 호가가 없으면 여기서 PriceUnavailable 이 나고, 그건 거부가 아니다
    resolved_limit = None
    price_unavailable: PriceUnavailable | None = None
    if not pending_open:
        try:
            resolved_limit = resolve_limit_price(
                order_type=order_type, side=side, price_level=price_level,
                limit_price=limit_price, book=book, quote=quote,
            )
        except PriceUnavailable as exc:
            if not exc.recoverable:
                raise
            # 체결은 못 하지만 접수는 한다 (F-16 3.4)
            price_unavailable = exc

    # ★★ **대회는 호가 없이 체결하지 않는다** ────────────────────────────────
    #
    #   호가가 없다고 현재가로 전량 체결해 버리면 **연습 모드와 똑같아진다.**
    #   대회를 대회로 만드는 것이 호가 소진(시장 충격)이므로, 호가가 없으면
    #   체결을 미루는 편이 맞다. 주문은 접수된 채로 남아 다음 폴링이 가져간다.
    #   (`_match_for` 에도 같은 방어가 한 겹 더 있다 — 이 경로를 못 지나쳐도
    #    현재가 체결로 새지 않게.)
    if is_contest and not pending_open and book is None and price_unavailable is None:
        price_unavailable = PriceUnavailable(
            symbol,
            "호가를 아직 받지 못했습니다. 잠시 후 자동으로 체결을 다시 시도합니다.",
            recoverable=True,
        )

    if order_type == OrderType.STOP and (stop_price is None or stop_price <= 0):
        raise OrderRejected("INVALID_ORDER", "stop_price", "STOP 가격을 입력해 주십시오.")

    # ⑤ 수량 환산 — 기준가는 결정된 지정가, 없으면 현재가 (F-03 3.1)
    base_price = resolved_limit or quote.price
    portfolio = _build_portfolio(account)
    requested_qty = _resolve_qty(
        account=account, portfolio=portfolio, symbol=symbol, side=side,
        asset_class=asset_class, is_contest=is_contest, base_price=base_price,
        qty=qty, weight_pct=weight_pct, amount_krw=amount_krw,
    )

    # ⑥ 잔고·보유 검증 — **수수료를 포함한 금액**으로 본다 (F-03 7.3)
    estimated_gross = int(
        (requested_qty * base_price).to_integral_value(rounding=ROUND_DOWN)
    )
    _check_affordable(account, side, estimated_gross, requested_qty, symbol)

    # ⑦ 편입 한도 — 매수만 (F-04 3장)
    if is_contest and context is not None:
        contest_rules.check_limits(
            context, portfolio, symbol=symbol, side=side, amount_krw=estimated_gross
        )

    # ⑧ 접수
    now = timezone.now()
    order = Order.objects.create(
        account=account,
        symbol=symbol,
        asset_class=asset_class,
        side=side,
        order_type=order_type,
        price_level=price_level if order_type in (OrderType.RELATIVE, OrderType.OWN) else None,
        limit_price=resolved_limit,
        stop_price=stop_price,
        requested_weight_pct=weight_pct,
        requested_qty=requested_qty,
        status=OrderStatus.PENDING_OPEN if pending_open else OrderStatus.ACCEPTED,
        source=source,
        accepted_at=now,
    )

    # ⑨ 즉시 체결 시도
    #
    # ★ 체결하지 않는 경우가 셋이다. 전부 **정상 상태**이고 거부가 아니다.
    #   · 장외 접수(PENDING_OPEN)   → 잡 5 가 장 시작에 처리
    #   · STOP 주문                 → 발동 조건이 올 때까지 대기 (F-03 4.2)
    #   · 호가 조달 실패            → 잡 3 이 다음 폴링에서 처리 (F-16 3.4)
    if not pending_open and order_type != OrderType.STOP and price_unavailable is None:
        result = _match_for(
            order=order, book=book, quote=quote,
            limit_price=resolved_limit, qty=requested_qty, is_contest=is_contest,
        )
        if result.fills:
            apply_fills(order, result, at=now)

    _register_polling(order)
    return order


def _load_book_if_needed(symbol: str, is_contest: bool, pending_open: bool) -> Orderbook | None:
    """대회 모드에서만 호가를 읽는다 (F-16 2.5 각주).

    연습 모드는 현재가 기준 즉시 체결이라 KIS 를 호출할 일이 없고, **이것이 유량
    부담을 크게 줄인다.** 장외 접수는 어차피 체결하지 않으므로 읽지 않는다.

    호가가 없어도 여기서는 터뜨리지 않는다 — `None` 을 돌려주고, 그것이 문제가 되는
    지점(`resolve_limit_price`)에서 `PriceUnavailable` 로 드러나게 한다.
    """
    if not is_contest or pending_open:
        return None
    try:
        return market_quotes.get_orderbook(symbol)
    except PriceUnavailable:
        return None


def _match_for(
    *, order: Order, book: Orderbook | None, quote: Quote,
    limit_price: Decimal | None, qty: Decimal, is_contest: bool,
) -> FillResult:
    """이 주문에 맞는 체결 방식을 고른다.

    | 모드 | 방식 | 근거 |
    |---|---|---|
    | **대회** | 호가 10단계 소진 + 가정 잔량 | F-03 5.1 — 시장 충격을 재현한다 |
    | **연습** | 현재가 전량 즉시 | F-03 5.2 주석 — v1.0 동작을 그대로 유지한다 |

    ★ 연습 모드의 지정가 주문은 **조건이 맞을 때만** 체결한다. 안 맞으면 빈 결과를
      돌려주고 주문은 `ACCEPTED` 로 남아 추종 체결 대상이 된다.
    """
    if is_contest:
        # 호가 없이 대회 체결을 하지 않는다 — `place_order` 의 같은 이름 주석 참조.
        if book is None:
            return FillResult(remaining_qty=qty)
        return match_orderbook(
            book=book, side=order.side, limit_price=limit_price, qty=qty,
            quote=quote, asset_class=order.asset_class,
        )

    if limit_price is not None and not _price_ok(order.side, quote.price, limit_price):
        return FillResult(remaining_qty=qty)
    return match_at_price(price=quote.price, qty=qty)


def _resolve_qty(
    *, account, portfolio, symbol, side, asset_class, is_contest,
    base_price: Decimal, qty, weight_pct, amount_krw,
) -> Decimal:
    """주문 수량을 정한다.

    | 모드 | 입력 | 환산 |
    |---|---|---|
    | **대회** | `weight_pct` (순자산 대비 %) | `floor(순자산 × 비중 / 100 / 기준가)` |
    | 연습 | `qty` 또는 `amount_krw` | 그대로 / `floor(금액 / 기준가)` |

    ★★ **매도도 순자산 대비 %다** (F-03 3.1) — 보유 수량 대비가 아니다.
      타임폴리오와 같다. 환산 결과가 보유분을 넘으면 **보유 전량으로 잘라낸다.**
      거부하지 않는 이유는 "가진 것 다 팔기"가 참가자의 의도일 때가 많고,
      비중 환산은 어차피 근사이기 때문이다.
    """
    if base_price <= 0:
        raise OrderRejected("PRICE_UNAVAILABLE", "symbol", "기준가를 계산할 수 없습니다.")

    if is_contest:
        if weight_pct is None or weight_pct <= 0:
            raise OrderRejected(
                "INVALID_ORDER", "weight_pct", "주문 비중(%)을 입력해 주십시오."
            )
        target = Decimal(portfolio.total_asset) * Decimal(weight_pct) / 100
        resolved = _round_qty(target / base_price, asset_class)
    elif qty is not None:
        resolved = _round_qty(Decimal(qty), asset_class)
    elif amount_krw is not None:
        resolved = _round_qty(Decimal(amount_krw) / base_price, asset_class)
    else:
        raise OrderRejected("INVALID_ORDER", "qty", "수량 또는 금액을 입력해 주십시오.")

    if resolved <= 0:
        raise OrderRejected(
            "INVALID_ORDER", "qty",
            f"주문 수량이 0 입니다 (기준가 {base_price:,.0f}원). 비중이나 금액을 늘려 주십시오.",
            {"base_price": str(base_price)},
        )

    if side == OrderSide.SELL:
        held = (
            Position.objects.filter(account=account, symbol=symbol)
            .values_list("qty", flat=True).first()
            or Decimal(0)
        )
        if held <= 0:
            raise OrderRejected(
                "INSUFFICIENT_POSITION", "qty", "보유하고 있지 않은 종목입니다."
            )
        resolved = min(resolved, held)

    return resolved


def _check_affordable(account, side, estimated_gross, qty, symbol) -> None:
    """⑤ 잔고 검증 — **수수료를 포함한 금액**으로 본다 (F-03 7.3).

    v1.0 은 수수료가 없어 체결금액만 봤다. 수수료를 빼놓고 검증하면 체결 직전에
    현금이 모자라 `apply_fills` 에서 터진다 — 그건 사용자에게 설명할 수 없는 실패다.
    """
    if side != OrderSide.BUY:
        return
    fee_bp, tax_bp = cost_rates(account)
    fee, _ = calc_cost(estimated_gross, side, fee_bp, tax_bp)
    required = estimated_gross + fee
    if account.cash < required:
        raise OrderRejected(
            "INSUFFICIENT_CASH",
            "weight_pct",
            f"현금이 부족합니다. 필요 {required:,}원 (수수료 {fee:,}원 포함) · "
            f"보유 {account.cash:,}원",
            {
                "required_krw": required,
                "cash": account.cash,
                "fee_krw": fee,
                "shortfall_krw": required - account.cash,
            },
        )


def _build_portfolio(account: Account) -> contest_rules.Portfolio:
    """규칙 판정용 계좌 스냅샷 (F-04 3장).

    ★ **평가액은 현재가로 낸다.** 평단으로 내면 오른 종목의 비중이 실제보다 작게
      보여 한도를 넘겨 살 수 있다. 한도는 "지금 얼마어치를 들고 있는가" 의 규칙이다.

    ★ 시세를 못 얻은 종목은 **평단으로 대신한다.** 그 종목을 0 으로 두면 순자산이
      줄어 다른 종목의 비중이 부풀고, 엉뚱한 종목이 한도에 걸린다.
    """
    positions = list(Position.objects.filter(account=account))
    if not positions:
        return contest_rules.Portfolio(cash=account.cash, holdings=[])

    prices = market_quotes.get_quotes(
        account.asset_class, [item.symbol for item in positions]
    )
    holdings = []
    for item in positions:
        quote = prices.get(item.symbol)
        price = quote.price if quote else item.avg_price
        value = int((item.qty * price).to_integral_value(rounding=ROUND_DOWN))
        holdings.append(contest_rules.Holding(symbol=item.symbol, value=value))
    return contest_rules.Portfolio(cash=account.cash, holdings=holdings)


def _register_polling(order: Order) -> None:
    """미체결로 남은 주문의 종목을 **시세 폴링 우선순위 1** 에 올린다 (F-16 2.5).

    호가가 5초마다 갱신돼야 추종 체결이 의미를 갖는다. 등록하지 않으면 그 종목은
    우선순위 4(캐시 만료 시에만)로 남아 **체결이 하염없이 미뤄진다.**

    ★★ **호가가 필요한지도 함께 알려준다** (세션 10 · 변경노트 E-44) ────────────

    호가는 대회 모드에서만 쓴다 (F-16 2.5). `market` 앱은 계좌 모드를 모르므로
    (규약 1.1 — market 은 다른 앱을 참조하지 않는다) **여기서 판정해 넘긴다.**
    넘기지 않으면 잡 2 가 연습 종목까지 KIS 로 호가를 받아 유량을 태운다.
    """
    if order.status not in (OrderStatus.ACCEPTED, OrderStatus.PARTIAL, OrderStatus.PENDING_OPEN):
        return
    market_quotes.mark_priority(
        order.asset_class,
        order.symbol,
        priority=1,
        reason="OPEN_ORDER",
        # 대회 주식 주문만 실호가로 체결한다. 연습은 현재가 기준이라 호가가 없어도 된다.
        needs_orderbook=order.account.is_contest and order.asset_class == AssetClass.STOCK,
    )


# ─────────────────────────────────────────────────────────────────
# 6. 추종 체결 — 잡 3 이 부른다 (F-03 5.2 · F-20 잡 3)
# ─────────────────────────────────────────────────────────────────


def fill_open_order(order: Order, quote: Quote, book: Orderbook | None = None) -> bool:
    """미체결 주문 하나를 시장 체결에 맞춰 채운다.

    ```
    매수 주문: 시장 체결가 ≤ 내 지정가  →  그 가격으로 체결
    매도 주문: 시장 체결가 ≥ 내 지정가  →  그 가격으로 체결
    STOP 주문: 발동 조건 확인          →  시장가로 전환해 체결
    ```

    Returns:
        체결이 일어났으면 `True`.

    호출 전제: `transaction.atomic()` 안이고 `order.account` 가 잠겨 있어야 한다.

    ★★ **멱등성 — 이 함수의 안전은 상태 전이가 보장한다** (F-20 6장) ──────────

    잡은 5초마다 돌고, 같은 주문을 몇 번이고 다시 본다. `FILLED` 주문은 애초에
    스캔 대상이 아니고(`OPEN_ORDER_STATUSES`), 부분 체결된 주문은 **남은 잔량만**
    처리하므로 두 번 돌아도 결과가 같다. 별도의 멱등키가 필요 없는 이유다.

    ★ **추종 체결은 호가를 소진하지 않고 현재가에 전량 채운다** (F-03 5.2).
      즉시 체결(5.1)과 다른 점이고, 의도된 차이다 — 5.2 는 "시장에서 그 가격에
      거래가 일어났다" 는 사실을 따라가는 것이라 이미 체결된 가격을 그대로 쓴다.
    """
    remaining = order.requested_qty - order.filled_qty
    if remaining <= 0:
        return False

    is_stop = order.order_type == OrderType.STOP
    if is_stop:
        if not _stop_triggered(order, quote.price):
            return False
        # ★ 발동했다. **`order_type` 은 STOP 그대로 둔다** — 참가자가 낸 주문이
        #   STOP 이었다는 사실은 이력에 남아야 한다. 바뀌는 것은 *체결 방식*이지
        #   *주문의 정체*가 아니다 (F-03 4.2).
        limit_price = None
    else:
        limit_price = order.limit_price
        if limit_price is not None and not _price_ok(order.side, quote.price, limit_price):
            return False

    # STOP 발동은 시장가라 대회에서는 호가를 소진한다. 그 외에는 현재가 전량.
    if is_stop and book is not None:
        result = match_orderbook(
            book=book, side=order.side, limit_price=None, qty=remaining,
            quote=quote, asset_class=order.asset_class,
        )
    else:
        result = match_at_price(price=quote.price, qty=remaining)

    if not result.fills:
        return False

    apply_fills(order, result)
    return True


def _stop_triggered(order: Order, price: Decimal) -> bool:
    """STOP 발동 조건 (F-03 4.2).

        매도 STOP: 현재가 ≤ STOP 가격  → 손절
        매수 STOP: 현재가 ≥ STOP 가격  → 추격 매수
    """
    if order.stop_price is None:
        return False
    if order.side == OrderSide.SELL:
        return price <= order.stop_price
    return price >= order.stop_price


# ─────────────────────────────────────────────────────────────────
# 7. 취소 (A-03 4장)
# ─────────────────────────────────────────────────────────────────


class OrderNotCancellable(Exception):
    """이미 끝난 주문은 취소할 수 없다 — 409 로 응답한다."""


@transaction.atomic
def cancel_order(order: Order, *, reason: str = "") -> Order:
    """미체결 잔량을 소멸시킨다.

    | 상태 | 결과 |
    |---|---|
    | `ACCEPTED` · `PARTIAL` · `PENDING_OPEN` | 취소. 잔량 소멸 |
    | `FILLED` · `CANCELLED` · `REJECTED` | `OrderNotCancellable` (409) |

    ★ **부분 체결된 주문을 취소해도 체결된 부분은 되돌리지 않는다.** 이미 현금과
      포지션이 움직였고, 그것을 되돌리는 것은 취소가 아니라 반대매매다.
      `filled_qty` 는 그대로 두고 상태만 `CANCELLED` 로 바꾼다.

    ★ 장 마감 시 미체결 주문 자동 취소(잡 6 `close_market`)도 이 함수를 쓴다.
      **다음 날로 넘기지 않는다** — 밤새 뉴스로 상황이 바뀐 뒤 옛 지정가가
      체결되는 문제를 막는다 (F-03 2장).
    """
    order = Order.objects.select_for_update().get(pk=order.pk)
    if order.status not in (
        OrderStatus.ACCEPTED, OrderStatus.PARTIAL, OrderStatus.PENDING_OPEN
    ):
        raise OrderNotCancellable(
            f"{order.get_status_display()} 상태의 주문은 취소할 수 없습니다."
        )

    order.status = OrderStatus.CANCELLED
    order.cancelled_at = timezone.now()
    if reason:
        order.reject_message = reason[:200]
    order.save(update_fields=["status", "cancelled_at", "reject_message"])
    return order
