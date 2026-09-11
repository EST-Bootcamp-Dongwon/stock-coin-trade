"""거래 세션 판정 — "지금 장이 열려 있는가" (F-03 8장 · F-04 4.1 ②).

`TradingCalendar` 를 소유한 앱이 `market` 이므로 판정도 여기 둔다.
`trading` 과 `contests` 가 함께 쓴다.

| 모드 | 시간 | 장외 주문 |
|---|---|---|
| **대회** | 영업일 09:00~15:30 KST | `PENDING_OPEN` 으로 접수 → 다음 장 시작 시 처리 |
| 연습(주식) | 상시 | 최종 종가로 즉시 체결 (v1.0 동작 유지) |
| 연습(코인) | 24시간 | — |
| 연습(대체자산) | 상시 | 영업일 단위 가격이라 같은 날은 동일가 |

**동시호가(08:30~09:00 / 15:20~15:30) 세션은 v2.1** 이다. 1차는 09:00~15:30 을
하나의 연속 세션으로 다룬다 (F-03 8장).
"""

import logging
from datetime import date, datetime, time

from core.time import KST, now_kst
from market.models import TradingCalendar

logger = logging.getLogger(__name__)

# 대회 거래 시간 (KST). 동시호가는 v2.1 이라 연속 세션 하나로 본다.
MARKET_OPEN = time(9, 0)
MARKET_CLOSE = time(15, 30)


class SessionState:
    """장 상태. `TextChoices` 가 아닌 이유는 **DB 에 저장하지 않기** 때문이다 —
    매번 계산하는 값이라 컬럼이 없고, 따라서 마이그레이션에 실릴 일도 없다."""

    OPEN = "OPEN"                  # 장중
    BEFORE_OPEN = "BEFORE_OPEN"    # 영업일이지만 아직 09:00 전
    AFTER_CLOSE = "AFTER_CLOSE"    # 영업일이지만 15:30 지남
    HOLIDAY = "HOLIDAY"            # 휴장일 (주말·공휴일)


def is_business_day(day: date | None = None) -> bool:
    """그날이 영업일인가.

    ★★ **달력에 행이 없을 때 무엇을 답할 것인가** ────────────────────────────

    `TradingCalendar` 는 pykrx 로 채우는데 **미래를 채울 수 없다**(E-20).
    "내일이 영업일인가"를 물으면 행이 없는 것이 **기본 상태**다.

    | 안 | 결과 |
    |---|---|
    | 없으면 휴장 | 달력이 하루만 뒤처져도 **대회가 통째로 멈춘다** |
    | 없으면 개장 | 공휴일에 장이 열린 것으로 본다 |
    | **없으면 요일로 판정** ✅ | 주말은 확실히 막고, 공휴일만 놓친다 |

    세 번째를 택한다. 놓치는 것은 **연 15일 남짓의 공휴일**이고, 그날은 시세가
    갱신되지 않아 호가 캐시가 낡는다 → `PriceUnavailable(recoverable=True)` 로
    체결이 자연히 멈춘다. **두 겹의 방어 중 하나가 빠지는 것이지 뚫리는 게 아니다.**

    운영자가 달력을 채우면(`manage.py sync_trading_calendar`) 정확해진다.
    """
    day = day or now_kst().date()
    row = TradingCalendar.objects.filter(date=day).first()
    if row is not None:
        return row.is_open

    weekday_open = day.weekday() < 5        # 월(0)~금(4)
    logger.info(
        "TradingCalendar 에 %s 행이 없습니다 — 요일로 판정합니다 (%s). "
        "`manage.py sync_trading_calendar` 로 채우면 공휴일까지 정확해집니다",
        day, "영업일" if weekday_open else "휴장",
    )
    return weekday_open


def session_state(at: datetime | None = None) -> str:
    """지금(또는 주어진 시각)의 장 상태.

    Args:
        at: 판정할 시각. **aware datetime** 이어야 한다. 생략하면 현재.

    ★ 인자를 받는 이유는 **테스트 때문만이 아니다.** 장 마감 잡(`close_market`)이
      15:35 에 돌면서 "15:30 기준으로 미체결이었던 주문"을 판정해야 할 수 있다.
    """
    moment = (at or now_kst()).astimezone(KST)
    if not is_business_day(moment.date()):
        return SessionState.HOLIDAY
    clock = moment.time()
    if clock < MARKET_OPEN:
        return SessionState.BEFORE_OPEN
    if clock > MARKET_CLOSE:
        return SessionState.AFTER_CLOSE
    return SessionState.OPEN


def is_market_open(at: datetime | None = None) -> bool:
    """대회 주문을 지금 체결할 수 있는가."""
    return session_state(at) == SessionState.OPEN
