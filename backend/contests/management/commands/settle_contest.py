"""대회 상태 전이 · 최종 정산 커맨드 (F-20 잡 10 · F-05 4.3 · F-02 2장).

사용법::

    python manage.py settle_contest                      # 오늘(KST) 기준으로 상태를 민다
    python manage.py settle_contest --date 2026-09-01    # 그날인 것처럼 판정
    python manage.py settle_contest --dry-run            # 무엇이 바뀔지만 본다

운영에서는 pg_cron 이 **매일 06:00 KST** 에 부른다.

무슨 일이 일어나는가::

    UPCOMING → ONGOING    시작일 도달. **이때 유니버스를 얼린다** (F-02 3.4)
    ONGOING  → SETTLING   종료일 경과 (종료일 당일은 그대로 둔다 — 15:40 정산이 남았다)
    SETTLING → CLOSED     관리 점수·백분위·등급 확정 → ContestResult → 계좌 동결

★★ **대회를 열기 전에 종목 마스터를 채워 두십시오** ────────────────────────

유니버스는 `StockMaster` 에서 **업종이 매겨진 보통주**를 긁어 만든다. 마스터가
비어 있으면 유니버스도 비고, 그러면 **대회 첫날에 아무도 매수할 수 없다**
(업종을 모르는 종목은 매수가 막힌다 — F-04 2장 조건 6 · E-31)::

    python manage.py sync_stock_master --with-sectors    # 15분쯤 걸린다

잡이 빈 유니버스를 만들면 `notes` 에 경고를 남기지만, 그때는 이미 대회가 시작된
뒤다. 먼저 채우는 편이 낫다.

★ **`DRAFT → UPCOMING` 은 자동으로 하지 않는다.** "이 대회를 공개한다" 는 운영자의
  결정이다. Admin 에서 직접 바꾼다.
"""

from datetime import datetime

from django.core.management.base import CommandError

from contests.jobs import settle_contest
from core.management.base import JobCommand


class Command(JobCommand):
    help = "대회 상태를 전이시키고 종료된 대회를 최종 확정한다 (F-20 잡 10)"
    job_name = "settle_contest"

    def add_job_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            metavar="YYYY-MM-DD",
            help="판정 기준일 (기본: 오늘 KST). 리허설·복구용",
        )

    def run_job(self, **options):
        return settle_contest(
            target_date=_parse_date(options["date"]),
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  {message}"),
        )


def _parse_date(raw: str | None):
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(
            f"--date 는 YYYY-MM-DD 형식이어야 합니다 (받은 값: {raw!r}). 예: --date 2026-09-01"
        ) from exc
