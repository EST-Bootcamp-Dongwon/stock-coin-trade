"""market 서비스 계층 — 외부 시장 데이터 적재 (F-16 · E-04).

**규약** — 여기 있는 함수는 HTTP 엔드포인트(pg_cron → pg_net)와 management command
양쪽에서 그대로 불린다 (F-16 5.3). 그래서 이 함수들은:

- `stdout` 에 직접 쓰지 않는다 — 진행 상황은 `on_progress` 콜백으로 넘긴다
- 외부 장애를 `ExternalDataError` 로 좁혀 던진다 — 호출부가 코드 버그와 구분할 수 있게
- `job_run()` 으로 자기 자신을 감싼다 — 누가 부르든 `DataSyncLog` 에 남게

★★ **외부 호출은 트랜잭션 밖에서, DB 쓰기만 트랜잭션 안에서** ────────────────

    ① 외부에서 전부 받아온다   (느리다 · 실패할 수 있다 · 트랜잭션 밖)
    ② 받은 것을 한 번에 쓴다   (빠르다 · 원자적이다 · 트랜잭션 안)

수천 종목을 받는 동안 트랜잭션을 열어두면 그 시간 내내 커넥션을 붙잡는다.
Supabase 는 커넥션 수가 빠듯하다(`settings.py` 의 `CONN_MAX_AGE` 주석 참조).
중간에 네트워크가 끊기면 이미 받은 것까지 통째로 롤백되는 문제도 있다.

Django 관점 — FastAPI + SQLAlchemy 에서는 `session` 의 수명이 요청과 같아 이 구분을
의식하기 어려웠다. Django 는 기본이 autocommit 이라 `transaction.atomic()` 을 **명시적으로
연 구간만** 트랜잭션이다. 이 기본값이 여기서는 유리하다.
"""

import calendar
import os
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Callable

from django.db import connection, transaction

from core.constants import TriggeredBy
from core.jobs import ExternalDataError, SyncResult, job_run, retry
from core.time import today_kst
from market.models import (
    CalendarSource,
    Market,
    MarketIndexSnapshot,
    StockMaster,
    StockType,
    TradingCalendar,
    UpbitMarket,
)

# 진행 상황을 받는 쪽(커맨드)이 넘기는 콜백. 서비스는 화면을 모른다.
ProgressFn = Callable[[str], None]


def _noop(_message: str) -> None:
    """`on_progress` 를 안 넘겼을 때 쓰는 빈 콜백."""


# ─────────────────────────────────────────────────────────────────
# 0. 외부 API 유량 예산 (E-04 6장 · 변경노트 D-1)
# ─────────────────────────────────────────────────────────────────

# 초당 허용 호출 수. **보수적으로 잡는다** — 막히면 복구가 느리고,
# 배치는 몇 초 더 걸려도 아무도 손해 보지 않는다.
API_RATE_LIMITS = {
    # 업비트 공개 시세(quotation) 그룹은 초당 10회다. 여유를 둔다.
    "UPBIT": 8,
    # KRX 데이터포털은 공개된 유량 기준이 없다. pykrx 는 웹 포털을 그대로
    # 두드리는 방식이라 더욱 조심한다. 일 1회 배치라 느려도 문제없다.
    "KRX": 2,
    # ★ KIS 는 **모드에 따라 한도가 다르고 공개 자료도 엇갈린다**(변경노트 E-43).
    #   그래서 `market/kis.py` 가 `.env` 를 보고 `limit=` 로 넘겨준다.
    #   여기 값은 넘기지 않았을 때의 최후 방어선이다 — 모의 기준으로 낮게 잡는다.
    "KIS": 2,
    "KIS_REAL": 15,
    # 네이버 금융은 비공식 경로다. 공개된 한도가 없으므로 **남의 서버를 두드리는
    # 쪽이 조심한다.** 다종목 API 로 한 번에 받으므로 초당 2건이면 충분하다.
    "NAVER": 2,
}

# 예산이 찼을 때 최대 이만큼 기다린다. 넘으면 외부 장애로 본다.
BUDGET_MAX_WAIT_SECONDS = 30.0


def charge_api_budget(api_name: str, *, amount: int = 1) -> int:
    """호출 1건을 예산에 기록하고, **그 1초 창의 누적 호출 수**를 돌려준다.

    변경노트 D-1 이 확정한 방식이다 — 잠금이 아니라 upsert 로 센다::

        INSERT INTO api_call_budget (api_name, window_start, count)
        VALUES ('KIS', date_trunc('second', clock_timestamp()), 1)
        ON CONFLICT (api_name, window_start)
        DO UPDATE SET count = api_call_budget.count + EXCLUDED.count
        RETURNING count;

    잠금(`SELECT … FOR UPDATE`)은 트랜잭션이 끝날 때까지 다른 요청을 세우는데,
    **초당 5건짜리 창에서는 그 대기 자체가 유량을 낭비한다.**

    ★★ **`now()` 가 아니라 `clock_timestamp()` 를 쓴다** ──────────────────────

    Postgres 의 `now()` 는 **트랜잭션 시작 시각**이라 트랜잭션 안에서는 아무리
    시간이 흘러도 값이 고정된다. `atomic()` 블록 안에서 예산을 차감하면 수백 건이
    **같은 1초 창에 몰려** 카운터가 즉시 한도를 넘고, 실제 호출 속도와 무관하게
    배치가 스스로를 막아버린다.

    `clock_timestamp()` 는 문장이 실행되는 실제 벽시계를 준다.
    E-04 6장·F-16 2.4 의 SQL 예시는 `now()` 로 적혀 있으나, 위 이유로 여기서는
    `clock_timestamp()` 를 쓴다 (→ 변경노트에 기록).

    ★ **예산 차감은 트랜잭션 밖에서 한다.** 트랜잭션이 롤백되면 카운터도 함께
    되돌아가는데, **호출은 이미 나갔다.** 되돌아간 예산은 거짓이다.
    이 모듈이 외부 호출을 `atomic()` 밖에 두는 이유 중 하나다.

    Returns:
        차감 후의 누적 호출 수. 한도를 넘었는지는 호출부가 판단한다.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO api_call_budget (api_name, window_start, count)
            VALUES (%s, date_trunc('second', clock_timestamp()), %s)
            ON CONFLICT (api_name, window_start)
            DO UPDATE SET count = api_call_budget.count + EXCLUDED.count
            RETURNING count
            """,
            [api_name, amount],
        )
        return cursor.fetchone()[0]


def try_charge_api_budget(api_name: str, *, limit: int | None = None) -> tuple[bool, int]:
    """차감해 보고 **한도 안이었는지**를 함께 돌려준다 — 요청 경로용.

    시세 조회처럼 사용자를 기다리게 하면 안 되는 곳이 쓴다.
    거절되면 호출하지 말고 **캐시값으로 응답한다** (F-16 2.4).
    """
    limit = limit if limit is not None else API_RATE_LIMITS.get(api_name, 5)
    count = charge_api_budget(api_name)
    return count <= limit, count


def spend_api_budget(
    api_name: str,
    *,
    limit: int | None = None,
    max_wait: float = BUDGET_MAX_WAIT_SECONDS,
) -> None:
    """예산에 자리가 날 때까지 기다렸다가 차감한다 — **배치 경로용**.

    요청 경로와 정반대의 선택이다. 배치는 기다리는 사람이 없으므로
    **캐시로 때우는 것보다 정확히 가져오는 쪽**이 낫다.

    Args:
        limit: 초당 한도. 생략하면 `API_RATE_LIMITS` 를 따른다.
            ★ 인자로 받는 이유는 **KIS 한도가 실행 환경(`KIS_MODE`)에 달려 있기**
            때문이다. 모듈 상수 하나로는 모의(낮음)와 실전(높음)을 함께 표현할 수 없고,
            자료마다 값이 엇갈려 `.env` 로 조절할 수 있어야 한다 (→ 변경노트 E-43).

    Raises:
        ExternalDataError: `max_wait` 를 넘도록 자리가 나지 않은 경우.
            보통 다른 인스턴스가 같은 API 를 몰아 쓰고 있다는 뜻이다.
    """
    limit = limit if limit is not None else API_RATE_LIMITS.get(api_name, 5)
    deadline = time.monotonic() + max_wait

    while True:
        if charge_api_budget(api_name) <= limit:
            return
        if time.monotonic() >= deadline:
            raise ExternalDataError(
                f"{api_name} 유량 예산이 {max_wait:.0f}초 동안 회복되지 않았습니다 (초당 {limit}건).",
                hint=(
                    "다른 인스턴스나 잡이 같은 API 를 몰아 쓰고 있는지 확인하십시오. "
                    "Admin > API 유량 예산 에서 최근 1초 창의 호출 수를 볼 수 있습니다."
                ),
            )
        # 다음 초 창이 열릴 때까지만 잔다. 0.1초씩 재시도하면
        # 그 시도마다 카운터가 올라가 창이 영영 안 열린다.
        time.sleep(1.0 - (time.time() % 1.0) + 0.01)


# ─────────────────────────────────────────────────────────────────
# 1. pykrx 로더
# ─────────────────────────────────────────────────────────────────


def _load_pykrx():
    """pykrx 를 **지금** 불러온다. 모듈 최상단에서 import 하지 않는다.

    ★★ **pykrx 는 import 하는 순간 KRX 에 로그인한다.** `pykrx/website/comm/webio.py`
    가 모듈 레벨에서 `build_krx_session()` 을 호출하기 때문이다. 최상단에 두면
    `manage.py help` 나 `makemigrations` 처럼 시장 데이터와 아무 상관 없는 명령도
    KRX 에 접속하고, 자격증명이 없으면 표준출력에 로그인 실패 메시지를 뱉는다.

    ★ **2026-08-13 확인 — KRX 데이터포털이 로그인을 요구하도록 바뀌었다.**
    자격증명 없이 호출하면 모든 요청이 `LOGOUT`(HTTP 400) 으로 돌아오고,
    pykrx 는 그것을 `AttributeError: 'RangeIndex' object has no attribute 'month'`
    같은 **엉뚱한 예외**로 바꿔 던진다. 원인을 짐작할 수 없는 메시지다.
    그래서 자격증명 유무를 **먼저** 보고 무엇을 해야 하는지까지 알려준다 (규약 8.5).

    F-16 5장·05 문서 5.3 은 "pykrx 일배치"를 로그인 없이 되는 것으로 전제하고
    작성됐다. 전제가 바뀐 것이라 → 변경노트에 기록.
    """
    if not (os.getenv("KRX_ID") and os.getenv("KRX_PW")):
        raise ExternalDataError(
            "KRX 데이터포털 자격증명이 없습니다 (KRX_ID · KRX_PW).",
            hint=(
                "① https://data.krx.co.kr 에서 무료 회원가입 후\n"
                "    ② backend/.env 에 KRX_ID · KRX_PW 를 넣으십시오.\n"
                "       (KRX_PASSWORD 가 아니라 KRX_PW 입니다 — pykrx 가 읽는 이름입니다)\n"
                "    ③ 지금 당장 필요 없다면 --soft-fail 로 건너뛸 수 있습니다."
            ),
        )

    try:
        from pykrx import stock       # noqa: PLC0415 — 지연 import 가 의도다
    except ImportError as exc:
        raise ExternalDataError(
            "pykrx 가 설치되어 있지 않습니다.",
            hint="backend 가상환경에서 `pip install -r requirements.txt` 를 실행하십시오.",
        ) from exc

    return stock


def _krx_call(fn, *, label: str, on_progress: ProgressFn = _noop):
    """KRX 호출 1건 — 유량 예산을 차감하고, 실패하면 지수 백오프로 재시도한다."""

    def _once():
        spend_api_budget("KRX")
        return fn()

    return retry(
        _once,
        label=label,
        on_retry=lambda attempt, exc, delay: on_progress(
            f"⚠ {label} {attempt}회 실패 ({type(exc).__name__}) — {delay:.0f}초 후 재시도"
        ),
    )


# ─────────────────────────────────────────────────────────────────
# 2. 영업일 — sync_trading_calendar
# ─────────────────────────────────────────────────────────────────


def _pykrx_month_business_days(stock, year: int, month: int, *, on_progress: ProgressFn) -> list[date]:
    """pykrx 가 아는 그 달의 영업일.

    ★ **함수 이름이 `get_previous_business_days` 인 것에 주목한다 — 과거 영업일이다.**
    미래 달을 물으면 KRX 에 지수 시세가 없어 빈 응답이 오고, pykrx 는 그것을
    `AttributeError` 로 바꿔 던진다. **재시도해도 소용없다.**
    그래서 호출부가 미래 달을 아예 묻지 않는다 (`sync_trading_calendar` 참조).
    """
    rows = _krx_call(
        lambda: stock.get_previous_business_days(year=year, month=month),
        label=f"{year}-{month:02d} 영업일 조회",
        on_progress=on_progress,
    )
    return sorted(
        row.date() if isinstance(row, datetime) else row
        for row in rows
    )


def _upsert_calendar_rows(rows: list[tuple[date, bool]]) -> SyncResult:
    """영업일 행을 upsert 하되 **운영자가 손댄 행(`source=MANUAL`)은 건드리지 않는다.**

    ★★ **E-04 4장의 예시 코드는 이 보호가 되지 않는다** ──────────────────────

        # 문서에 적힌 코드 — MANUAL 행을 지키지 못한다
        TradingCalendar.objects.filter(source=CalendarSource.PYKRX).bulk_create(
            rows, update_conflicts=True, unique_fields=["date"], update_fields=["is_open"],
        )

    `bulk_create()` 는 **매니저의 `filter()` 를 무시한다.** `INSERT … ON CONFLICT`
    문을 만들 뿐이라 앞에 붙은 조건이 SQL 에 반영되지 않는다. 그대로 두면
    운영자가 Admin 에서 "임시 휴장"으로 고쳐 놓은 날을 **다음 배치가 되살린다.**
    임시 휴장일에 대회 정산이 도는 사고로 이어진다. → 변경노트에 기록.

    Postgres 는 `ON CONFLICT DO UPDATE` 에 `WHERE` 를 붙일 수 있다. 그것으로 막는다.
    Django ORM 은 이 `WHERE` 를 표현하지 못하므로 여기만 raw SQL 을 쓴다.

    `RETURNING (xmax = 0)` 은 "이 행이 INSERT 였는가" 를 알려주는 Postgres 관용구다.
    **`WHERE` 에 걸려 건너뛴 행은 아예 반환되지 않는다** — 그 차이가 `skipped` 다.
    """
    if not rows:
        return SyncResult()

    placeholders = ",".join(["(%s, %s, %s, %s)"] * len(rows))
    params: list = []
    for day, is_open in rows:
        params.extend([day, is_open, "", CalendarSource.PYKRX])
    params.append(CalendarSource.PYKRX)     # WHERE 절이 쓸 값

    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            INSERT INTO trading_calendar (date, is_open, note, source)
            VALUES {placeholders}
            ON CONFLICT (date) DO UPDATE
               SET is_open = EXCLUDED.is_open
             WHERE trading_calendar.source = %s
            RETURNING (xmax = 0) AS inserted
            """,
            params,
        )
        touched = [row[0] for row in cursor.fetchall()]

    created = sum(1 for inserted in touched if inserted)
    return SyncResult(
        created=created,
        updated=len(touched) - created,
        skipped=len(rows) - len(touched),      # 운영자가 손댄 행
    )


def sync_trading_calendar(
    *,
    year: int,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """한 해의 영업일을 `TradingCalendar` 에 채운다 (E-04 4장).

    Args:
        year: 대상 연도.

    ★★ **미래는 채울 수 없다** ────────────────────────────────────────────

    pykrx 의 영업일은 "그날 KOSPI 지수 시세가 있었는가" 로 판정된다.
    **미래에는 시세가 없으므로 미래 영업일도 없다.** `--year 2026` 을 오늘
    (2026-08-13) 돌리면 **1월 1일 ~ 오늘까지만** 채워진다. 남은 넉 달은
    KRX 가 다음 해 휴장일을 공지한 뒤에 다시 돌려야 한다.

    이것을 **에러가 아니라 정상 종료로 다룬다.** 배치가 매일 도는 이상 어제까지는
    항상 채워지고, "아직 모르는 미래"는 원래 모르는 게 맞다.

    ★ **조회된 구간은 휴장일까지 빠짐없이 채운다.** 영업일만 넣으면 "행이 없다"가
    *휴장*인지 *아직 모름*인지 구분되지 않는다. 구간 안의 모든 날짜를 넣고
    영업일이면 `is_open=True`, 아니면 `False` 로 둔다. **구간 밖은 손대지 않는다.**
    """
    result = SyncResult()

    # ★★ **외부 조회를 `job_run` 안에 둔다.** 자격증명 누락·네트워크 장애도
    #    `DataSyncLog` 에 FAILED 로 남아야 Admin 경고 배너에 뜬다 (E-07 2.2 · F-20 5장).
    #    밖에 두면 pg_cron 이 불렀을 때 **아무 흔적 없이 실패**하고, 운영자는 데이터가
    #    왜 낡았는지 알 방법이 없다 — 없애려던 바로 그 문제다.
    #
    #    `job_run` 은 트랜잭션을 열지 않는다(행 2개를 쓸 뿐이다). 느린 외부 호출을
    #    감싸도 커넥션을 붙잡지 않으므로, "외부 호출은 `atomic()` 밖" 원칙과 충돌하지 않는다.
    with job_run("sync_trading_calendar", triggered_by, dry_run=dry_run) as record:
        stock = _load_pykrx()
        today = today_kst()

        if year > today.year:
            raise ExternalDataError(
                f"{year}년은 아직 조회할 수 없습니다 (오늘은 {today}).",
                hint="KRX 는 지나간 영업일만 알려줍니다. 해가 바뀐 뒤 다시 실행하십시오.",
            )

        last_month = 12 if year < today.year else today.month
        if year == today.year and today.month < 12:
            on_progress(
                f"{year}년은 진행 중입니다 — {last_month}월(오늘 {today})까지만 채웁니다. "
                f"나머지는 해당 월이 지난 뒤 다시 실행하십시오."
            )

        for month in range(1, last_month + 1):
            is_current_month = (year, month) == (today.year, today.month)
            try:
                business_days = _pykrx_month_business_days(
                    stock, year, month, on_progress=on_progress
                )
            except ExternalDataError:
                # ★ **진행 중인 달은 아직 영업일이 0건일 수 있다.** 1월 1일에
                #   `--year 2026` 을 돌리면 그 달에 지수 시세가 하나도 없어 pykrx 가
                #   터진다. 그것 때문에 **1~12월 전체 잡이 실패하면 안 된다.**
                #   지나간 달의 실패는 진짜 장애이므로 그대로 올려보낸다.
                if not is_current_month:
                    raise
                result.note(
                    f"{year}-{month:02d}: 아직 영업일 데이터가 없습니다 — "
                    f"이 달은 건너뜁니다 (진행 중인 달)"
                )
                continue

            if not business_days:
                # 한 달이 통째로 비는 일은 정상적으로는 없다. 조용히 넘기지 않는다.
                result.note(f"{year}-{month:02d}: 영업일이 0건 — 건너뜁니다")
                continue

            # 지나간 달은 말일까지, 진행 중인 달은 **마지막 영업일까지만** 채운다.
            # 오늘 이후를 휴장(False)으로 넣으면 그 순간 거짓 데이터가 된다.
            is_past_month = (year, month) < (today.year, today.month)
            last_day = (
                date(year, month, calendar.monthrange(year, month)[1])
                if is_past_month
                else max(business_days)
            )

            open_days = set(business_days)
            cursor_day = date(year, month, 1)
            rows: list[tuple[date, bool]] = []
            while cursor_day <= last_day:
                rows.append((cursor_day, cursor_day in open_days))
                cursor_day += timedelta(days=1)

            if dry_run:
                # 쓰지 않고 무엇이 바뀔지만 센다.
                # ★ **MANUAL 행을 실제 실행과 똑같이 건너뛴 것으로 센다.** dry-run 이
                #   "덮어쓸 것"처럼 보고하면 운영자가 자기 수정이 날아갈까 봐 실행을
                #   망설이거나, 반대로 보호가 되는 줄 모르고 지나친다.
                #   **dry-run 의 값어치는 실제 실행과 같은 숫자를 내는 데 있다.**
                sources = dict(
                    TradingCalendar.objects.filter(
                        date__in=[day for day, _ in rows]
                    ).values_list("date", "source")
                )
                created = sum(1 for day, _ in rows if day not in sources)
                skipped = sum(
                    1 for day, _ in rows if sources.get(day) == CalendarSource.MANUAL
                )
                result.merge(SyncResult(
                    created=created,
                    updated=len(rows) - created - skipped,
                    skipped=skipped,
                ))
            else:
                with transaction.atomic():
                    result.merge(_upsert_calendar_rows(rows))

            # ★ 월마다 갱신한다. 달력은 **월 단위로 커밋**되므로, 뒤쪽 달에서 실패해도
            #   앞쪽 달은 이미 DB 에 남아 있다. 루프가 끝난 뒤에만 기록하면 실패 시
            #   `rows_affected=0` 이 되어 "아무것도 안 됐다"로 잘못 읽힌다.
            record["rows"] = result.rows

            on_progress(
                f"{year}-{month:02d}: 영업일 {len(business_days)}일 / "
                f"{rows[0][0]}~{rows[-1][0]} {len(rows)}일 처리"
            )

    if result.skipped:
        result.note(
            f"운영자가 손댄 날짜 {result.skipped}건은 그대로 두었습니다 "
            f"(source=MANUAL — E-04 4장)"
        )
    return result


# ─────────────────────────────────────────────────────────────────
# 3. 종목 마스터 — sync_stock_master
# ─────────────────────────────────────────────────────────────────

# 5일 평균 거래대금의 "5일". 대회 규칙 "30억 이하 매수 불가" 의 분모다 (F-16 5.1).
TURNOVER_WINDOW_DAYS = 5

# ★ 배치가 **절대 덮어쓰지 않는** 필드.
#
#   listing_date · is_supervised · alert_level
#       pykrx 가 주지 않는다. 함수 90개를 전수 확인했다 (2026-08-13).
#       기본값으로 덮어쓰면 운영자가 Admin 에서 채워 넣은 값이 매일 지워진다.
#   is_featured
#       `seed_demo` 가 켠 수업용 14종 표시다. 배치가 끄면 시세 조회 실패 시
#       시뮬레이션 폴백 대상이 사라진다 (E-04 5.1 · 변경노트 B-8).
#   sector_code · sector_name
#       `--with-sectors` 없이 돌린 실행이 지난번에 채운 업종을 지우면 안 된다.
#       그래서 아래 `_master_update_fields()` 가 조건부로만 포함한다.
MASTER_PROTECTED_FIELDS = (
    "listing_date", "is_supervised", "alert_level", "is_featured",
)


def _master_update_fields(*, with_sectors: bool) -> list[str]:
    """upsert 충돌 시 갱신할 컬럼 목록. 보호 필드는 여기 들어가지 않는다."""
    fields = [
        "name", "market", "stock_type",
        "market_cap", "shares_outstanding", "avg_turnover_5d", "close_price",
        "is_delisted", "updated_at",
    ]
    if with_sectors:
        fields += ["sector_code", "sector_name"]
    return fields


def _classify_stock_type(symbol: str, name: str, etf: set[str], etn: set[str]) -> str:
    """종목 구분 판정. 대회는 **보통주만** 허용한다 (F-04 2.1).

    ★ pykrx 는 종목 구분을 직접 주지 않는다. 목록의 교집합과 이름 규칙으로 판정한다.

    | 구분 | 근거 |
    |---|---|
    | ETF · ETN | `get_etf_ticker_list` · `get_etn_ticker_list` 와의 교집합 (확실) |
    | 리츠 · 스팩 | 종목명 규칙 (한국거래소 상장 관행상 이름에 반드시 들어간다) |
    | 우선주 | **종목코드 끝자리가 `0` 이 아니다** (005930 보통주 / 005935 우선주) |

    끝자리 규칙은 관행이라 예외가 있을 수 있다. **틀리는 방향이 안전한 쪽**임을
    확인해 둔다 — 보통주를 우선주로 잘못 보면 대회에서 **거래가 막힐 뿐**이고,
    반대 방향(우선주를 보통주로 봄)이라야 규칙 위반이 된다. 끝자리가 0이 아닌
    보통주는 사실상 없으므로 위험한 쪽 오류는 나지 않는다.
    """
    if symbol in etf:
        return StockType.ETF
    if symbol in etn:
        return StockType.ETN
    if "스팩" in name:
        return StockType.SPAC
    if "리츠" in name:
        return StockType.REIT
    if not symbol.endswith("0"):
        return StockType.PREFERRED
    return StockType.COMMON


def _recent_business_days(stock, base_date: date, count: int, *, on_progress: ProgressFn) -> list[date]:
    """`base_date` 를 포함해 거슬러 올라간 영업일 `count` 개 (오름차순).

    ① `TradingCalendar` 를 먼저 본다 — 05 문서 7장이 `sync_trading_calendar` 를
       **먼저** 돌리라고 한 이유가 이것이다. DB 조회는 공짜고 KRX 호출은 비싸다.
    ② 달력이 아직 비어 있으면 pykrx 로 최근 몇 달을 긁어 대신한다.
       (착수 직후처럼 순서를 지키지 못한 경우에도 배치가 돌아야 한다)

    ★★ **달력이 `base_date` 까지 덮고 있을 때만 쓴다** ────────────────────────

    개수(`count`)만 보고 쓰면 **몇 달 전 영업일 5개**가 "최근 5영업일" 로 통과한다.
    이 window 는 두 곳에 함께 쓰이는데 방향이 어긋난다::

        분자  get_market_price_change(window[0], base_date)   ← base_date 까지 **누적**
        분모  len(window)                                     ← 5 로 **고정**

    달력이 k영업일 뒤처지면 `avg_turnover_5d` 가 `(5+k)/5` 배로 부풀려진다.
    이 값은 대회 규칙 **"5일 평균 거래대금 30억 이하 매수 불가"** 의 분모다 —
    부풀면 **막아야 할 저유동성 종목의 매수가 통과한다.** 에러도 경고도 나지 않는다.

    `sync_trading_calendar` 는 연 단위 수동 실행이고 미래를 채울 수 없는 반면
    `sync_stock_master` 는 매 영업일 도는 잡이라, **달력이 뒤처진 채 마스터만 도는
    상태가 오히려 기본 운영 형태다.** 그래서 개수가 아니라 **범위**를 확인한다.
    """
    # 달력에 `base_date` 행이 있는가 (개장·휴장 무관 — "그날까지 채워졌는가" 를 본다)
    covers_base_date = TradingCalendar.objects.filter(date=base_date).exists()
    from_calendar = list(
        TradingCalendar.objects.filter(date__lte=base_date, is_open=True)
        .order_by("-date")
        .values_list("date", flat=True)[:count]
    )
    if covers_base_date and len(from_calendar) == count:
        return sorted(from_calendar)

    on_progress(
        f"TradingCalendar 가 {base_date} 까지 덮고 있지 않습니다 "
        f"(최근 영업일 {len(from_calendar)}건 · 기준일 수록 {'있음' if covers_base_date else '없음'}) — "
        f"pykrx 로 대신 계산합니다. `sync_trading_calendar` 를 먼저 돌리면 빨라집니다"
    )

    collected: list[date] = []
    year, month = base_date.year, base_date.month
    for _ in range(3):                     # 최대 3개월까지만 거슬러 올라간다
        month_days = [
            day for day in _pykrx_month_business_days(stock, year, month, on_progress=on_progress)
            if day <= base_date
        ]
        collected = month_days + collected
        if len(collected) >= count:
            break
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)

    if not collected:
        raise ExternalDataError(
            f"{base_date} 이전 영업일을 하나도 찾지 못했습니다.",
            hint="KRX 응답이 비어 있습니다. 잠시 후 다시 실행하십시오.",
        )
    return sorted(collected)[-count:]


def _fetch_sector_map(
    stock, base_date_str: str, *, on_progress: ProgressFn
) -> dict[str, tuple[str, str]]:
    """KRX 업종분류를 **업종지수 구성종목에서 역산**한다 (F-16 5.2).

    ★ **GICS 를 쓰지 않는 이유** — GICS 는 MSCI·S&P 의 유료 데이터다.
    대회 규칙 안내문에 "본 대회의 섹터 구분은 KRX 업종분류를 따릅니다" 를 명시한다.

    ★ **업종지수를 어떻게 골라내는가** — 지수 목록에는 규모별(코스피 대형주)·
    전략지수(코스피 200 헬스케어)가 섞여 있다. 실측 결과 **업종지수만 이름이
    "코스피"/"코스닥" 으로 시작하지 않는다**::

        1013 전기전자        ← 업종
        1028 코스피 200      ← 전략지수 (제외)
        2181 코스닥 우량기업부 ← 소속부 (제외)

    ★★ **업종지수는 계층이 겹친다 — 좁은 쪽이 이겨야 한다** ────────────────

    KRX 는 대분류와 중분류를 **둘 다 지수로 제공한다.** 삼성전자는
    `1013 전기전자`(315종)와 `1027 제조`(493종)에 **동시에** 들어 있다.
    먼저 만난 쪽이 이기게 두면 순서에 따라 대분류가 세부 분류를 덮어쓰고,
    **"제조" 하나에 수백 종이 몰려 대회 섹터 한도 규칙이 무의미해진다.**

    구성종목이 **적은 업종일수록 구체적인 분류**다. 넓은 것부터 채우고 좁은 것이
    덮어쓰게 정렬하면 종목마다 가장 구체적인 업종이 남는다.

    ★ **우선주는 업종지수에 편입되지 않는다.** 여기서는 빈 채로 두고,
    본주의 업종을 물려받는 처리는 호출부(`_run_stock_master`)가 한다 —
    이 함수는 "KRX 가 알려준 것"만 담고 추론은 섞지 않는다.

    ★ **느리다.** 업종지수가 50여 개이고 지수 하나에 10초 안팎이 걸려 **15분가량**
    소요된다(실측 14분 58초). 그래서 `--with-sectors` 로 명시할 때만 수행한다.
    업종은 매일 바뀌는 값이 아니라 매 배치마다 다시 받을 이유도 없다.

    Returns:
        `{종목코드: (업종지수코드, 업종명)}`. 어느 업종지수에도 없는 종목은 빠진다.
    """
    # (지수코드, 업종명, 구성종목) — 전부 모은 뒤에 한 번에 정한다.
    sectors: list[tuple[str, str, list[str]]] = []

    for market in ("KOSPI", "KOSDAQ"):
        index_tickers = _krx_call(
            lambda market=market: stock.get_index_ticker_list(base_date_str, market=market),
            label=f"{market} 지수 목록",
            on_progress=on_progress,
        )
        # 규모별(코스피 대형주)·전략지수(코스피 200 헬스케어)·소속부(코스닥 우량기업부)는
        # 업종이 아니다. 실측 결과 **업종지수만 이름이 "코스피/코스닥" 으로 시작하지 않는다.**
        sector_tickers = [
            (ticker, name)
            for ticker in index_tickers
            # `get_index_ticker_name` 은 목록 조회에서 채워진 로컬 캐시라 호출 비용이 없다
            for name in [stock.get_index_ticker_name(ticker)]
            if not name.startswith(("코스피", "코스닥"))
        ]

        on_progress(f"{market} 업종지수 {len(sector_tickers)}개 — 구성종목을 받습니다")
        for order, (ticker, name) in enumerate(sector_tickers, start=1):
            members = _krx_call(
                lambda ticker=ticker: stock.get_index_portfolio_deposit_file(
                    ticker, base_date_str
                ),
                label=f"업종 {name} 구성종목",
                on_progress=on_progress,
            )
            sectors.append((ticker, name, list(members)))
            on_progress(f"  [{order}/{len(sector_tickers)}] {name}: {len(members)}종")

    # ★ 넓은 업종부터 채우고 좁은 업종이 덮어쓰게 한다 → 가장 구체적인 분류가 남는다.
    sectors.sort(key=lambda entry: len(entry[2]), reverse=True)

    mapping: dict[str, tuple[str, str]] = {}
    for ticker, name, members in sectors:
        for symbol in members:
            mapping[symbol] = (ticker, name)

    return mapping


def _base_share_symbol(symbol: str) -> str:
    """우선주 종목코드 → 본주 종목코드. `005935`(삼성전자우) → `005930`(삼성전자).

    한국 종목코드는 앞 5자리가 발행사를 가리키고 끝자리가 종류를 가른다
    (보통주 `0` · 우선주 `5`·`7`·`9` 등). 업종지수는 보통주만 편입하므로
    **우선주에 업종을 붙이려면 본주를 거쳐야 한다.**
    """
    return symbol[:5] + "0"


def sync_stock_master(
    *,
    base_date: date | None = None,
    with_sectors: bool = False,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """pykrx 로 종목 마스터를 적재한다 (F-16 5장 · E-04 3장).

    Args:
        base_date: 기준 영업일. 생략하면 KRX 가 아는 최근 영업일.
        with_sectors: KRX 업종분류까지 채운다. **10분 이상 걸린다** (`_fetch_sector_map`).

    ★ **`TRUNCATE` 하지 않는다** (F-20 6장). symbol 기준 upsert 다.
    지우고 다시 넣으면 동기화 도중 조회한 사람에게 빈 테이블이 보인다.

    ★ **상장폐지 처리** — 이번 조회에 나오지 않은 기존 종목은 `is_delisted=True` 로
    올린다. 행을 지우지는 않는다 — `Order.symbol` · `Position.symbol` 이 문자열로
    맞물려 있어(변경노트 D-2) 과거 거래 이력이 종목명을 잃으면 안 되기 때문이다.
    다만 **`--date` 로 과거를 지정하면 이 처리를 끈다.** 그날 이후 상장한 종목이
    통째로 상장폐지로 뒤집히기 때문이다.
    """
    result = SyncResult()

    # ★★ 외부 조회까지 `job_run` 안에 둔다 — 사유는 `sync_trading_calendar` 의 주석 참조.
    with job_run("sync_stock_master", triggered_by, dry_run=dry_run) as record:
        _run_stock_master(
            result=result,
            base_date=base_date,
            with_sectors=with_sectors,
            dry_run=dry_run,
            on_progress=on_progress,
        )
        record["rows"] = result.rows

    result.note(
        "배치가 건드리지 않는 필드: "
        + " · ".join(MASTER_PROTECTED_FIELDS)
        + " (pykrx 미제공 · 운영자 입력값 보호)"
    )
    return result


def _run_stock_master(
    *,
    result: SyncResult,
    base_date: date | None,
    with_sectors: bool,
    dry_run: bool,
    on_progress: ProgressFn,
) -> None:
    """`sync_stock_master` 의 본체. 결과는 넘겨받은 `result` 에 채운다.

    공개 함수와 나눈 이유는 **`job_run` 이 본체 전체를 감싸게** 하면서 들여쓰기를
    한 단계로 유지하기 위해서다. 로직을 함수 하나에 두고 `with` 로 감싸면 200줄이
    통째로 한 칸씩 밀려 읽기 어려워진다.
    """
    stock = _load_pykrx()

    # ── ① 외부에서 전부 받는다 (트랜잭션 밖) ─────────────────────
    if base_date is None:
        latest = _krx_call(
            stock.get_nearest_business_day_in_a_week,
            label="최근 영업일 조회", on_progress=on_progress,
        )
        base_date = datetime.strptime(latest, "%Y%m%d").date()
        mark_delisted = True
    else:
        # 과거를 지정한 실행은 "그때 그 스냅샷"이라 상장폐지 판정을 맡길 수 없다.
        mark_delisted = False
        on_progress(
            f"--date 로 과거({base_date})를 지정했습니다 — 상장폐지 처리는 하지 않습니다"
        )

    base_str = base_date.strftime("%Y%m%d")
    on_progress(f"기준 영업일 {base_date}")

    window = _recent_business_days(stock, base_date, TURNOVER_WINDOW_DAYS, on_progress=on_progress)
    on_progress(f"거래대금 평균 구간 {window[0]} ~ {window[-1]} ({len(window)}영업일)")

    kospi = set(_krx_call(
        lambda: stock.get_market_ticker_list(base_str, market="KOSPI"),
        label="KOSPI 종목 목록", on_progress=on_progress,
    ))
    kosdaq = set(_krx_call(
        lambda: stock.get_market_ticker_list(base_str, market="KOSDAQ"),
        label="KOSDAQ 종목 목록", on_progress=on_progress,
    ))
    # ★ KONEX 는 넣지 않는다. `Market` 선택지가 KOSPI/KOSDAQ 뿐이고(E-04 1장),
    #   대회 유니버스에도 들어가지 않는다. `market="ALL"` 응답에서 걸러낸다.
    universe = kospi | kosdaq
    on_progress(f"대상 종목 KOSPI {len(kospi):,} · KOSDAQ {len(kosdaq):,} = {len(universe):,}종")

    # ★★ **빈 응답을 성공으로 넘기지 않는다** ─────────────────────────────────
    #
    #   pykrx 는 응답 형식이 흔들리면 **예외 대신 빈 DataFrame 을 돌려준다**
    #   (`pykrx/website/comm/util.py` 의 `dataframe_empty_handler` 가
    #   `KeyError` · `ValueError` · `JSONDecodeError` 등을 삼킨다).
    #   그러면 `retry` 도 `job_run` 의 FAILED 기록도 발동하지 않는다.
    #
    #   가드가 없으면 아래 상장폐지 처리에서 **전 종목이 뒤집힌다** —
    #   `exclude(symbol__in=set())` 를 Django 가 "조건 없음"으로 최적화하기 때문이다.
    #   그리고 잡은 "완료: 신규 0건" 과 함께 **SUCCESS 로 기록된다.**
    #   업비트 경로에는 같은 가드가 이미 있다(`_fetch_upbit_markets`).
    if not universe:
        raise ExternalDataError(
            "KRX 상장종목 목록이 비어 있습니다 (KOSPI 0 · KOSDAQ 0).",
            hint=(
                "pykrx 는 KRX 응답 형식이 바뀌면 예외 없이 빈 결과를 줍니다.\n"
                "    ① KRX 세션이 만료되지 않았는지 (KRX_ID · KRX_PW)\n"
                "    ② https://data.krx.co.kr 이 정상인지\n"
                "    ③ pykrx 버전이 KRX 변경을 따라잡았는지 확인하십시오.\n"
                "    적재를 중단했습니다 — 기존 종목 마스터는 그대로입니다."
            ),
        )

    cap_df = _krx_call(
        lambda: stock.get_market_cap(base_str, market="ALL"),
        label="시가총액·상장주식수", on_progress=on_progress,
    )
    change_df = _krx_call(
        lambda: stock.get_market_price_change(
            window[0].strftime("%Y%m%d"), base_str, market="ALL"
        ),
        label=f"{len(window)}영업일 종목명·거래대금", on_progress=on_progress,
    )
    etf = set(_krx_call(
        lambda: stock.get_etf_ticker_list(base_str), label="ETF 목록", on_progress=on_progress,
    ))
    etn = set(_krx_call(
        lambda: stock.get_etn_ticker_list(base_str), label="ETN 목록", on_progress=on_progress,
    ))

    sector_map: dict[str, tuple[str, str]] = {}
    if with_sectors:
        sector_map = _fetch_sector_map(stock, base_str, on_progress=on_progress)
        on_progress(f"업종이 매겨진 종목 {len(sector_map):,}종")

    # ── ② 받은 것을 모델로 옮긴다 ────────────────────────────────
    caps = cap_df.to_dict("index")
    changes = change_df.to_dict("index")
    now_window_days = len(window)

    objects: list[StockMaster] = []
    missing_name = 0
    for symbol in sorted(universe):
        change_row = changes.get(symbol)
        if not change_row:
            # 종목명을 얻을 곳이 여기뿐이다. 이름 없는 종목은 넣지 않는다 —
            # 화면과 대회 유니버스에 빈 이름으로 나가는 것보다 빠지는 편이 낫다.
            missing_name += 1
            continue
        cap_row = caps.get(symbol, {})
        name = str(change_row["종목명"]).strip()

        # 5일 **평균** 거래대금 — `get_market_price_change` 의 거래대금은 구간 누적이다.
        turnover_total = int(change_row.get("거래대금") or 0)

        # ★ 우선주는 업종지수에 편입되지 않는다 — 본주의 업종을 물려받는다.
        #   물려받지 않으면 우선주 113종의 업종이 통째로 비어(2026-08-13 실측)
        #   섹터 한도 판정에서 "업종 없음" 이라는 존재하지 않는 섹터가 생긴다.
        sector = sector_map.get(symbol)
        if sector is None and not symbol.endswith("0"):
            sector = sector_map.get(_base_share_symbol(symbol))

        objects.append(StockMaster(
            symbol=symbol,
            name=name[:60],
            market=Market.KOSPI if symbol in kospi else Market.KOSDAQ,
            stock_type=_classify_stock_type(symbol, name, etf, etn),
            sector_code=(sector or ("", ""))[0][:20],
            sector_name=(sector or ("", ""))[1][:40],
            market_cap=int(cap_row.get("시가총액") or 0),
            shares_outstanding=int(cap_row.get("상장주식수") or 0),
            avg_turnover_5d=turnover_total // max(now_window_days, 1),
            close_price=Decimal(int(cap_row.get("종가") or change_row.get("종가") or 0)),
            is_delisted=False,
        ))

    if missing_name:
        result.note(f"종목명을 얻지 못한 {missing_name}종은 제외했습니다")

    counts = {}
    for obj in objects:
        counts[obj.stock_type] = counts.get(obj.stock_type, 0) + 1
    result.note("종목 구분: " + " · ".join(
        f"{StockType(key).label} {value:,}" for key, value in sorted(counts.items())
    ))

    if with_sectors:
        # 업종이 안 붙는 종목이 남는 것은 정상이다 — 스팩·신규상장은 업종지수에
        # 편입되기 전이다. 다만 **몇 종이 비었는지 보이게** 한다. 조용히 비면
        # 섹터 한도 규칙이 어디까지 적용되는지 아무도 모른다.
        with_sector = sum(1 for obj in objects if obj.sector_name)
        result.note(
            f"업종 부여 {with_sector:,}/{len(objects):,}종 "
            f"(나머지 {len(objects) - with_sector}종은 업종지수 미편입 — 스팩·신규상장 등)"
        )
    else:
        result.note(
            "업종분류는 건너뛰었습니다 — 섹터 한도 규칙을 쓰려면 --with-sectors 로 "
            "다시 실행하십시오 (15분가량 걸립니다)"
        )

    # ── ③ 한 번에 쓴다 (트랜잭션 안) ─────────────────────────────
    existing = set(StockMaster.objects.values_list("symbol", flat=True))
    incoming = {obj.symbol for obj in objects}
    result.created = len(incoming - existing)
    result.updated = len(incoming & existing)

    if dry_run:
        return

    with transaction.atomic():
        # ★ **업종은 값이 있는 종목에만 쓴다.** `update_fields` 는 행마다 다르게 줄 수
        #   없으므로 두 묶음으로 나눈다. 한 번에 쓰면 이번 응답에서 업종을 못 받은
        #   종목의 `sector_name` 이 **빈 문자열로 덮여 지워진다** — 업종지수 조회가
        #   한 번 실패하거나 편입이 바뀌면 지난 실행이 채운 값을 잃는다.
        if with_sectors:
            groups = [
                ([obj for obj in objects if obj.sector_name], True),
                ([obj for obj in objects if not obj.sector_name], False),
            ]
        else:
            groups = [(objects, False)]

        for batch, write_sector in groups:
            if not batch:
                continue
            StockMaster.objects.bulk_create(
                batch,
                update_conflicts=True,
                unique_fields=["symbol"],
                update_fields=_master_update_fields(with_sectors=write_sector),
                batch_size=500,
            )

        if mark_delisted:
            # ★★ **판정 기준은 `universe`(거래소 상장목록)지 `incoming` 이 아니다.**
            #    `incoming` 은 종목명을 얻지 못해 제외된 종목이 빠진 부분집합이라,
            #    그것으로 판정하면 **이름만 못 받은 정상 상장 종목이 상장폐지로 뒤집힌다.**
            #    실측에서도 매 실행 2종이 이름 없이 걸러졌다 — 그 2종은 상장폐지가 아니다.
            delisted = (
                StockMaster.objects
                .exclude(symbol__in=universe)
                .filter(is_delisted=False)
                .update(is_delisted=True)
            )
            if delisted:
                result.note(f"조회되지 않은 {delisted}종을 상장폐지로 표시했습니다")


# ─────────────────────────────────────────────────────────────────
# 4. 업비트 마켓 — sync_upbit_markets
# ─────────────────────────────────────────────────────────────────

UPBIT_MARKET_ALL_URL = "https://api.upbit.com/v1/market/all"
UPBIT_TIMEOUT_SECONDS = 10


def _fetch_upbit_markets(*, on_progress: ProgressFn) -> list[dict]:
    """업비트 마켓 목록을 받아온다. 인증이 필요 없는 공개 API 다."""
    try:
        import requests        # noqa: PLC0415 — 다른 잡이 requests 없이도 돌게 지연 import
    except ImportError as exc:
        raise ExternalDataError(
            "requests 가 설치되어 있지 않습니다.",
            hint="backend 가상환경에서 `pip install -r requirements.txt` 를 실행하십시오.",
        ) from exc

    def _once():
        spend_api_budget("UPBIT")
        response = requests.get(
            UPBIT_MARKET_ALL_URL,
            params={"isDetails": "true"},
            timeout=UPBIT_TIMEOUT_SECONDS,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            raise ValueError(f"예상과 다른 응답 형식입니다: {type(payload).__name__}")
        return payload

    return retry(
        _once,
        label="업비트 마켓 목록",
        on_retry=lambda attempt, exc, delay: on_progress(
            f"⚠ 업비트 조회 {attempt}회 실패 ({type(exc).__name__}) — {delay:.0f}초 후 재시도"
        ),
    )


def _parse_upbit_warning(item: dict) -> bool:
    """유의 종목 여부. **스키마가 두 벌이라 둘 다 받는다.**

    ★ 2026-08-13 실측 — 업비트가 `market_event.warning` 형태로 바꿨다::

        {"market":"KRW-BTC", "market_event":{"warning":false, "caution":{...}}}

    구형은 `"market_warning": "NONE" | "CAUTION"` 이었다. 한쪽만 읽으면 스키마가
    되돌아가거나 다시 바뀌었을 때 **모든 종목이 조용히 정상으로 보인다.**
    유의 종목 표시가 사라지는 건 사용자에게 직접 손해라 양쪽을 다 본다.

    `caution`(투자유의 안내)은 `warning`(유의 종목 지정)과 다른 개념이라
    `is_warning` 에 섞지 않는다.
    """
    event = item.get("market_event") or {}
    if "warning" in event:
        return bool(event["warning"])
    legacy = item.get("market_warning")
    if legacy is not None:
        return str(legacy).upper() != "NONE"
    return False


def sync_upbit_markets(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """업비트 마켓 목록을 upsert 한다 (E-04 9.1 · F-16 6장).

    v1.0 동작을 승계하되 두 가지를 더한다:

    ★ **사라진 마켓은 `is_active=False` 로 내린다.** v1.0 은 "없는 것만 추가"라
    상장폐지된 마켓이 영원히 활성으로 남았다. 행을 지우지는 않는다 —
    과거 거래 이력이 마켓 이름을 잃으면 안 된다.

    ★ **유의 종목(`is_warning`)을 매번 갱신한다.** 지정과 해제가 오가는 값이라
    "없는 것만 추가" 로는 최신 상태를 따라갈 수 없다.
    """
    result = SyncResult()

    # ★★ 외부 조회까지 `job_run` 안에 둔다 — 사유는 `sync_trading_calendar` 의 주석 참조.
    with job_run("sync_upbit_markets", triggered_by, dry_run=dry_run) as record:
        _run_upbit_markets(result=result, dry_run=dry_run, on_progress=on_progress)
        record["rows"] = result.rows

    return result


def _run_upbit_markets(
    *, result: SyncResult, dry_run: bool, on_progress: ProgressFn
) -> None:
    """`sync_upbit_markets` 의 본체. 결과는 넘겨받은 `result` 에 채운다."""
    payload = _fetch_upbit_markets(on_progress=on_progress)

    objects = [
        UpbitMarket(
            market=item["market"][:20],
            korean_name=str(item.get("korean_name") or "")[:40],
            english_name=str(item.get("english_name") or "")[:60],
            is_warning=_parse_upbit_warning(item),
            is_active=True,
        )
        for item in payload
        if item.get("market")
    ]
    incoming = {obj.market for obj in objects}
    existing = set(UpbitMarket.objects.values_list("market", flat=True))

    result.created = len(incoming - existing)
    result.updated = len(incoming & existing)

    krw = sum(1 for market in incoming if market.startswith("KRW-"))
    warned = sum(1 for obj in objects if obj.is_warning)
    on_progress(f"마켓 {len(objects):,}개 (KRW {krw:,} · 유의 종목 {warned})")

    if dry_run:
        return

    with transaction.atomic():
        UpbitMarket.objects.bulk_create(
            objects,
            update_conflicts=True,
            unique_fields=["market"],
            update_fields=[
                "korean_name", "english_name", "is_warning", "is_active", "updated_at",
            ],
            batch_size=500,
        )
        retired = (
            UpbitMarket.objects
            .exclude(market__in=incoming)
            .filter(is_active=True)
            .update(is_active=False)
        )
        if retired:
            result.note(f"응답에 없는 {retired}개 마켓을 비활성으로 내렸습니다")


# ─────────────────────────────────────────────────────────────────
# 5. 벤치마크 지수 — sync_market_index (F-05 4.4)
# ─────────────────────────────────────────────────────────────────
#
# ★★ **왜 지수를 따로 쌓는가** ─────────────────────────────────────────────
#
#   참가자의 NAV 차트에 KOSPI·KOSDAQ 을 겹쳐 그리려면 **같은 날짜 축의 지수 종가**가
#   있어야 한다 (F-05 4.4). "벤치마크 대비 초과수익"(F-05 5장)도 이 값이 없으면
#   계산할 수 없다. 지수는 조회 시점에 외부에서 가져올 수 없다 — 랭킹 조회는
#   **절대 외부 API 를 호출하지 않는다**는 것이 F-05 7장의 규칙이기 때문이다.
#
# ★ **`nav` 컬럼을 두지 않는다** (E-04 10.1). NAV 는 "대회 시작일을 1000 으로 놓은
#   지수" 인데 이 테이블은 대회와 무관한 전역 시계열이다. 대회마다 기준일이 달라
#   한 행에 하나의 NAV 를 담을 수 없다. 종가만 저장하고 환산은 조회 시에 한다:
#
#       지수 NAV(대회 c, 날짜 d) = 1000 × close(d) / close(대회 c 의 시작일)

# pykrx 지수 티커. KRX 데이터포털의 코드 체계다.
#   1001 코스피 · 2001 코스닥
# `index_code` 컬럼에는 **사람이 읽는 이름**을 넣는다 — 화면 범례에 그대로 나가고,
# 숫자 코드는 화면에서 아무 의미가 없다.
BENCHMARK_INDICES: tuple[tuple[str, str], ...] = (
    ("1001", "KOSPI"),
    ("2001", "KOSDAQ"),
)

# 기본으로 훑는 기간(일). 매일 도는 잡이지만 **며칠치를 겹쳐 가져온다** —
# 잡이 하루 실패하면 그날 지수가 영원히 비고, 차트에 구멍이 남기 때문이다.
# upsert 라 겹쳐도 행이 늘지 않는다.
DEFAULT_INDEX_LOOKBACK_DAYS = 7


def sync_market_index(
    *,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
    base_date: date | None = None,
    days: int | None = None,
) -> SyncResult:
    """KOSPI·KOSDAQ 일별 종가를 적재한다 (F-05 4.4).

    Args:
        base_date: 조회 기준 마지막 날. 생략하면 오늘(KST).
        days: 거슬러 올라갈 달력 일수. 생략하면 7일.

    ★ **KRX 호출 2건짜리 가벼운 잡이다.** 종목 마스터(88초)와 달리 몇 초면 끝나므로
      정산 직전(15:40 이전)에 붙여도 부담이 없다.

    ★ 등락률은 pykrx 응답에 없어 **직전 종가로 직접 계산**한다. 구간 첫날은 DB 에
      이미 있는 직전 행을 본다 — 없으면 0 으로 둔다(첫 적재에는 비교 대상이 없다).
    """
    result = SyncResult()
    with job_run("sync_market_index", triggered_by, dry_run=dry_run) as record:
        _run_market_index(
            result=result, dry_run=dry_run, on_progress=on_progress,
            base_date=base_date, days=days,
        )
        record["rows"] = result.rows
    return result


def _run_market_index(
    *, result: SyncResult, dry_run: bool, on_progress: ProgressFn,
    base_date: date | None, days: int | None,
) -> None:
    """`sync_market_index` 의 본체."""
    stock = _load_pykrx()
    end = base_date or today_kst()
    span = days if days and days > 0 else DEFAULT_INDEX_LOOKBACK_DAYS
    start = end - timedelta(days=span)
    on_progress(f"지수 {len(BENCHMARK_INDICES)}종 · {start} ~ {end}")

    for ticker, code in BENCHMARK_INDICES:
        rows = _fetch_index_ohlcv(stock, start, end, ticker, on_progress=on_progress)
        if not rows:
            # ★ pykrx 는 응답 형식이 깨져도 **예외 대신 빈 DataFrame** 을 준다
            #   (`dataframe_empty_handler`). 빈 결과를 성공으로 넘기면 차트가
            #   조용히 비므로 반드시 남긴다.
            result.note(f"{code}: 조회 결과가 비었습니다 — 휴장 구간이거나 응답 형식이 바뀌었습니다")
            continue

        on_progress(f"  {code} {len(rows)}영업일 (마지막 {rows[-1][0]} {rows[-1][1]:,})")
        if dry_run:
            result.updated += len(rows)
            continue
        result.merge(_upsert_index_rows(code, rows))


def _fetch_index_ohlcv(stock, start: date, end: date, ticker: str, *, on_progress: ProgressFn):
    """`[(날짜, 종가), …]` 를 날짜 오름차순으로 돌려준다."""
    frame = _krx_call(
        lambda: stock.get_index_ohlcv(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), ticker
        ),
        label=f"지수 시세 {ticker}",
        on_progress=on_progress,
    )
    if frame is None or getattr(frame, "empty", True):
        return []

    # ★★ **파싱 실패는 우리 버그가 아니라 외부 형식 붕괴다** ────────────────────
    #
    #   `_krx_call` 은 *호출* 만 감싼다. 응답이 돌아온 뒤의 파싱에서 터지면 그대로
    #   500(우리 코드의 버그)으로 나가는데, 실제 원인은 KRX 응답 컬럼이 바뀐 것이다.
    #   원인을 잘못 가리키면 운영자가 엉뚱한 곳을 뒤진다 — 타입으로 구분해 던진다
    #   (`core/jobs.py` 의 `ExternalDataError` 주석).
    rows = []
    try:
        for stamp, item in frame.iterrows():
            close = item.get("종가")
            if close is None:
                continue
            value = Decimal(str(close))
            if value <= 0:
                # 휴장일이 0 으로 섞여 오는 경우가 있다. 0 을 저장하면 등락률이 -100% 가 된다.
                continue
            rows.append((stamp.date(), value))
    except (AttributeError, TypeError, ValueError, ArithmeticError) as exc:
        raise ExternalDataError(
            f"지수 응답을 해석하지 못했습니다 ({ticker}): {type(exc).__name__}: {exc}",
            hint=(
                "KRX 응답의 컬럼 구성이 바뀌었을 수 있습니다. "
                "`python -c \"from pykrx import stock; "
                "print(stock.get_index_ohlcv('20260810','20260813','1001').columns)\"` 로 "
                "'종가' 컬럼과 날짜 인덱스가 그대로인지 확인하십시오."
            ),
        ) from exc

    rows.sort(key=lambda row: row[0])
    return rows


def _upsert_index_rows(code: str, rows: list[tuple[date, Decimal]]) -> SyncResult:
    """지수 한 종목의 행들을 upsert 하고 등락률을 채운다."""
    result = SyncResult()
    existing = set(
        MarketIndexSnapshot.objects.filter(
            index_code=code, date__in=[row[0] for row in rows]
        ).values_list("date", flat=True)
    )

    # 구간 첫날의 비교 대상 — DB 에 이미 있는 직전 영업일 종가.
    previous = (
        MarketIndexSnapshot.objects.filter(index_code=code, date__lt=rows[0][0])
        .order_by("-date")
        .values_list("close", flat=True)
        .first()
    )

    objects = []
    for day, close in rows:
        change_pct = Decimal(0)
        if previous and previous > 0:
            change_pct = ((close / previous - 1) * 100).quantize(Decimal("0.0001"))
        objects.append(
            MarketIndexSnapshot(
                date=day, index_code=code, close=close, change_pct=change_pct
            )
        )
        previous = close

    with transaction.atomic():
        MarketIndexSnapshot.objects.bulk_create(
            objects,
            update_conflicts=True,
            unique_fields=["date", "index_code"],
            update_fields=["close", "change_pct"],
            batch_size=500,
        )
    result.created = sum(1 for day, _close in rows if day not in existing)
    result.updated = len(rows) - result.created
    return result
