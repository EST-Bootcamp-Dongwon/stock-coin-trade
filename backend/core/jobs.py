"""배치 잡 실행 래퍼 (E-07 2.2).

모든 잡이 같은 방식으로 기록되게 컨텍스트 매니저를 둔다.

Django 관점 — FastAPI + APScheduler 에서는 데코레이터로 감싸는 게 흔했다.
여기서는 HTTP 엔드포인트(pg_cron 호출)와 management command 양쪽에서
같은 함수를 쓰므로, 데코레이터보다 컨텍스트 매니저가 재사용에 유리하다.

이 모듈이 담는 것은 **잡의 실행 정책**이다 — 기록(`job_run`) · 재시도(`retry`) ·
연속 실패 판정(`has_consecutive_failures`) · 결과 형식(`SyncResult`).
데이터를 어디서 어떻게 가져오는지는 각 앱의 `services.py` 가 안다.
"""

import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field

from django.utils import timezone

from core.constants import SyncStatus, TriggeredBy
from core.models import DataSyncLog

# 3회 연속 실패하면 잡을 끄고 운영자에게 알린다.
# 외부 API 장애 때 무한 재시도로 유량을 태우는 것을 막는다 (E-07 2.1).
CONSECUTIVE_FAILURE_LIMIT = 3

# 외부 호출 1회에 대한 재시도 기본값. 지수 백오프라 2초 → 4초 → 8초로 벌어진다.
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY = 2.0


class ExternalDataError(Exception):
    """외부 데이터 소스를 쓸 수 없다 — 네트워크 · 인증 · 응답 형식 붕괴.

    ★ **우리 코드의 버그와 외부 장애를 타입으로 구분한다.** 이 구분이 있어야
    "네트워크가 죽어도 배포는 계속된다"(05 문서 5.3)를 커맨드 계층에서 판단할 수 있다.
    `--soft-fail` 은 이 예외만 삼키고, `KeyError` 같은 진짜 버그는 그대로 터뜨린다.

    `hint` 에는 **운영자가 무엇을 해야 하는지**를 적는다 (규약 8.5 —
    "환경 가드는 막다른 길로 만들지 않는다"). 예외 메시지가 "실패했습니다" 로
    끝나면 받는 사람이 할 수 있는 일이 없다.
    """

    def __init__(self, message: str, *, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@dataclass
class SyncResult:
    """동기화 결과. 커맨드가 화면에 뿌리고, `job_run` 이 `rows_affected` 로 기록한다.

    `created` / `updated` 를 나누는 이유 — **두 번째 실행부터는 `created` 가 0이어야
    정상이다.** 매번 created 가 수천 건이면 upsert 키가 잘못 잡힌 것이고,
    이건 숫자를 나눠 보지 않으면 드러나지 않는다 (F-20 6장 멱등성).

    `skipped` 는 "일부러 건드리지 않은 행"이다. 운영자가 손으로 고친
    `TradingCalendar(source=MANUAL)` 처럼, 배치가 **의도적으로 비켜간** 것을 센다.
    """

    created: int = 0
    updated: int = 0
    skipped: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def rows(self) -> int:
        """`DataSyncLog.rows_affected` 에 들어갈 값. 건드리지 않은 행은 빼고 센다."""
        return self.created + self.updated

    def note(self, message: str) -> None:
        """사람이 읽을 한 줄을 덧붙인다. 커맨드가 그대로 출력한다."""
        self.notes.append(message)

    def merge(self, other: "SyncResult") -> "SyncResult":
        """부분 결과를 합친다. 월별·시장별로 나눠 처리하는 잡이 쓴다."""
        self.created += other.created
        self.updated += other.updated
        self.skipped += other.skipped
        self.notes.extend(other.notes)
        return self


@contextmanager
def job_run(
    job_name: str,
    triggered_by: str = TriggeredBy.CRON,
    *,
    dry_run: bool = False,
    coalesce_idle: bool = False,
):
    """잡 실행을 감싸 시작·종료·실패를 자동 기록한다.

    사용법::

        with job_run("sync_stock_master") as result:
            result["rows"] = upsert_stock_master()

    `result["rows"]` 에 처리 행 수를 넣으면 `DataSyncLog.rows_affected` 에 들어간다.
    예외는 삼키지 않고 다시 던진다 — 호출자가 실패를 알아야 하기 때문이다.

    Args:
        coalesce_idle: **아무 일도 하지 않은 실행을 새 행으로 남기지 않는다.**
            고빈도 잡(5~10초)이 쓴다. 아래 설명 참조.

    ★ **`dry_run=True` 면 `DataSyncLog` 를 남기지 않는다.** 연습 실행이 이력에 섞이면
    `has_consecutive_failures()` 판정과 Admin 대시보드의 "마지막 성공 시각"이
    거짓이 된다. dry-run 은 *무엇이 바뀔지 보는 것*이지 *실행한 것*이 아니다.

    ★★ **`coalesce_idle` — 고빈도 잡의 이력 정책** ────────────────────────────

    `match_pending_orders` 는 장중 5초마다 돈다. 매 실행을 한 행으로 남기면
    **하루 4,680행**이고, 그중 대부분은 "체결할 게 없었다" 이다. 그대로 두면
    Admin 의 배치 이력 화면이 무변화 행으로 덮여 정작 봐야 할 실패가 묻힌다.

    그래서 이 모드는 **결과에 따라 행을 다르게 쓴다**::

        처리 행 > 0  또는 실패  →  새 행을 남긴다        (실제로 일어난 일)
        처리 행 = 0  이고 성공  →  직전 무변화 행을 갱신  (조용한 구간을 늘린다)

    갱신할 때 **`started_at` 은 건드리지 않고 `finished_at` 만 민다.** 그러면 한 행이
    *조용했던 구간*을 그대로 나타낸다 — "09:00:05 부터 10:23:41 까지 체결 없음".
    횟수를 세는 컬럼을 새로 만들지 않아도 의미가 산다.

    ★ 대신 **이 모드는 `RUNNING` 행을 만들지 않는다.** 무변화인지는 끝나 봐야 알기
      때문이다. `core/job_health.py` 의 정체(STUCK) 감지는 `RUNNING` 이 오래 남는 것을
      보는 방식이라 이 잡에는 걸리지 않는다. 5초 잡이 통째로 멈춘 것은 "마지막 행의
      `finished_at` 이 오래됐다" 로 판정해야 하고, 그건 실행처를 정한 뒤의 과제다
      (→ 변경노트 E-37).
    """
    if dry_run:
        yield {"rows": 0}
        return

    # coalesce_idle 이면 시작 시점에 행을 만들지 않는다 — 무엇을 남길지는 끝나 봐야 안다.
    log = (
        None
        if coalesce_idle
        else DataSyncLog.objects.create(
            job_name=job_name, started_at=timezone.now(), triggered_by=triggered_by
        )
    )
    started_at = timezone.now()
    result = {"rows": 0}
    try:
        yield result
    except BaseException:
        if log is None:
            # 실패는 **언제나 자기 행을 갖는다.** 조용한 구간에 섞으면 사라진다.
            log = DataSyncLog.objects.create(
                job_name=job_name, started_at=started_at, triggered_by=triggered_by
            )
        # ★ `Exception` 이 아니라 `BaseException` 인 이유 — **Ctrl-C 로 끊은 잡도 실패다.**
        #   `KeyboardInterrupt` · `SystemExit` 은 `Exception` 을 상속하지 않아
        #   `except Exception` 으로는 잡히지 않는다. 그러면 `else` 도 건너뛰어
        #   상태가 `RUNNING` 인 채 `finished_at` 만 찍힌 **모순된 행**이 남고,
        #   Admin 대시보드는 그 잡을 "아직 돌고 있음"으로 보여준다.
        #   다시 던지므로 중단 동작 자체는 그대로다.
        log.status = SyncStatus.FAILED
        log.error = traceback.format_exc()
        raise
    else:
        if log is None:
            log = _idle_log_for(job_name, started_at, triggered_by, rows=result["rows"])
        log.status = SyncStatus.SUCCESS
    finally:
        # ★ **성공·실패 모두 `rows_affected` 를 남긴다.** 실패한 잡에도 이미 커밋된 행이
        #   있을 수 있다 — 달력 잡은 월별로 나눠 쓰므로 5월에서 실패해도 1~4월은 DB 에
        #   남는다. 실패했다고 0 으로 적으면 "아무것도 안 됐다"로 읽혀 운영자가
        #   복구 범위를 잘못 판단한다. 잡은 **어디까지 갔는지**를 말해야 한다.
        #
        # ★ `started_at` 은 여기서 건드리지 않는다. 무변화 행을 이어 쓰는 경우
        #   그 값이 **조용한 구간의 시작**을 가리키기 때문이다 (docstring 참조).
        log.rows_affected = result["rows"]
        log.finished_at = timezone.now()
        log.save(update_fields=["status", "error", "rows_affected", "finished_at"])


def _idle_log_for(job_name: str, started_at, triggered_by: str, *, rows: int) -> DataSyncLog:
    """`coalesce_idle` 잡이 성공했을 때 쓸 `DataSyncLog` 행을 고른다.

    처리 행이 있으면 **새 행**, 하나도 없으면 **직전 무변화 행**을 이어 쓴다.
    이어 쓸 무변화 행이 없으면(잡의 첫 실행이거나 직전이 실패였다면) 새로 만든다.

    ★ 이어 쓸 대상을 "마지막 행"으로만 한정하는 이유 — 중간에 체결이 한 번 일어나면
      그 행이 마지막이 되므로 **다음 조용한 구간은 새 행에서 다시 시작**한다.
      구간이 시간순으로 끊어져 이력이 이야기처럼 읽힌다.
    """
    if rows == 0:
        last = DataSyncLog.objects.filter(job_name=job_name).order_by("-started_at").first()
        if last is not None and last.status == SyncStatus.SUCCESS and last.rows_affected == 0:
            return last
    return DataSyncLog.objects.create(
        job_name=job_name, started_at=started_at, triggered_by=triggered_by
    )


def has_consecutive_failures(job_name: str, limit: int = CONSECUTIVE_FAILURE_LIMIT) -> bool:
    """최근 `limit` 건이 전부 실패인가.

    참이면 스케줄러가 이 잡을 건너뛰고 Admin 에 경고를 띄운다.
    """
    recent = list(
        DataSyncLog.objects.filter(job_name=job_name)
        .order_by("-started_at")
        .values_list("status", flat=True)[:limit]
    )
    return len(recent) == limit and all(s == SyncStatus.FAILED for s in recent)


def retry(
    fn,
    *,
    attempts: int = RETRY_ATTEMPTS,
    base_delay: float = RETRY_BASE_DELAY,
    label: str = "",
    on_retry=None,
):
    """외부 호출 1회를 지수 백오프로 재시도한다.

    Args:
        fn: 인자 없는 호출 가능 객체. `lambda: stock.get_market_cap(d)` 처럼 감싸 넘긴다.
        attempts: 총 시도 횟수. 3이면 최초 1회 + 재시도 2회다.
        base_delay: 첫 대기 시간(초). 2초 → 4초 → 8초로 두 배씩 벌어진다.
        label: 로그에 찍을 이름. 어느 호출이 흔들리는지 알아야 원인을 좁힌다.
        on_retry: `(시도번호, 예외, 대기초)` 를 받는 콜백. 커맨드가 진행 상황을 찍는 데 쓴다.

    Returns:
        `fn()` 의 반환값.

    Raises:
        ExternalDataError: 모든 시도가 실패한 경우. 마지막 예외를 `__cause__` 로 단다.

    ★ **왜 고정 간격이 아니라 지수 백오프인가** — 외부 API 가 유량 제한으로 거절하는
    중이라면 같은 간격으로 두드리는 것은 상황을 악화시킨다. KRX·업비트처럼 **우리가
    제어할 수 없는 남의 서버**에는 물러서는 쪽이 맞다.

    ★ **재시도하면 안 되는 것도 있다.** 인증 실패(잘못된 자격증명)는 몇 번을 다시
    해도 같은 결과다. 그런 것은 호출부가 `ExternalDataError` 로 **직접 던져** 이 함수를
    거치지 않게 한다.
    """
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ExternalDataError:
            # 호출부가 "재시도해도 소용없다"고 판단해 던진 것이다. 그대로 올려보낸다.
            raise
        except Exception as exc:                 # noqa: BLE001 — 외부 라이브러리의 예외는 종류를 특정할 수 없다
            last_error = exc
            if attempt >= attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            if on_retry:
                on_retry(attempt, exc, delay)
            time.sleep(delay)

    name = label or getattr(fn, "__name__", "외부 호출")
    raise ExternalDataError(
        f"{name} 이(가) {attempts}회 모두 실패했습니다: "
        f"{type(last_error).__name__}: {last_error}",
        hint="네트워크 · 외부 서비스 상태를 확인한 뒤 다시 실행하십시오.",
    ) from last_error
