"""pg_cron 조회 — **운영 DB 에만 있는 것**을 안전하게 읽는다 (F-20 5장).

로컬 개발 DB(`pgvector/pgvector:pg16`)에는 pg_cron 이 없다. 운영은 Supabase 라 있다.
같은 코드가 양쪽에서 돌아야 하므로, 이 모듈의 함수는 **없으면 빈 목록을 준다.**
에러를 던지지 않는다 — pg_cron 이 없는 것은 로컬에서 정상이기 때문이다.

★★ **`cron.job_run_details` 로는 잡의 성공을 판정할 수 없다** ────────────────

이게 이 모듈에서 가장 중요한 사실이다.

    pg_cron  →  net.http_post(...)  →  (pg_net 백그라운드 워커)  →  Django
                └─ 여기까지가 cron 이 보는 전부. **요청을 큐에 넣은 것**

`net.http_post()` 는 **요청 id 를 즉시 반환하는 비동기 함수**다. 실제 HTTP 는
pg_net 워커가 나중에 보낸다. 그래서 Django 가 500 을 뱉든, 앱이 꺼져 있든,
`cron.job_run_details.status` 는 **`succeeded`** 로 남는다.

    cron.job_run_details  →  "SQL 이 실행됐다"        (요청을 걸었다)
    net._http_response    →  "응답이 이렇게 왔다"      (상태코드·본문)
    DataSyncLog           →  "잡이 무엇을 했다"        ★ 진실은 여기다

Admin 배너가 둘을 **나란히** 보여주는 이유가 이것이다. 한쪽만 보면
"cron 은 성공인데 데이터가 안 들어왔다"를 영원히 이해할 수 없다.
"""

import logging

from django.db import DatabaseError, connection

logger = logging.getLogger(__name__)

# 프로세스 수명 동안 캐시한다. 확장 설치 여부가 요청 중에 바뀌는 일은 없고,
# Admin 을 열 때마다 `pg_extension` 을 뒤질 이유도 없다.
# ★ 운영 DB 에 pg_cron 을 **새로 설치한 직후에는 앱을 재기동**해야 화면에 나타난다.
_available: bool | None = None


def pg_cron_available(*, refresh: bool = False) -> bool:
    """이 DB 에 pg_cron 이 설치돼 있는가."""
    global _available
    if _available is None or refresh:
        _available = _probe()
    return _available


def _probe() -> bool:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_cron'")
            return cursor.fetchone() is not None
    except DatabaseError as exc:
        # 권한 부족·연결 문제. 없는 것으로 보고 넘어간다 — 운영 화면 하나 때문에
        # Admin 전체가 죽으면 안 된다.
        logger.warning("pg_cron 설치 여부를 확인하지 못했습니다: %s", exc)
        return False


def _query(sql: str, params: tuple = ()) -> list[dict]:
    """`cron` 스키마 조회. 실패하면 빈 목록.

    ★ 조회 전에 `pg_cron_available()` 로 먼저 거른다. 없는 테이블을 조회하면
      예외가 나고, **`ATOMIC_REQUESTS` 를 켠 배포에서는 그 트랜잭션 전체가
      깨져** 이후 쿼리까지 줄줄이 실패한다. 예외를 잡는 것만으로는 부족하다.
    """
    if not pg_cron_available():
        return []
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except DatabaseError as exc:
        logger.warning("cron 스키마 조회 실패: %s", exc)
        return []


def scheduled_jobs() -> list[dict]:
    """등록된 cron 잡 목록. 스케줄은 **UTC** 기준 문자열이다 (F-20 2장)."""
    return _query(
        """
        SELECT jobid, jobname, schedule, active
          FROM cron.job
         ORDER BY jobname
        """
    )


def recent_runs(limit: int = 10, *, failed_only: bool = False) -> list[dict]:
    """`cron.job_run_details` 최근 실행.

    잡 이름은 `cron.job` 에 있으므로 조인해 온다. **LEFT JOIN 인 이유** — 잡을
    `cron.unschedule` 로 지워도 실행 이력은 남는다. INNER JOIN 이면 그 이력이
    통째로 사라져, 방금 지운 잡이 왜 실패했는지 볼 수 없게 된다.
    """
    where = "WHERE d.status <> 'succeeded'" if failed_only else ""
    return _query(
        f"""
        SELECT d.runid, d.jobid, j.jobname, d.status, d.return_message,
               d.start_time, d.end_time
          FROM cron.job_run_details d
          LEFT JOIN cron.job j ON j.jobid = d.jobid
          {where}
         ORDER BY d.start_time DESC
         LIMIT %s
        """,
        (limit,),
    )
