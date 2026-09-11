"""시간 규약 (규약 3장).

저장은 UTC, 표시는 KST. `USE_TZ=True` 이므로 DB 에는 `timestamptz` 로 들어간다.

**모델·서비스 코드에서는 `django.utils.timezone.now()` 만 쓴다.**
`datetime.now()` 는 naive 라 서버 로케일에 끌려간다.

Django 관점 — FastAPI 에서는 `datetime` 을 어떻게 다룰지 프로젝트마다 달랐다.
Django 는 `USE_TZ=True` 를 켜는 순간 aware 가 아닌 값을 넣으면 경고를 띄운다.
강제성이 있어서 오히려 실수가 줄어든다.
"""

import zoneinfo
from datetime import date, datetime, timedelta

from django.utils import timezone

KST = zoneinfo.ZoneInfo("Asia/Seoul")


def today_kst() -> date:
    """오늘의 KST 날짜. 정산·스냅샷의 `date` 는 전부 이걸 쓴다.

    `DailySnapshot.date` · `TradingCalendar.date` · `WeeklyTurnover.week_start` 는
    시각이 아니라 **한국 장의 하루**를 가리킨다. UTC 로 환산하면 날짜가 하루 밀린다.
    """
    return timezone.now().astimezone(KST).date()


def now_kst() -> datetime:
    """현재 시각을 KST 로 본 aware datetime. 화면 표시·로그용."""
    return timezone.now().astimezone(KST)


def week_start_kst(day: date | None = None) -> date:
    """그 주 월요일의 KST 날짜.

    F-04 5.1 확정 — 주는 월요일 00:00 ~ 일요일 24:00 KST 다.
    `WeeklyTurnover.week_start` 에 넣는 값을 만든다.
    """
    day = day or today_kst()
    return day - timedelta(days=day.weekday())
