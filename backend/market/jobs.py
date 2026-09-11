"""잡 1 `poll_quotes` · 잡 2 `poll_orderbook` — 시세 캐시를 채우는 쪽 (F-20 잡 1·2).

세션 9 까지 체결 엔진은 `QuoteCache` · `OrderbookCache` 를 **읽기만** 했다.
채우는 경로가 없어서 대회 주문은 영원히 `ACCEPTED` 로 남았다. 이 모듈이 그 구멍을 메운다.

    market/services.py   pykrx·업비트 **일배치**       느리고 · 하루 한 번 · 마스터
    market/jobs.py       KIS·네이버 **초 단위 폴링**   빠르고 · 예산이 빠듯 · 캐시  ← 여기
    market/quotes.py     체결이 **읽는** 쪽             판정만 한다

★★ **예산이 이 모듈의 모든 설계를 결정한다** ────────────────────────────────

KIS 는 초당 몇 건뿐이다(자료가 엇갈려 기본값을 보수적으로 둔다 — 변경노트 E-43).
전 종목 2,700개를 돌리는 건 불가능하고, 그래서 **필요한 것만** 갱신한다
(`SubscriptionRegistry` · F-16 2.5). 잡이 하는 일의 절반은 조회가 아니라
**무엇을 조회할지 고르는 것**이다.

    ① 우선순위 높은 순 · 오래 안 본 순으로 줄을 세운다
    ② 이번 회차 예산만큼 앞에서 자른다
    ③ 자른 사실을 `notes` 에 남긴다        ← 조용히 자르면 "다 갱신했다"로 읽힌다

★★ **3단 폴백은 "무엇을 캐시에 쓸 것인가" 의 문제다** (F-16 4장) ──────────────

    대회  KIS → 네이버 → **아무것도 쓰지 않는다**
    연습  네이버 → 시뮬레이션(수업용 14종에 한해, `is_simulated=True`)

마지막 칸이 핵심이다. **대회에서는 시뮬레이션 가격을 만들지 않는다.** 만들면
`market/quotes.py` 가 어차피 거부하지만(`for_contest=True`), 애초에 만들지 않는 편이
낫다 — 캐시 한 행을 대회와 연습이 공유하므로, 시뮬 값을 써 넣는 순간 **연습에는
가짜 값이 보이고 대회는 체결이 멈춘다.** 낡은 진짜 값을 남겨 두는 편이 둘 다에게 낫다.

★ **yfinance 는 쓰지 않는다** (2026-08-13 결정 · 변경노트 E-45).
  F-16 4장은 연습 1순위를 yfinance 로 적었지만, 종목당 1호출 + 5일 히스토리라
  폴링 잡에 맞지 않는다. 네이버 다종목 API 는 **40종을 1회**에 받는다.
  코드에는 지연 import 자리를 남겨 두었고, 설치돼 있으면 1순위로 끼어든다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 였다면 `asyncio.gather` 로 종목별 요청을 동시에 던졌을 것이다. 여기서는
**동시성이 오히려 해롭다** — 초당 한도가 있는 API 라 병렬로 던지면 그만큼 빨리
EGW00201 을 맞는다. 순차 + 전역 예산(`ApiCallBudget`)이 맞는 모양이다.
"""

import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from typing import Callable

from django.db import transaction
from django.utils import timezone

from core.constants import AssetClass, QuoteSource, TriggeredBy
from core.jobs import ExternalDataError, SyncResult, job_run
from market import kis, naver
from market.models import (
    OrderbookCache,
    QuoteCache,
    StockMaster,
    SubscriptionRegistry,
)

logger = logging.getLogger(__name__)

ProgressFn = Callable[[str], None]


def _noop(_message: str) -> None:
    """`on_progress` 를 안 넘겼을 때 쓰는 빈 콜백."""


def _oldest_first():
    """`last_polled_at` 오름차순 정렬식 — **한 번도 폴링되지 않은 종목이 맨 앞**.

    ★★ **`nulls_first` 를 명시하는 것이 핵심이다** ─────────────────────────────

    Postgres 는 오름차순에서 NULL 을 **마지막**에 둔다. 그대로 두면 `last_polled_at`
    이 NULL 인 종목(= 방금 등록된 새 관심 종목)이 **줄의 맨 뒤로 밀린다.** 상한
    (`MAX_*_PER_RUN`)에 걸리면 그 종목은 영원히 차례가 오지 않고, 참가자가 방금
    낸 주문의 시세만 갱신되지 않는 상태가 된다. 증상이 조용해서 더 위험하다.

    ★ 두 잡이 같은 정렬을 쓰므로 함수로 뺀다. 한쪽만 고치면 어긋난다.
    """
    from django.db.models import F      # noqa: PLC0415 — 지역 import 로 충분하다

    return F("last_polled_at").asc(nulls_first=True)


# ── TTL (F-16 2.3) ──────────────────────────────────────────────
#
# ★ TTL 은 "언제 갱신할까" 의 기준이지 "언제 못 쓸까" 의 기준이 아니다.
#   못 쓰는 기준은 `market/quotes.py` 의 `CONTEST_USABLE_AGE`(5분)다.
QUOTE_TTL = timedelta(seconds=10)
ORDERBOOK_TTL = timedelta(seconds=5)

# 한 회차에 처리할 종목 수 상한.
#
# ★ 예산과 별개로 상한을 두는 이유 — 예산은 "1초에 몇 건" 이고 이 잡은 여러 초에
#   걸쳐 돌 수 있다. 상한이 없으면 구독 종목이 늘어난 만큼 한 회차가 길어져
#   **다음 회차와 겹친다.** 잡 3 의 `MAX_ORDERS_PER_RUN` 과 같은 취지다.
MAX_ORDERBOOK_PER_RUN = 20
MAX_QUOTES_PER_RUN = 120


# ─────────────────────────────────────────────────────────────────
# 1. 잡 2 — poll_orderbook (KIS 전용)
# ─────────────────────────────────────────────────────────────────


def poll_orderbook(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """대회 종목의 호가 10단계를 KIS 로 갱신한다 (F-20 잡 2 · 장중 5초).

    Returns:
        `SyncResult` — `created` = 처음 채운 종목, `updated` = 갱신한 종목,
        `skipped` = 예산·상한에 밀렸거나 조회에 실패한 종목.

    ★ **KIS 말고는 방법이 없다.** 10단계 호가를 주는 무료 경로가 달리 없어서
      폴백이 없다. KIS 가 죽으면 호가는 낡고, 5분이 지나면
      `market/quotes.py` 가 체결을 보류한다 (F-16 3.4) — **주문을 거부하지는 않는다.**

    ★ `coalesce_idle=True` — 장중 5초 잡이라 조용한 회차가 대부분이다
      (`core/jobs.py` 의 `job_run` docstring · 변경노트 E-37).
    """
    result = SyncResult()
    with job_run("poll_orderbook", triggered_by, dry_run=dry_run, coalesce_idle=True) as record:
        _run_orderbook(result=result, dry_run=dry_run, on_progress=on_progress)
        record["rows"] = result.rows
    return result


def _run_orderbook(*, result: SyncResult, dry_run: bool, on_progress: ProgressFn) -> None:
    """`poll_orderbook` 의 본체."""
    if not kis.is_configured():
        # ★ **예외를 던지지 않는다.** 자격증명 미설정은 장애가 아니라 상태다.
        #   5초 잡이 매번 FAILED 를 남기면 `has_consecutive_failures()` 가 잡을 끄고
        #   Admin 경고 배너가 이 한 줄로 도배된다. 한 번 알리고 조용히 끝낸다.
        result.note(
            "KIS 자격증명이 없어 호가를 갱신하지 않았습니다 — 대회 주문은 접수 상태로 "
            "남습니다(거부되지 않습니다). backend/.env 의 KIS_APP_KEY · KIS_APP_SECRET 을 "
            "채우고 `manage.py kis_probe` 로 확인하십시오."
        )
        on_progress("KIS 미설정 — 건너뜁니다")
        return

    targets = list(
        SubscriptionRegistry.objects
        .filter(needs_orderbook=True, asset_class=AssetClass.STOCK)
        # 우선순위 높은 순 → 오래 안 본 순. `last_polled_at` 이 NULL(한 번도 안 봄)이
        # 가장 먼저 와야 하므로 `nulls_first` 를 명시한다. Postgres 의 기본은
        # 오름차순에서 NULL 이 **마지막**이라, 두지 않으면 새 종목이 뒤로 밀린다.
        .order_by("priority", _oldest_first())
        [: MAX_ORDERBOOK_PER_RUN + 1]
    )
    truncated = len(targets) > MAX_ORDERBOOK_PER_RUN
    if truncated:
        targets = targets[:MAX_ORDERBOOK_PER_RUN]
        result.note(
            f"호가 대상이 {MAX_ORDERBOOK_PER_RUN}종을 넘어 잘라 처리했습니다 — "
            f"나머지는 다음 회차가 가져갑니다"
        )

    if not targets:
        return

    on_progress(f"호가 갱신 대상 {len(targets)}종")
    if dry_run:
        result.updated = len(targets)
        return

    config = kis.load_config()
    fetched: list[tuple[SubscriptionRegistry, kis.KisOrderbook]] = []

    # ── ① 외부에서 받는다 (트랜잭션 밖 — market/services.py 규약) ──
    for row in targets:
        try:
            fetched.append((row, kis.fetch_orderbook(row.symbol, config=config)))
        except ExternalDataError as exc:
            # ★ 한 종목의 실패가 회차를 죽이지 않는다. 다른 종목은 계속 받는다.
            #   실패한 종목의 캐시는 **건드리지 않는다** — 낡은 값이 남아 있어야
            #   5분 안에는 체결이 이어진다 (F-16 3.4).
            result.skipped += 1
            logger.warning("호가 조회 실패: %s — %s", row.symbol, exc)
            on_progress(f"⚠ {row.symbol} 호가 실패 — {exc}")

    if not fetched:
        return

    # ── ② 한 번에 쓴다 (트랜잭션 안) ─────────────────────────────
    now = timezone.now()
    existing = set(
        OrderbookCache.objects
        .filter(symbol__in=[row.symbol for row, _ in fetched])
        .values_list("symbol", flat=True)
    )

    with transaction.atomic():
        OrderbookCache.objects.bulk_create(
            [
                OrderbookCache(
                    symbol=book.symbol,
                    levels=book.levels,
                    total_ask_qty=book.total_ask_qty,
                    total_bid_qty=book.total_bid_qty,
                    fetched_at=now,
                    expires_at=now + ORDERBOOK_TTL,
                    source=QuoteSource.KIS,
                )
                for _, book in fetched
            ],
            update_conflicts=True,
            unique_fields=["symbol"],
            update_fields=["levels", "total_ask_qty", "total_bid_qty",
                           "fetched_at", "expires_at", "source"],
        )
        SubscriptionRegistry.objects.filter(
            pk__in=[row.pk for row, _ in fetched]
        ).update(last_polled_at=now)

    result.created = sum(1 for _, book in fetched if book.symbol not in existing)
    result.updated = len(fetched) - result.created
    on_progress(f"호가 갱신 {len(fetched)}종 (신규 {result.created} · 실패 {result.skipped})")


# ─────────────────────────────────────────────────────────────────
# 2. 잡 1 — poll_quotes (KIS → 네이버 → 시뮬레이션)
# ─────────────────────────────────────────────────────────────────


def poll_quotes(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """우선순위 기반으로 현재가를 갱신한다 (F-20 잡 1 · 장중 10초 · F-16 2.5).

    자산군마다 소스가 다르다.

    | 자산군 | 소스 | 비고 |
    |---|---|---|
    | 주식(대회) | KIS → 네이버 | 시뮬레이션 금지 (F-16 4.2) |
    | 주식(연습) | 네이버 다종목 → 시뮬레이션 | 수업용 14종만 시뮬 |
    | 코인 | 업비트 `/v1/ticker` | 다종목 1회 · 인증 불필요 |
    | 대체자산 | — | v2.0 1차 범위 밖 (F-08) |

    ★ `coalesce_idle=True` — 잡 2 와 같은 이유.
    """
    result = SyncResult()
    with job_run("poll_quotes", triggered_by, dry_run=dry_run, coalesce_idle=True) as record:
        _run_quotes(result=result, dry_run=dry_run, on_progress=on_progress)
        record["rows"] = result.rows
    return result


def _run_quotes(*, result: SyncResult, dry_run: bool, on_progress: ProgressFn) -> None:
    """`poll_quotes` 의 본체."""
    targets = list(
        SubscriptionRegistry.objects
        .order_by("priority", _oldest_first())
        [: MAX_QUOTES_PER_RUN + 1]
    )
    if len(targets) > MAX_QUOTES_PER_RUN:
        targets = targets[:MAX_QUOTES_PER_RUN]
        result.note(
            f"시세 대상이 {MAX_QUOTES_PER_RUN}종을 넘어 잘라 처리했습니다 — "
            f"나머지는 다음 회차가 가져갑니다"
        )
    if not targets:
        return

    by_class: dict[str, list[SubscriptionRegistry]] = defaultdict(list)
    for row in targets:
        by_class[row.asset_class].append(row)

    on_progress(
        "시세 갱신 대상 "
        + " · ".join(f"{AssetClass(key).label} {len(rows)}종" for key, rows in sorted(by_class.items()))
    )
    if dry_run:
        result.updated = len(targets)
        return

    rows: list[QuoteCache] = []
    polled: list[int] = []          # 갱신에 성공한 SubscriptionRegistry pk

    stock_rows = by_class.get(AssetClass.STOCK, [])
    if stock_rows:
        fetched, failed = _fetch_stock_quotes(stock_rows, result=result, on_progress=on_progress)
        rows += fetched
        polled += [row.pk for row in stock_rows if row.symbol not in failed]

    crypto_rows = by_class.get(AssetClass.CRYPTO, [])
    if crypto_rows:
        fetched = _fetch_crypto_quotes(crypto_rows, result=result, on_progress=on_progress)
        rows += fetched
        got = {row.symbol for row in fetched}
        polled += [row.pk for row in crypto_rows if row.symbol in got]

    alt_rows = by_class.get(AssetClass.ALT, [])
    if alt_rows:
        # 대체자산 시세 원천은 F-08 이 아직 정하지 않았다. **조용히 빠뜨리지 않는다.**
        result.note(
            f"대체자산 {len(alt_rows)}종은 갱신하지 않았습니다 — "
            f"시세 원천이 아직 정해지지 않았습니다 (F-08 · v2.0 1차 범위 밖)"
        )

    if not rows:
        return

    _write_quotes(rows, polled=polled, result=result)
    on_progress(f"시세 갱신 {len(rows)}종 (신규 {result.created} · 실패 {result.skipped})")


def _fetch_stock_quotes(
    targets: list[SubscriptionRegistry], *, result: SyncResult, on_progress: ProgressFn
) -> tuple[list[QuoteCache], set[str]]:
    """주식 현재가 — 3단 폴백 (F-16 4장).

    Returns:
        `(캐시에 쓸 행들, 끝내 실패한 종목코드 집합)`.

    ★★ **대회 종목을 먼저, 그리고 다르게 다룬다** ─────────────────────────────

        대회(`needs_orderbook=True`)   KIS 1순위 — 실호가와 같은 원천에서 온 값이라
                                        체결 판정과 어긋나지 않는다
        연습                            네이버 다종목 1회 — 40종을 한 번에 받는다

    KIS 유량은 귀하고 네이버는 넉넉하다. **비싼 자원을 꼭 필요한 곳에만 쓴다.**
    """
    contest = [row for row in targets if row.needs_orderbook]
    practice = [row for row in targets if not row.needs_orderbook]

    quotes: dict[str, QuoteCache] = {}
    now = timezone.now()

    # ── ① 대회 종목: KIS ────────────────────────────────────────
    kis_failed: list[SubscriptionRegistry] = []
    if contest:
        if not kis.is_configured():
            kis_failed = list(contest)
            result.note(
                "KIS 자격증명이 없어 대회 종목 시세를 네이버로 받습니다 "
                "(F-16 4.2 의 1순위가 빠진 상태입니다)."
            )
        else:
            config = kis.load_config()
            for row in contest:
                try:
                    quote = kis.fetch_quote(row.symbol, config=config)
                except ExternalDataError as exc:
                    kis_failed.append(row)
                    logger.warning("KIS 현재가 실패: %s — %s", row.symbol, exc)
                    continue
                quotes[row.symbol] = QuoteCache(
                    asset_class=AssetClass.STOCK,
                    symbol=row.symbol,
                    price=quote.price,
                    change=quote.change,
                    change_pct=quote.change_pct,
                    volume=quote.volume,
                    open=quote.open,
                    high=quote.high,
                    low=quote.low,
                    prev_close=quote.prev_close,
                    is_simulated=False,
                    fetched_at=now,
                    expires_at=now + QUOTE_TTL,
                    source=QuoteSource.KIS,
                )

    # ── ② 나머지 + KIS 실패분: 네이버 다종목 ─────────────────────
    #
    # ★ KIS 가 실패한 대회 종목도 여기 합류한다 — 그것이 F-16 4.2 의 2순위다.
    naver_targets = [row.symbol for row in practice] + [row.symbol for row in kis_failed]
    naver_failed: set[str] = set(naver_targets)
    if naver_targets:
        # yfinance 자리 — 설치돼 있으면 1순위로 끼어든다 (모듈 docstring · 변경노트 E-45).
        try:
            received = naver.fetch_quotes(sorted(set(naver_targets)))
        except ExternalDataError as exc:
            received = {}
            logger.warning("네이버 다종목 실패 — %s", exc)
            on_progress(f"⚠ 네이버 시세 실패 — {exc}")
        for symbol, quote in received.items():
            naver_failed.discard(symbol)
            quotes[symbol] = QuoteCache(
                asset_class=AssetClass.STOCK,
                symbol=symbol,
                price=quote.price,
                change=quote.change,
                change_pct=quote.change_pct,
                volume=quote.volume,
                prev_close=quote.prev_close,
                is_simulated=False,
                fetched_at=now,
                expires_at=now + QUOTE_TTL,
                source=QuoteSource.NAVER,
            )

    # ── ③ 시뮬레이션 — **연습 · 수업용 14종에 한해서만** (F-16 4.1) ─
    still_failed = naver_failed
    if still_failed:
        contest_symbols = {row.symbol for row in contest}
        # 대회 종목은 절대 시뮬로 채우지 않는다 (모듈 docstring).
        candidates = sorted(still_failed - contest_symbols)
        featured = set(
            StockMaster.objects
            .filter(symbol__in=candidates, is_featured=True)
            .values_list("symbol", flat=True)
        )
        for symbol in candidates:
            if symbol not in featured:
                continue
            simulated = _simulated_quote(symbol, now)
            if simulated is not None:
                quotes[symbol] = simulated
                still_failed = still_failed - {symbol}

        blocked = sorted(still_failed & contest_symbols)
        if blocked:
            result.note(
                f"대회 종목 {len(blocked)}종은 KIS·네이버가 모두 실패해 "
                f"**갱신하지 않았습니다** — 시뮬레이션 가격으로 대회를 체결하지 않습니다 "
                f"(F-16 4.1). 5분이 지나면 체결이 자동 보류됩니다"
            )

    result.skipped += len(still_failed)
    return list(quotes.values()), still_failed


def _simulated_quote(symbol: str, now) -> QuoteCache | None:
    """수업용 14종의 시뮬레이션 가격 (v1.0 `_simulated_price` 승계 · F-16 4.1).

    v1.0 은 `BASE_PRICES` 딕셔너리를 하드코딩했다. v2.0 은 **`StockMaster.close_price`
    (pykrx 가 넣은 전일 종가)를 기준값으로 쓴다** — 하드코딩이 사라지고,
    수업용 종목이 바뀌어도 따라간다.

    ★ **`is_simulated=True` 를 반드시 남긴다.** 이 표시가 대회 체결을 막고
      (`market/quotes.py`), 화면에 "시뮬레이션 가격" 배지를 띄운다.
      v1.0 은 이 사실이 코드에만 있고 화면에 없었다 (F-16 4.1).

    Returns:
        기준가를 얻지 못하면 `None` — **0원짜리 시뮬레이션은 만들지 않는다.**
    """
    base = (
        StockMaster.objects.filter(symbol=symbol)
        .values_list("close_price", flat=True)
        .first()
    )
    if not base or base <= 0:
        return None

    # v1.0 의 파형을 그대로 옮긴다 — 10초마다 한 칸씩, ±10칸(기준가의 ±2%) 안에서 흔든다.
    step = int((now.timestamp() // 10) % 20) - 10
    price = max(Decimal(1_000), base + Decimal(step) * (base / Decimal(500)).to_integral_value())
    change = price - base
    return QuoteCache(
        asset_class=AssetClass.STOCK,
        symbol=symbol,
        price=price,
        change=change,
        change_pct=(change / base * 100) if base else Decimal(0),
        volume=0,
        prev_close=base,
        is_simulated=True,
        fetched_at=now,
        expires_at=now + QUOTE_TTL,
        source=QuoteSource.SIM,
    )


def _fetch_crypto_quotes(
    targets: list[SubscriptionRegistry], *, result: SyncResult, on_progress: ProgressFn
) -> list[QuoteCache]:
    """코인 현재가 — 업비트 `/v1/ticker` (F-16 6장).

    ★ **다종목 1회다.** `markets=KRW-BTC,KRW-ETH,…` 로 한 번에 받는다.
      인증이 필요 없고(발급 가이드 5장) 유량도 넉넉해 주식보다 훨씬 단순하다.

    ★ **시뮬레이션 폴백이 없다.** 코인은 24시간 거래라 "장외 종가" 라는 개념이 없고,
      가짜 값으로 연습 체결을 하면 사용자가 손실을 오해한다. 실패하면 낡은 값이 남는다.
    """
    markets = [row.symbol for row in targets]
    try:
        received = _fetch_upbit_tickers(markets)
    except ExternalDataError as exc:
        result.skipped += len(markets)
        logger.warning("업비트 시세 실패 — %s", exc)
        on_progress(f"⚠ 업비트 시세 실패 — {exc}")
        return []

    now = timezone.now()
    rows = []
    for item in received:
        market = str(item.get("market") or "")
        price = _to_decimal(item.get("trade_price"))
        if not market or price <= 0:
            continue
        prev_close = _to_decimal(item.get("prev_closing_price")) or price
        rows.append(QuoteCache(
            asset_class=AssetClass.CRYPTO,
            symbol=market,
            price=price,
            change=_to_decimal(item.get("signed_change_price")),
            # 업비트는 등락률을 **비율**(0.0123)로 준다. 우리 규약은 퍼센트다 (규약 4.2).
            change_pct=_to_decimal(item.get("signed_change_rate")) * 100,
            volume=int(_to_decimal(item.get("acc_trade_volume_24h"))),
            open=_to_decimal(item.get("opening_price")),
            high=_to_decimal(item.get("high_price")),
            low=_to_decimal(item.get("low_price")),
            prev_close=prev_close,
            is_simulated=False,
            fetched_at=now,
            expires_at=now + QUOTE_TTL,
            source=QuoteSource.UPBIT,
        ))
    result.skipped += len(markets) - len(rows)
    return rows


UPBIT_TICKER_URL = "https://api.upbit.com/v1/ticker"


def _fetch_upbit_tickers(markets: list[str]) -> list[dict]:
    """업비트 현재가 다종목 조회. `market/services.py` 의 마켓 목록 조회와 같은 방식."""
    from core.jobs import retry                      # noqa: PLC0415
    from market.services import spend_api_budget     # noqa: PLC0415

    try:
        import requests        # noqa: PLC0415 — 지연 import
    except ImportError as exc:
        raise ExternalDataError(
            "requests 가 설치되어 있지 않습니다.",
            hint="backend 가상환경에서 `pip install -r requirements.txt` 를 실행하십시오.",
        ) from exc

    def _once():
        spend_api_budget("UPBIT")
        response = requests.get(
            UPBIT_TICKER_URL,
            params={"markets": ",".join(markets)},
            timeout=10,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            raise ValueError(f"예상과 다른 응답 형식입니다: {type(payload).__name__}")
        return payload

    return retry(_once, label=f"업비트 현재가 {len(markets)}종")


def _write_quotes(rows: list[QuoteCache], *, polled: list[int], result: SyncResult) -> None:
    """받은 시세를 `QuoteCache` 에 한 번에 쓴다 (트랜잭션 안).

    ★ `created` / `updated` 를 나눠 세는 이유 — **두 번째 회차부터 `created` 가
      0이 아니면 upsert 키가 잘못 잡힌 것이다** (`core/jobs.py` 의 `SyncResult`).
    """
    keys = {(row.asset_class, row.symbol) for row in rows}
    existing = {
        (asset_class, symbol)
        for asset_class, symbol in QuoteCache.objects
        .filter(symbol__in=[symbol for _, symbol in keys])
        .values_list("asset_class", "symbol")
    }

    with transaction.atomic():
        QuoteCache.objects.bulk_create(
            rows,
            update_conflicts=True,
            unique_fields=["asset_class", "symbol"],
            update_fields=[
                "price", "change", "change_pct", "volume", "open", "high", "low",
                "prev_close", "is_simulated", "fetched_at", "expires_at", "source",
            ],
            batch_size=200,
        )
        if polled:
            SubscriptionRegistry.objects.filter(pk__in=polled).update(
                last_polled_at=timezone.now()
            )

    result.created += len(keys - existing)
    result.updated += len(keys & existing)


def _to_decimal(raw) -> Decimal:
    try:
        return Decimal(str(raw).strip() or 0)
    except Exception:       # noqa: BLE001 — decimal.InvalidOperation 등
        return Decimal(0)


__all__ = ["poll_orderbook", "poll_quotes"]
