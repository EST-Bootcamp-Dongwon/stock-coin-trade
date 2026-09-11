"""체결이 읽는 시세·호가 조달 계층 (F-16 2.3 · 3.3 · 3.4 · 4.1).

`market/services.py` 가 **바깥에서 안으로 채우는 쪽**(pykrx·업비트 배치)이라면,
이 모듈은 **안에서 꺼내 쓰는 쪽**이다. 체결 엔진(`trading/services.py`)이 가격을
얻는 유일한 입구이고, 그래서 여기 담기는 것은 조회 SQL 이 아니라 **판정**이다:

    이 값으로 체결해도 되는가?

세 가지를 본다.

| 판정 | 근거 |
|---|---|
| **신선한가** | 캐시 TTL 이 지났어도 5분 이내면 쓴다. 그 이상이면 못 쓴다 (F-16 3.4) |
| **진짜 값인가** | 시뮬레이션 가격으로는 **대회 체결을 하지 않는다** (F-16 4.1 · v1.0 승계) |
| **없으면 어떻게 되는가** | 주문을 거부하지 않는다. `ACCEPTED` 로 남겨 다음 폴링에 맡긴다 |

★★ **세 번째가 이 모듈에서 가장 중요한 결정이다** ────────────────────────────

우리 쪽 시세 파이프라인이 흔들렸다고 참가자의 주문을 `REJECTED` 로 만들면
**참가자가 우리 장애 때문에 매매 기회를 잃는다.** F-16 3.4 가 명시적으로 금지한다.

그래서 이 모듈은 "가격이 없다"를 **두 종류로 나눠** 던진다.

    PriceUnavailable(recoverable=True)   시세가 잠깐 없다  → 주문은 ACCEPTED 로 접수
    PriceUnavailable(recoverable=False)  이 종목은 값이 없다 → 400 으로 거부

앞의 것은 **시간이 지나면 해결되는 상태**이고, 뒤의 것은 **기다려도 안 되는 상태**다
(상장폐지 종목·오타 난 심볼). 둘을 뭉치면 호출부가 무엇을 해야 할지 알 수 없다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI + SQLAlchemy 에서는 이런 조회를 `crud.py` 에 두고 `Optional[Model]` 을
돌려주는 게 흔했다. 호출부가 `if quote is None:` 로 매번 분기했고, **그 분기를
빠뜨리면 `None.price` 로 터졌다.**

여기서는 `None` 을 돌려주지 않고 **예외를 던진다.** 값이 없는 것은 체결 경로에서
정상 흐름이 아니라 *중단해야 할 사건*이기 때문이다. 빠뜨릴 수가 없다.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from core.constants import AssetClass
from market.models import OrderbookCache, QuoteCache

logger = logging.getLogger(__name__)

# ★ 만료된 캐시를 **얼마나 오래된 것까지** 체결에 쓸 것인가 (F-16 3.4).
#
#   TTL(호가 5초 · 현재가 10초)은 "언제 갱신할까"의 기준이지 "언제 못 쓸까"의 기준이
#   아니다. KIS 가 잠깐 죽었을 때 5초 지났다고 체결을 멈추면 장애가 곧 거래 중단이 된다.
#   반대로 무한정 쓰면 30분 전 가격으로 체결하는 사고가 난다. 5분이 그 사이의 선이고
#   F-16 3.4 가 정한 값이다.
CONTEST_USABLE_AGE = timedelta(minutes=5)


class PriceUnavailable(Exception):
    """체결에 쓸 가격을 얻지 못했다.

    Attributes:
        symbol: 대상 종목.
        recoverable: **기다리면 해결되는가.**
            `True` 면 호출부는 주문을 거부하지 말고 `ACCEPTED` 로 남긴다 (F-16 3.4).
            `False` 면 이 종목은 애초에 값이 없다 — 거부해야 한다.
        message: 참가자에게 그대로 보여줄 수 있는 한국어 문장.
    """

    def __init__(self, symbol: str, message: str, *, recoverable: bool):
        super().__init__(message)
        self.symbol = symbol
        self.message = message
        self.recoverable = recoverable


@dataclass(frozen=True)
class Level:
    """호가 한 단계. 매도(ask)와 매수(bid)가 한 줄에 마주 본다.

    KIS 호가 응답이 이 형태라 캐시 JSON 도 같은 모양으로 저장한다
    (`OrderbookCache.levels` 의 help_text).
    """

    ask_price: Decimal
    ask_qty: Decimal
    bid_price: Decimal
    bid_qty: Decimal


@dataclass(frozen=True)
class Orderbook:
    """호가 10단계 스냅샷.

    `levels[0]` 이 최우선호가다 — 매도 1호가와 매수 1호가.
    """

    symbol: str
    levels: list[Level]
    fetched_at: object
    is_stale: bool          # TTL 은 지났지만 아직 쓸 수 있는 값인가

    def opposite(self, side: str) -> list[tuple[Decimal, Decimal]]:
        """**상대호가** — 내 주문을 받아 줄 쪽. `[(가격, 잔량), …]` 을 유리한 순서로.

        매수 주문은 매도호가(ask)를 먹고, 매도 주문은 매수호가(bid)를 먹는다.
        먹는 순서는 **나에게 유리한 쪽부터**다 — 매수는 싼 것부터, 매도는 비싼 것부터.

        캐시 JSON 의 배열 순서를 믿지 않고 여기서 정렬한다. 배열이 어떤 순서로 들어와도
        체결 결과가 같아야 하기 때문이다. **가격 0(빈 호가 단계)은 빼고 준다** —
        장 시작 직후나 거래정지 종목은 뒤쪽 단계가 비어 있다.
        """
        if side == "BUY":
            rows = [(lv.ask_price, lv.ask_qty) for lv in self.levels if lv.ask_price > 0]
            return sorted(rows, key=lambda row: row[0])              # 싼 매도부터
        rows = [(lv.bid_price, lv.bid_qty) for lv in self.levels if lv.bid_price > 0]
        return sorted(rows, key=lambda row: row[0], reverse=True)    # 비싼 매수부터

    def own(self, side: str) -> list[tuple[Decimal, Decimal]]:
        """**자기호가** — 내가 줄을 서는 쪽. 매수 주문이면 매수호가(bid)다.

        F-03 4장의 `OWN` 유형이 쓴다. 여기서 얻는 것은 *가격*뿐이고,
        그 가격에 실제로 체결되려면 상대가 와야 한다 — 소극적 주문인 이유다.
        """
        if side == "BUY":
            rows = [(lv.bid_price, lv.bid_qty) for lv in self.levels if lv.bid_price > 0]
            return sorted(rows, key=lambda row: row[0], reverse=True)
        rows = [(lv.ask_price, lv.ask_qty) for lv in self.levels if lv.ask_price > 0]
        return sorted(rows, key=lambda row: row[0])


@dataclass(frozen=True)
class Quote:
    """현재가 스냅샷. 연습 모드 체결과 STOP 발동 판정이 쓴다."""

    symbol: str
    price: Decimal
    prev_close: Decimal
    is_simulated: bool
    fetched_at: object
    is_stale: bool


# ─────────────────────────────────────────────────────────────────
# 1. 조회
# ─────────────────────────────────────────────────────────────────


def get_orderbook(symbol: str, *, usable_age: timedelta = CONTEST_USABLE_AGE) -> Orderbook:
    """대회 체결용 호가 10단계.

    Raises:
        PriceUnavailable: 캐시가 아예 없거나(`recoverable=True` — 아직 폴링이 닿지
            않은 종목일 수 있다) `usable_age` 를 넘도록 낡은 경우(`recoverable=True`).

    ★ **둘 다 `recoverable=True` 인 이유** — 호가가 없는 것은 종목의 문제가 아니라
      우리 폴링의 문제다. `SubscriptionRegistry` 우선순위 1(미체결 주문 종목)에
      올라가면 5초 뒤 채워진다 (F-16 2.5). 참가자를 벌할 일이 아니다.

    ★ **`OrderbookCache` 에는 `asset_class` 가 없다** — 호가는 대회 모드(주식) 전용이라
      키가 `symbol` 하나다 (E-04 2장). 코인 호가를 넣고 싶어지면 그때 키를 늘린다.
    """
    row = OrderbookCache.objects.filter(symbol=symbol).first()
    if row is None:
        raise PriceUnavailable(
            symbol,
            "호가를 아직 받지 못했습니다. 잠시 후 자동으로 체결을 다시 시도합니다.",
            recoverable=True,
        )

    age = timezone.now() - row.fetched_at
    if age > usable_age:
        # ★ 로그를 남긴다 — 이 상태가 이어지면 시세 파이프라인이 죽은 것이고,
        #   참가자에게는 "체결이 지연되고 있습니다" 만 보이므로 원인은 여기에만 남는다.
        logger.warning(
            "호가가 낡았습니다: %s — %.0f초 전 값 (허용 %.0f초)",
            symbol, age.total_seconds(), usable_age.total_seconds(),
        )
        raise PriceUnavailable(
            symbol,
            "시세 서버 지연으로 체결이 지연되고 있습니다. 주문은 접수된 상태로 유지됩니다.",
            recoverable=True,
        )

    return Orderbook(
        symbol=symbol,
        levels=[_to_level(item) for item in (row.levels or [])],
        fetched_at=row.fetched_at,
        is_stale=not row.is_fresh,
    )


def get_quote(asset_class: str, symbol: str, *, for_contest: bool) -> Quote:
    """현재가.

    Args:
        for_contest: 대회 체결에 쓸 값인가. **판정이 이 인자 하나로 갈린다.**

    | | 대회 (`True`) | 연습 (`False`) |
    |---|---|---|
    | 시뮬레이션 가격 | **거부** (F-16 4.1) | 허용 — 화면에 배지를 단다 |
    | 낡은 값 | 5분까지만 | 제한 없음 — 장외에는 최종 종가로 체결한다 (F-03 8장) |

    ★ **연습 모드에 신선도 제한을 두지 않는 이유** — 연습(주식)은 24시간 거래이고,
      장이 닫힌 동안의 "최근 값"은 정의상 종가다. 여기에 5분 제한을 걸면
      **밤에는 연습 거래가 통째로 막힌다.** v1.0 이 장외 종가 체결을 허용했고
      그 동작을 승계한다.

    Raises:
        PriceUnavailable: 값이 없거나(대회에서) 쓸 수 없는 값인 경우.
    """
    row = QuoteCache.objects.filter(asset_class=asset_class, symbol=symbol).first()
    if row is None:
        raise PriceUnavailable(
            symbol,
            "현재가를 아직 받지 못했습니다. 잠시 후 다시 시도해 주십시오.",
            recoverable=True,
        )

    if for_contest:
        if row.is_simulated:
            # ★ v1.0 의 판단을 그대로 가져온다 — **가짜 가격으로 주문이 나가지 않게 한다.**
            #   v1.0 은 이걸 503 으로 던졌고 코드에만 있었다. v2.0 은 사유를 문장으로
            #   돌려줘 화면에 그대로 띄운다 (관통 원칙 ②).
            raise PriceUnavailable(
                symbol,
                "이 종목은 시뮬레이션 가격만 있어 대회에서 체결할 수 없습니다.",
                recoverable=True,
            )
        age = timezone.now() - row.fetched_at
        if age > CONTEST_USABLE_AGE:
            logger.warning(
                "현재가가 낡았습니다: %s — %.0f초 전 값", symbol, age.total_seconds()
            )
            raise PriceUnavailable(
                symbol,
                "시세 서버 지연으로 체결이 지연되고 있습니다. 주문은 접수된 상태로 유지됩니다.",
                recoverable=True,
            )

    if row.price <= 0:
        # 가격 0 은 "아직 안 받음"과 구분되지 않는 값이다. 체결에 쓰면 0원 거래가 된다.
        raise PriceUnavailable(
            symbol, "현재가가 유효하지 않습니다 (0원).", recoverable=True
        )

    return Quote(
        symbol=symbol,
        price=row.price,
        prev_close=row.prev_close,
        is_simulated=row.is_simulated,
        fetched_at=row.fetched_at,
        is_stale=not row.is_fresh,
    )


def get_quotes(asset_class: str, symbols: list[str]) -> dict[str, Quote]:
    """여러 종목의 현재가를 **한 번에** 읽는다 — 체결 추종 잡(F-20 잡 3)이 쓴다.

    ★ 잡은 미체결 주문 수백 건을 훑는다. `get_quote()` 를 주문마다 부르면 쿼리가
      주문 수만큼 늘어난다(N+1). 종목 목록으로 한 번에 읽고 딕셔너리로 넘긴다.

    ★ **여기서는 예외를 던지지 않는다.** 잡은 "값이 있는 종목만 처리하고 나머지는
      다음 회차에 다시 본다" 가 맞는 동작이라, 한 종목이 비었다고 전체를 멈출 이유가 없다.
      값이 없는 종목은 결과 딕셔너리에서 그냥 빠진다.
    """
    rows = QuoteCache.objects.filter(asset_class=asset_class, symbol__in=symbols)
    return {
        row.symbol: Quote(
            symbol=row.symbol,
            price=row.price,
            prev_close=row.prev_close,
            is_simulated=row.is_simulated,
            fetched_at=row.fetched_at,
            is_stale=not row.is_fresh,
        )
        for row in rows
        if row.price > 0
    }


def _to_level(item: dict) -> Level:
    """캐시 JSON 한 줄 → `Level`.

    ★ **키가 없어도 터지지 않게 한다.** 이 JSON 은 외부(KIS) 응답에서 만들어지므로
      필드가 빠질 수 있고, 그때 `KeyError` 로 체결 전체가 멈추는 것보다
      **그 단계를 잔량 0 으로 보는 편**이 안전하다. 잔량 0 인 단계는 체결에서 건너뛴다.
    """
    return Level(
        ask_price=Decimal(str(item.get("ask_price") or 0)),
        ask_qty=Decimal(str(item.get("ask_qty") or 0)),
        bid_price=Decimal(str(item.get("bid_price") or 0)),
        bid_qty=Decimal(str(item.get("bid_qty") or 0)),
    )


# ─────────────────────────────────────────────────────────────────
# 2. 호가 단위 (틱)
# ─────────────────────────────────────────────────────────────────

# 유가증권·코스닥 공통 호가가격단위 (2023-01-25 개편 후 기준).
# `(이 가격 미만, 호가단위)` 오름차순. 마지막 항목이 상한 없는 구간이다.
#
# ★ **체결 엔진이 이걸 쓰는 곳은 한 군데다** — 지정가가 상대 10호가를 넘어갈 때
#   11호가부터를 **1틱 간격으로 가정**해 만들어 낸다 (F-03 5.1 4단계).
#   틱을 모르면 그 가정 자체를 세울 수 없다.
TICK_TABLE = [
    (2_000, 1),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
    (None, 1_000),
]


def tick_size(price: Decimal) -> Decimal:
    """그 가격대의 호가 단위(원).

    >>> tick_size(Decimal("74300"))
    Decimal('100')
    >>> tick_size(Decimal("1500"))
    Decimal('1')

    ★ 코인에는 적용하지 않는다. 업비트는 원화 마켓 가격대별 호가단위가 따로 있고,
      **연습 모드 코인은 호가창을 쓰지 않으므로**(현재가 전량 즉시 체결) 필요가 없다.
    """
    for upper, tick in TICK_TABLE:
        if upper is None or price < upper:
            return Decimal(tick)
    return Decimal(1_000)          # 도달하지 않지만 타입을 위해 남긴다


def price_limits(prev_close: Decimal) -> tuple[Decimal, Decimal]:
    """전일 종가로부터 상한가·하한가를 계산한다 (±30%, 호가단위로 절사).

    F-03 4.1 의 상·하한가 특례가 쓴다.

    ★★ **근사값이라는 사실을 분명히 해둔다** ────────────────────────────────

    실제 상·하한가는 **기준가**로 계산하는데, 기준가는 보통 전일 종가지만
    유상증자·액면분할·거래정지 후 재개 종목에서는 달라진다. 우리는 그 사건을
    추적하지 않으므로(`StockMaster` 에 기준가 컬럼이 없다) 전일 종가로 근사한다.

    그래서 이 값은 **체결가를 정하는 데 쓰지 않고, "상한가에 갇혔는가" 판정에만**
    쓴다. 판정이 빗나가도 손해는 "특례가 적용되지 않아 체결이 안 됨"이고,
    반대 방향(있지도 않은 가격에 체결)은 나지 않는다. **틀리는 방향이 안전한 쪽이다.**
    """
    if prev_close <= 0:
        return Decimal(0), Decimal(0)
    tick = tick_size(prev_close)
    upper = (prev_close * Decimal("1.3") // tick) * tick
    lower = (prev_close * Decimal("0.7") // tick) * tick
    return upper, lower


# ─────────────────────────────────────────────────────────────────
# 3. 폴링 우선순위 등록 (F-16 2.5)
# ─────────────────────────────────────────────────────────────────


def mark_priority(
    asset_class: str,
    symbol: str,
    priority: int,
    reason: str,
    *,
    needs_orderbook: bool = False,
) -> None:
    """이 종목을 시세 폴링 우선순위에 올린다.

    미체결 주문이 생기면 그 종목은 **우선순위 1(5초)** 이 되어야 한다 (F-16 2.5).
    체결 엔진이 주문을 `ACCEPTED` · `PARTIAL` 로 남길 때마다 부른다.

    Args:
        needs_orderbook: 이 종목이 **실호가로 체결되는가** (= 대회 종목인가).
            참이면 잡 2(`poll_orderbook`)가 KIS 유량을 써서 호가를 채운다.
            거짓이면 현재가만 갱신한다 — 연습 모드는 호가창을 쓰지 않는다.

            ★ `market` 은 "대회" 라는 말을 모른다. **부르는 쪽이 알려준다**
              (`SubscriptionRegistry.needs_orderbook` 주석 참조).

    ★ **우선순위를 낮추지는 않는다** — `priority` 가 이미 더 높은(숫자가 작은) 행이면
      그대로 둔다. 같은 종목을 여러 사람이 다른 이유로 보고 있을 수 있고,
      나중에 온 약한 이유가 강한 이유를 덮으면 폴링이 느려진다.

    ★★ **`needs_orderbook` 은 반대로 — 한 번 참이면 내리지 않는다** ────────────
      같은 종목을 대회 참가자와 연습 참가자가 동시에 주문할 수 있다. 나중에 온
      연습 주문이 `False` 로 덮으면 **대회 종목의 호가 갱신이 멈추고 체결이 선다.**
      우선순위와 같은 원리다 — 강한 요구가 이긴다.

    ★ 되돌리는 일(주문이 다 체결돼서 우선순위를 내리는 것)은 **여기서 하지 않는다.**
      폴링 잡이 "미체결 주문이 남아 있는 종목" 을 매번 다시 계산하는 편이 정확하다 —
      주문은 취소·만료 등 여러 경로로 사라지므로 낮추는 호출을 빠뜨리기 쉽다.
    """
    from market.models import SubscriptionRegistry      # noqa: PLC0415 — 순환 임포트 회피

    row, created = SubscriptionRegistry.objects.get_or_create(
        asset_class=asset_class,
        symbol=symbol,
        defaults={
            "priority": priority,
            "reason": reason,
            "needs_orderbook": needs_orderbook,
        },
    )
    if created:
        return

    updates = []
    if row.priority > priority:
        row.priority = priority
        row.reason = reason
        updates += ["priority", "reason"]
    if needs_orderbook and not row.needs_orderbook:
        row.needs_orderbook = True
        updates.append("needs_orderbook")
    if updates:
        row.save(update_fields=updates)
