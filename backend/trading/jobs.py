"""잡 3 — 미체결 주문 시장 체결 추종 + STOP 발동 (F-20 잡 3 · F-03 5.2).

**F-03 의 절충안을 성립시키는 잡이다.** 비동기 워커·큐·재시도를 만들지 않는 대신,
이미 도는 폴링에 체결 추종을 얹는다.

    5초마다:
      ① PARTIAL / ACCEPTED 주문을 훑는다
      ② 시장 체결가가 내 지정가 조건을 넘었으면 그 가격으로 채운다
      ③ STOP 발동 조건을 확인해 시장가로 전환한다

★★ **주기는 코드에 박혀 있지 않다** ─────────────────────────────────────────

F-20 2장은 5초로 적었지만, **실행처에 따라 감당할 수 있는 주기가 다르다.**
`AppSetting("trading.match_interval_seconds")` 로 빼 두었으므로:

    로컬 상시기동   `manage.py match_pending_orders --loop`  →  5초로 돌린다
    pg_cron + 서버리스  cron 스케줄이 주기를 정한다          →  30초로 늦춘다

**어느 쪽이든 이 파일은 고치지 않는다.** 잡은 자기가 어디서 도는지 모른다
(F-16 5.3 · 변경노트 E-28).

★★ **`job_run(coalesce_idle=True)` 인 이유** ────────────────────────────────

장중 6.5시간을 5초로 나누면 하루 4,680회다. 그중 체결이 일어나는 것은 손에 꼽는다.
매 실행을 `DataSyncLog` 행으로 남기면 이력 화면이 무변화 행으로 덮여 **정작 봐야 할
실패가 묻힌다.** 조용한 실행은 직전 행의 `finished_at` 만 밀어 한 구간으로 합친다
(→ `core/jobs.py` 의 `job_run` docstring · 변경노트 E-37).
"""

import logging
from collections import defaultdict
from typing import Callable

from django.db import transaction

from accounts.models import Account
from core.constants import TriggeredBy
from core.jobs import SyncResult, job_run
from core.models import AppSetting
from market import quotes as market_quotes
from market.quotes import PriceUnavailable, Quote
from market.sessions import is_market_open
from trading.models import Order, OrderStatus, OrderType
from trading.services import fill_open_order

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]

# 주기 설정 (모듈 docstring 참조). 값이 없으면 F-20 2장의 5초를 쓴다.
INTERVAL_SETTING_KEY = "trading.match_interval_seconds"
DEFAULT_INTERVAL_SECONDS = 5

# 한 번에 훑을 주문 수 상한.
#
# ★ 상한을 두는 이유 — 잡이 5초마다 도는데 한 회차가 5초를 넘기면 **다음 회차와
#   겹친다.** 미체결이 수천 건 쌓인 상황(대회 첫날 개장 직후)에서도 회차가 끝나야
#   하므로 끊어서 처리하고, **끊었다는 사실을 `notes` 에 남긴다** — 조용히 자르면
#   "다 처리했다"로 읽힌다.
MAX_ORDERS_PER_RUN = 500


def _noop(_message: str) -> None:
    """`on_progress` 를 안 넘겼을 때 쓰는 빈 콜백."""


def match_interval_seconds() -> int:
    """폴링 주기(초). 커맨드의 `--loop` 와 pg_cron 등록 SQL 이 함께 참조한다."""
    raw = AppSetting.objects.filter(key=INTERVAL_SETTING_KEY).values_list(
        "value", flat=True
    ).first()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SECONDS
    return max(value, 1)


def match_pending_orders(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """미체결 주문을 시장 체결에 맞춰 채운다.

    Returns:
        `SyncResult` — `created` = 이번에 **전량 체결**된 주문 수,
        `updated` = **부분 체결**로 남은 주문 수, `skipped` = 조건 미달로 건너뛴 수.

    ★ `created`/`updated` 를 이렇게 읽는 것은 이 잡만의 해석이다. 적재 잡에서는
      "새 행/갱신 행"이지만 여기서는 행을 만드는 게 아니라 **주문을 끝내거나 진행**한다.
      `notes` 에 문장으로 함께 남겨 숫자만 보고 오해하지 않게 한다.
    """
    result = SyncResult()

    # ★ `coalesce_idle=True` — 조용한 실행은 새 행을 만들지 않는다 (모듈 docstring).
    with job_run(
        "match_pending_orders", triggered_by, dry_run=dry_run, coalesce_idle=True
    ) as record:
        _run(result=result, dry_run=dry_run, on_progress=on_progress)
        record["rows"] = result.rows

    return result


def _run(*, result: SyncResult, dry_run: bool, on_progress: ProgressFn) -> None:
    """본체. `job_run` 이 전체를 감싸도록 분리했다 (market/services.py 와 같은 이유)."""
    market_open = is_market_open()

    orders = list(
        Order.objects.filter(
            status__in=[OrderStatus.ACCEPTED, OrderStatus.PARTIAL]
        )
        .select_related("account", "account__contest")
        .order_by("created_at")[: MAX_ORDERS_PER_RUN + 1]
    )

    truncated = len(orders) > MAX_ORDERS_PER_RUN
    if truncated:
        orders = orders[:MAX_ORDERS_PER_RUN]
        result.note(
            f"미체결 주문이 {MAX_ORDERS_PER_RUN}건을 넘어 오래된 것부터 잘라 처리했습니다 — "
            f"나머지는 다음 회차가 가져갑니다"
        )

    if not orders:
        return

    # ★★ **대회 주문은 장중에만 체결한다** ────────────────────────────────
    #
    #   pg_cron 스케줄을 장중으로 좁혀 등록하더라도, 로컬 `--loop` 는 24시간 돈다.
    #   **잡이 스스로 판단**하지 않으면 밤에 옛 종가로 대회 주문이 체결된다.
    #   연습 모드는 상시 거래라 그대로 처리한다 (F-03 8장).
    if not market_open:
        before = len(orders)
        orders = [order for order in orders if not order.account.is_contest]
        skipped = before - len(orders)
        if skipped:
            result.skipped += skipped
            on_progress(f"장외 — 대회 주문 {skipped}건은 건너뜁니다")
        if not orders:
            return

    # ── 시세를 자산군별로 한 번에 읽는다 (N+1 방지) ─────────────────
    by_class: dict[str, set[str]] = defaultdict(set)
    for order in orders:
        by_class[order.asset_class].add(order.symbol)

    quotes: dict[tuple[str, str], Quote] = {}
    for asset_class, symbols in by_class.items():
        for symbol, quote in market_quotes.get_quotes(asset_class, sorted(symbols)).items():
            quotes[(asset_class, symbol)] = quote

    on_progress(
        f"미체결 {len(orders)}건 · 시세 {len(quotes)}종목"
        + ("" if market_open else " (장외 — 연습 주문만)")
    )

    # ── 계좌별로 묶어 처리한다 ────────────────────────────────────
    #
    # ★ 한 계좌에 미체결이 여러 건이면 계좌 행을 그만큼 여러 번 잠그게 된다.
    #   묶어서 한 트랜잭션에 처리하면 잠금이 한 번이고, **같은 계좌의 주문들이
    #   현금을 나눠 쓰는 순서도 결정적**이 된다 (오래된 주문부터).
    grouped: dict[int, list[Order]] = defaultdict(list)
    for order in orders:
        grouped[order.account_id].append(order)

    for account_id, account_orders in grouped.items():
        _process_account(
            account_id=account_id,
            orders=account_orders,
            quotes=quotes,
            result=result,
            dry_run=dry_run,
            on_progress=on_progress,
        )

    if result.rows:
        result.note(
            f"전량 체결 {result.created}건 · 부분 체결 {result.updated}건 "
            f"(이 잡의 created/updated 는 '행'이 아니라 '주문'을 셉니다)"
        )


def _process_account(
    *, account_id: int, orders: list[Order], quotes: dict, result: SyncResult,
    dry_run: bool, on_progress: ProgressFn,
) -> None:
    """한 계좌의 미체결 주문들을 한 트랜잭션에서 처리한다.

    ★ **계좌 하나가 실패해도 다른 계좌는 진행한다.** 전체를 한 트랜잭션에 묶으면
      주문 한 건의 문제로 그 회차가 통째로 롤백되고, 5초 뒤 같은 실패가 반복된다.
      실패는 로그로 남기고 다음 계좌로 간다 — 잡은 계속 돌아야 한다.
    """
    if dry_run:
        # 쓰지 않고 **체결될 것만 센다.** 조건 판정은 실제 실행과 같은 함수를 쓸 수
        # 없으므로(그 함수가 곧 쓰기다) 여기서는 가벼운 확인만 한다.
        for order in orders:
            quote = quotes.get((order.asset_class, order.symbol))
            if quote is not None and _would_fill(order, quote):
                result.updated += 1
            else:
                result.skipped += 1
        return

    try:
        with transaction.atomic():
            # 계좌를 잠근다 — `apply_fills` 의 전제다.
            # `of=("self",)` 인 이유는 `trading.services._lock_account` 주석 참조.
            account = (
                Account.objects.select_for_update(of=("self",))
                .select_related("contest")
                .get(pk=account_id)
            )
            for order in orders:
                quote = quotes.get((order.asset_class, order.symbol))
                if quote is None:
                    result.skipped += 1
                    continue
                if not _usable(quote, account.is_contest):
                    result.skipped += 1
                    continue

                # 잠긴 계좌 인스턴스를 주문에 물려 준다 — 그러지 않으면
                # `order.account` 가 잠기지 않은 별개 인스턴스를 가리켜
                # 현금 갱신이 서로를 덮어쓴다.
                order.account = account

                book = _book_for(order, account.is_contest)
                try:
                    # ★★ **주문마다 SAVEPOINT 를 둔다** ─────────────────────
                    #
                    #   중첩 `atomic()` 은 Django 에서 SAVEPOINT 가 된다. 이게 없으면
                    #   주문 하나가 `IntegrityError` 를 내는 순간 **바깥 트랜잭션이
                    #   통째로 깨져**, 예외를 잡아도 이후 쿼리가 전부
                    #   `TransactionManagementError` 로 실패한다. 같은 계좌의 나머지
                    #   주문이 그 한 건에 끌려 죽는다.
                    #
                    #   FastAPI + SQLAlchemy 에서 `session.begin_nested()` 를 쓰던
                    #   자리와 같다. Django 는 `atomic()` 하나로 둘 다 표현한다 —
                    #   가장 바깥이면 트랜잭션, 안쪽이면 SAVEPOINT.
                    with transaction.atomic():
                        filled = fill_open_order(order, quote, book)
                except Exception:       # noqa: BLE001 — 주문 하나가 회차를 죽이지 않게
                    logger.exception("주문 %s 체결 실패", order.pk)
                    result.skipped += 1
                    continue

                if not filled:
                    result.skipped += 1
                elif order.status == OrderStatus.FILLED:
                    result.created += 1
                else:
                    result.updated += 1
    except Exception:       # noqa: BLE001 — 계좌 하나가 잡을 죽이지 않게
        logger.exception("계좌 %s 체결 처리 실패", account_id)
        result.skipped += len(orders)


def _usable(quote: Quote, is_contest: bool) -> bool:
    """이 시세로 체결해도 되는가.

    ★ `get_quotes()` 는 잡을 위해 **예외를 던지지 않고** 값을 그대로 준다.
      대회에 걸어야 할 두 가지 제한(시뮬레이션 금지 · 5분 신선도)을 여기서 본다
      — 판정을 조회 함수에 넣으면 잡이 "왜 이 종목만 빠졌는지"를 알 수 없다.
    """
    if not is_contest:
        return True
    if quote.is_simulated:
        return False
    from django.utils import timezone       # noqa: PLC0415 — 지역 import 로 충분하다

    return timezone.now() - quote.fetched_at <= market_quotes.CONTEST_USABLE_AGE


def _book_for(order: Order, is_contest: bool):
    """STOP 발동 시 시장가 체결에 쓸 호가. 대회가 아니거나 STOP 이 아니면 필요 없다."""
    if not is_contest or order.order_type != OrderType.STOP:
        return None
    try:
        return market_quotes.get_orderbook(order.symbol)
    except PriceUnavailable:
        # 호가가 없으면 현재가 전량 체결로 떨어진다 (`fill_open_order` 참조).
        return None


def _would_fill(order: Order, quote: Quote) -> bool:
    """dry-run 판정 — 지금 돌면 이 주문이 체결될 것인가."""
    remaining = order.requested_qty - order.filled_qty
    if remaining <= 0:
        return False
    if order.order_type == OrderType.STOP:
        if order.stop_price is None:
            return False
        return (
            quote.price <= order.stop_price
            if order.side == "SELL"
            else quote.price >= order.stop_price
        )
    limit = order.limit_price
    if limit is None:
        return True
    return quote.price <= limit if order.side == "BUY" else quote.price >= limit


# ─────────────────────────────────────────────────────────────────
# 장 시작 · 장 마감 (F-20 잡 5 · 6) — 잡 3 과 같은 재료를 쓴다
# ─────────────────────────────────────────────────────────────────


def open_pending_orders(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """잡 5 `open_market` — `PENDING_OPEN` 주문을 장 시작에 접수 상태로 올린다.

    ★ **여기서 체결하지 않는다.** 상태만 `ACCEPTED` 로 바꾸고, 실제 체결은 잡 3 이
      이어서 가져간다. 두 잡이 같은 일을 나눠 갖는 편이 각각 단순하고,
      장 시작 직후 호가가 아직 안 채워졌을 때 **체결이 지연될 뿐 유실되지 않는다.**
    """
    result = SyncResult()
    with job_run("open_market", triggered_by, dry_run=dry_run) as record:
        pending = Order.objects.filter(status=OrderStatus.PENDING_OPEN)
        count = pending.count()
        on_progress(f"장 시작 대기 주문 {count}건")
        if not dry_run and count:
            from django.utils import timezone      # noqa: PLC0415

            result.updated = pending.update(
                status=OrderStatus.ACCEPTED, accepted_at=timezone.now()
            )
        elif dry_run:
            result.updated = count
        record["rows"] = result.rows
    return result


def cancel_stale_orders(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """잡 6 `close_market` — 장 마감 시 대회의 미체결 주문을 자동 취소한다.

    **다음 날로 넘기지 않는다** (F-03 2장). 넘기면 밤새 뉴스로 상황이 바뀐 뒤
    옛 지정가가 체결되는 문제가 생긴다.

    ★ **연습 계좌는 건드리지 않는다.** 연습은 상시 거래라 "장 마감"이 없다.
    """
    from trading.services import cancel_order       # noqa: PLC0415 — 순환 임포트 회피

    result = SyncResult()
    with job_run("close_market", triggered_by, dry_run=dry_run) as record:
        orders = list(
            Order.objects.filter(
                status__in=[OrderStatus.ACCEPTED, OrderStatus.PARTIAL],
                account__mode="CONTEST",
            ).select_related("account")
        )
        on_progress(f"장 마감 미체결 대회 주문 {len(orders)}건")
        for order in orders:
            if dry_run:
                result.updated += 1
                continue
            try:
                cancel_order(order, reason="장 마감 미체결 자동 취소")
                result.updated += 1
            except Exception:       # noqa: BLE001
                logger.exception("주문 %s 자동 취소 실패", order.pk)
                result.skipped += 1
        record["rows"] = result.rows

    if result.updated:
        result.note("미체결 잔량은 소멸했습니다. 체결된 부분은 그대로 남습니다 (F-03 2장)")
    return result


__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "cancel_stale_orders",
    "match_interval_seconds",
    "match_pending_orders",
    "open_pending_orders",
]
