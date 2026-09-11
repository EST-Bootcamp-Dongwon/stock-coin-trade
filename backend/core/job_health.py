"""배치 건강 상태 요약 — Admin 경고 배너의 재료 (F-20 5장 · E-07 2장).

**"조용히 실패하는 배치"를 없애는 것이 목적이다.** v1.0 스케줄러는 실패해도 로그만
남겼고, 서버리스에서는 그 로그를 보기도 어려웠다. 운영자가 매일 여는 화면(Admin
대시보드) 맨 위에 문제를 올려 **찾지 않아도 보이게** 한다.

이 모듈이 잡아내는 3종 ─────────────────────────────────────────────────────

  ① **실패** — 잡의 **마지막** 실행이 FAILED.
       왜 "마지막"인가: 어제 실패했다가 오늘 성공한 잡은 이미 해결된 것이다.
       과거 실패를 계속 띄우면 배너가 늘 빨간 상태가 되어 아무도 안 본다.

  ② **자동 비활성화** — 3회 연속 실패. 스케줄러 경로(HTTP)가 이 잡을 건너뛴다.
       ①과 나누는 이유: "한 번 실패"와 "이제 아예 안 돈다"는 급이 다르다.

  ③ **정체(stuck)** — RUNNING 인 채 오래 남은 행.
       `job_run` 은 `BaseException` 까지 잡아 FAILED 로 닫으므로 Ctrl-C 는 여기
       걸리지 않는다. **걸리는 것은 프로세스가 통째로 죽은 경우다** — OOM kill,
       SIGKILL, 컨테이너 강제 종료, 서버리스 실행시간 초과. `finally` 조차 못 돌아
       DB 에는 RUNNING 이 영원히 남는다. 이건 실패보다 더 조용한 실패다.

Django 관점 — FastAPI 였다면 `/health` 엔드포인트를 만들고 모니터링 도구를 붙였을
것이다. Django 는 `AdminSite.each_context()` 로 **이미 있는 화면**에 값을 얹을 수
있어, 새 인프라 없이 관측 경로가 생긴다.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from core import pg_cron
from core.constants import SyncStatus
from core.jobs import CONSECUTIVE_FAILURE_LIMIT, has_consecutive_failures
from core.models import DataSyncLog

# 이 시간을 넘겨 RUNNING 인 채 남아 있으면 정체로 본다.
# 가장 긴 잡(`sync_stock_master --with-sectors`)이 실측 15분이라 넉넉히 잡았다.
STUCK_AFTER = timedelta(hours=1)

# 배너에 실을 pg_cron 실행 이력 줄 수.
CRON_RUN_LIMIT = 5


@dataclass
class JobAlert:
    """배너 한 줄."""

    job_name: str
    started_at: object
    kind: str                 # "FAILED" | "STUCK"
    disabled: bool = False    # 3회 연속 실패로 스케줄러가 건너뛰는 중인가
    detail: str = ""          # 실패 사유 한 줄

    @property
    def is_failed(self) -> bool:
        return self.kind == "FAILED"


def collect_job_alerts() -> dict:
    """대시보드 배너에 필요한 것을 한 번에 모은다.

    ★ **잡이 하나도 실패하지 않았으면 `has_problem` 이 False 다.** 그때 배너는
    아예 그리지 않는다 — 평소에 아무것도 안 보여야 빨간 배너가 뜬 날 눈에 띈다.
    """
    alerts = _latest_run_alerts()
    return {
        "alerts": alerts,
        "has_problem": bool(alerts),
        "failure_limit": CONSECUTIVE_FAILURE_LIMIT,
        "cron": _cron_section(),
    }


def _latest_run_alerts() -> list[JobAlert]:
    """잡마다 **마지막 실행 1건**만 보고 판정한다.

    Django 관점 — `.distinct("job_name")` 은 Postgres 의 `SELECT DISTINCT ON` 이다.
    MySQL 에는 없어 v1.0 이었다면 서브쿼리나 파이썬 루프로 풀었을 것이다.
    `order_by` 의 **첫 필드가 distinct 필드와 같아야** 한다는 제약이 있다.
    """
    latest = (
        DataSyncLog.objects.order_by("job_name", "-started_at")
        .distinct("job_name")
    )

    stuck_before = timezone.now() - STUCK_AFTER
    alerts: list[JobAlert] = []

    for log in latest:
        if log.status == SyncStatus.FAILED:
            alerts.append(
                JobAlert(
                    job_name=log.job_name,
                    started_at=log.started_at,
                    kind="FAILED",
                    # 잡별로 1회씩만 부른다. 실패한 잡이 있을 때만 도는 쿼리다.
                    disabled=has_consecutive_failures(log.job_name),
                    detail=_error_headline(log.error),
                )
            )
        elif log.status == SyncStatus.RUNNING and log.started_at < stuck_before:
            alerts.append(
                JobAlert(
                    job_name=log.job_name,
                    started_at=log.started_at,
                    kind="STUCK",
                    detail=(
                        f"{STUCK_AFTER.total_seconds() / 3600:.0f}시간 넘게 '실행중' 입니다 — "
                        "프로세스가 강제 종료됐을 수 있습니다."
                    ),
                )
            )

    # 자동 비활성화된 잡을 맨 위로. 그다음 실패, 그다음 정체.
    alerts.sort(key=lambda a: (not a.disabled, not a.is_failed))
    return alerts


def _error_headline(traceback_text: str) -> str:
    """스택트레이스에서 **마지막 줄**만 뽑는다.

    마지막 줄이 예외 타입과 메시지다 — 배너에 필요한 것은 그것뿐이고,
    전문은 링크를 눌러 `DataSyncLog` 상세에서 본다.
    """
    lines = [line.strip() for line in (traceback_text or "").strip().splitlines() if line.strip()]
    if not lines:
        return ""
    headline = lines[-1]
    return headline if len(headline) <= 160 else headline[:160] + "…"


def _cron_section() -> dict:
    """pg_cron 쪽 상황. 없으면 "없다"고 분명히 말한다.

    ★ 로컬에서 "pg_cron 없음"은 **에러가 아니라 정상**이다. 그 사실을 화면에
      적어 두지 않으면, 배너에 cron 정보가 비어 있는 것을 보고 운영자가
      "스케줄러가 죽었나" 로 오해한다.
    """
    if not pg_cron.pg_cron_available():
        return {
            "available": False,
            "note": "이 데이터베이스에는 pg_cron 이 없습니다 (로컬 개발 DB 에서는 정상입니다).",
            "jobs": [],
            "runs": [],
        }

    jobs = pg_cron.scheduled_jobs()
    return {
        "available": True,
        "note": "",
        "jobs": jobs,
        "inactive_count": sum(1 for job in jobs if not job.get("active")),
        "runs": pg_cron.recent_runs(CRON_RUN_LIMIT),
        "failed_runs": pg_cron.recent_runs(CRON_RUN_LIMIT, failed_only=True),
    }
