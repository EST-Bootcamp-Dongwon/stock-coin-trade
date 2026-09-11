"""네이버 금융 시세 — 3단 폴백의 2순위 (F-16 4장 · v1.0 승계).

v1.0 `stock_market.py` 가 쓰던 두 경로를 그대로 가져온다. **잘 만든 설계라
그대로 가져간다**는 F-16 4장의 판단을 따르되, 한 가지를 더 살린다.

★★ **다종목 API 가 이 모듈의 핵심이다** ─────────────────────────────────────

    단건   m.stock.naver.com/api/stock/005930/basic          1종목 = 1호출
    다종목 polling.finance.naver.com/api/realtime?query=…    **N종목 = 1호출**

v1.0 은 다종목 경로를 대시보드에만 썼다. v2.0 의 폴링 잡에는 이쪽이 훨씬 중요하다 —
연습 모드 관심 종목이 40종이어도 **1초에 한 번 부르면 전부 갱신**된다.
KIS 가 초당 몇 건이냐를 두고 씨름하는 것과 대조된다.

그래서 잡 1(`poll_quotes`)은 **연습 종목을 다종목 1회로 처리**하고, KIS 유량은
대회 종목에만 쓴다 (→ `market/jobs.py`).

★ **비공식 경로라는 사실을 잊지 않는다.** 네이버가 형식을 바꾸면 조용히 깨진다.
  그래서 이 모듈은 값이 이상하면 **빈 결과를 주는 대신 예외를 던진다** —
  0원짜리 시세가 캐시에 들어가 대회 체결에 쓰이는 것이 최악이다.
"""

import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from core.jobs import ExternalDataError, retry
from market.services import spend_api_budget

logger = logging.getLogger(__name__)

STOCK_BASIC_URL = "https://m.stock.naver.com/api/stock/{symbol}/basic"
REALTIME_URL = "https://polling.finance.naver.com/api/realtime"

# ★ 브라우저인 척한다. v1.0 이 쓰던 헤더를 그대로 가져왔다 —
#   `Referer` 가 없으면 네이버가 거절한다.
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.naver.com/",
}
TIMEOUT_SECONDS = 8

# 다종목 1회에 몇 종목까지 실을 것인가.
#
# ★ URL 길이 제한이 실질적인 상한이다(종목당 7자). 40종이면 쿼리스트링이 300자
#   남짓이라 안전하고, 그 이상이 필요하면 나눠서 여러 번 부른다.
REALTIME_BATCH_SIZE = 40

SYMBOL_RE = re.compile(r"^\d{6}$")


@dataclass(frozen=True)
class NaverQuote:
    """네이버에서 받은 현재가. `market/jobs.py` 가 `QuoteCache` 로 옮긴다."""

    symbol: str
    price: Decimal
    prev_close: Decimal
    change: Decimal
    change_pct: Decimal
    volume: int


def _requests():
    try:
        import requests        # noqa: PLC0415 — 지연 import (market/services.py 와 같은 이유)
    except ImportError as exc:
        raise ExternalDataError(
            "requests 가 설치되어 있지 않습니다.",
            hint="backend 가상환경에서 `pip install -r requirements.txt` 를 실행하십시오.",
        ) from exc
    return requests


def fetch_quotes(symbols: list[str]) -> dict[str, NaverQuote]:
    """**여러 종목을 한 번에** 조회한다 (모듈 docstring 참조).

    Args:
        symbols: 6자리 종목코드 목록. 6자리가 아닌 것은 조용히 버린다 —
            네이버 국내 시세는 숫자 6자리만 받는다 (v1.0 `_fetch_naver_quote` 승계).

    Returns:
        `{종목코드: NaverQuote}`. **응답에 없는 종목은 그냥 빠진다.**
        폴링 잡은 "받은 것만 갱신하고 나머지는 다음 회차" 가 맞는 동작이라
        한 종목이 비었다고 예외를 던지지 않는다.

    Raises:
        ExternalDataError: 네트워크 실패이거나 **응답이 통째로 비어 있는** 경우.
            후자를 성공으로 넘기면 "갱신했는데 0건" 이 조용히 반복된다.
    """
    targets = [s for s in symbols if SYMBOL_RE.match(s or "")]
    if not targets:
        return {}

    quotes: dict[str, NaverQuote] = {}
    for start in range(0, len(targets), REALTIME_BATCH_SIZE):
        batch = targets[start : start + REALTIME_BATCH_SIZE]
        quotes.update(_fetch_realtime_batch(batch))

    if not quotes:
        raise ExternalDataError(
            f"네이버 다종목 시세가 비어 있습니다 (요청 {len(targets)}종).",
            hint=(
                "네이버 응답 형식이 바뀌었거나 차단됐을 수 있습니다. "
                "비공식 경로라 예고 없이 바뀝니다 — F-16 4장."
            ),
        )
    return quotes


def _fetch_realtime_batch(symbols: list[str]) -> dict[str, NaverQuote]:
    """다종목 API 1회. 실패하면 지수 백오프로 재시도한다."""
    requests = _requests()

    def _once():
        spend_api_budget("NAVER")
        response = requests.get(
            REALTIME_URL,
            params={"query": f"SERVICE_ITEM:{','.join(symbols)}"},
            headers=HEADERS,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError(f"예상과 다른 응답 형식입니다: {type(payload).__name__}")
        return payload

    payload = retry(
        _once,
        label=f"네이버 다종목 시세 {len(symbols)}종",
        on_retry=lambda attempt, exc, delay: logger.warning(
            "네이버 다종목 %d회 실패 (%s) — %.0f초 후 재시도", attempt, type(exc).__name__, delay
        ),
    )

    # 응답 구조: {"result": {"areas": [{"datas": [ {...}, … ]}]}}
    areas = (payload.get("result") or {}).get("areas") or []
    rows = [row for area in areas for row in (area.get("datas") or [])]

    quotes: dict[str, NaverQuote] = {}
    for row in rows:
        symbol = str(row.get("cd") or "").strip()
        price = _to_decimal(row.get("nv"))
        if not SYMBOL_RE.match(symbol) or price <= 0:
            # 가격 0 은 "아직 안 받음" 과 구분되지 않는다. 넣지 않는다.
            continue
        prev_close = _to_decimal(row.get("pcv")) or price
        quotes[symbol] = NaverQuote(
            symbol=symbol,
            price=price,
            prev_close=prev_close,
            change=_to_decimal(row.get("cv")),
            change_pct=_to_decimal(row.get("cr")),
            volume=int(_to_decimal(row.get("aq"))),
        )
    return quotes


def fetch_quote(symbol: str) -> NaverQuote:
    """**한 종목**을 단건 API 로 조회한다 (v1.0 `_fetch_naver_quote` 승계).

    다종목 API 가 있는데 이걸 남겨 두는 이유는 **응답 형식이 서로 독립**이기 때문이다.
    한쪽이 깨져도 다른 쪽으로 버틸 수 있다. 잡 1 은 다종목을 쓰고, 개별 종목을
    확인해야 할 때(`kis_probe` 같은 점검 경로)는 이쪽을 쓴다.

    Raises:
        ExternalDataError: 네트워크 실패 · 형식 붕괴 · 현재가 0.
    """
    if not SYMBOL_RE.match(symbol or ""):
        raise ExternalDataError(
            f"국내 시세는 6자리 종목코드만 조회할 수 있습니다: {symbol!r}",
            hint="ETF·ETN 도 6자리입니다. 해외 티커는 이 경로로 조회하지 않습니다.",
        )

    requests = _requests()

    def _once():
        spend_api_budget("NAVER")
        response = requests.get(
            STOCK_BASIC_URL.format(symbol=symbol),
            headers={**HEADERS, "Referer": "https://m.stock.naver.com/"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        row = response.json()
        if not isinstance(row, dict):
            raise ValueError(f"예상과 다른 응답 형식입니다: {type(row).__name__}")
        return row

    row = retry(
        _once,
        label=f"네이버 {symbol} 시세",
        on_retry=lambda attempt, exc, delay: logger.warning(
            "네이버 %s %d회 실패 (%s) — %.0f초 후 재시도", symbol, attempt, type(exc).__name__, delay
        ),
    )

    price = _to_decimal(row.get("closePrice"))
    if price <= 0:
        raise ExternalDataError(
            f"네이버 응답에 현재가가 없습니다 ({symbol}).",
            hint="거래정지·상장폐지 종목이거나 응답 형식이 바뀌었을 수 있습니다.",
        )

    change = _to_decimal(row.get("compareToPreviousClosePrice"))
    rate = _to_decimal(row.get("fluctuationsRatio"))
    # ★ v1.0 이 잡아둔 함정 — **하락인데 `change` 가 양수로 오는 경우가 있다.**
    #   등락률의 부호로 바로잡는다.
    if rate < 0 and change > 0:
        change = -change

    prev_close = price - change
    return NaverQuote(
        symbol=symbol,
        price=price,
        prev_close=prev_close if prev_close > 0 else price,
        change=change,
        change_pct=rate,
        volume=int(_to_decimal(row.get("accumulatedTradingVolume"))),
    )


def _to_decimal(raw) -> Decimal:
    """`"74,300"` · `74300` · `None` · `""` 를 전부 받아준다 (v1.0 `_to_number` 승계)."""
    try:
        return Decimal(str(raw).replace(",", "").strip() or 0)
    except Exception:       # noqa: BLE001 — decimal.InvalidOperation 등
        return Decimal(0)


__all__ = ["NaverQuote", "fetch_quote", "fetch_quotes"]
