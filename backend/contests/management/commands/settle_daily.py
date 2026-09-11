"""일별 정산 커맨드 (F-20 잡 7 · F-05 4.1).

사용법::

    python manage.py settle_daily                    # 오늘(KST) 정산
    python manage.py settle_daily --date 2026-08-13  # 특정 영업일을 손으로 메운다
    python manage.py settle_daily --dry-run          # 무엇이 계산될지만 본다

운영에서는 pg_cron 이 **매 영업일 15:40 KST** 에 부른다.

★★ **몇 번을 돌려도 안전하다** ─────────────────────────────────────────────

`DailySnapshot` 은 `UNIQUE(participation, date)` 라 같은 날짜로 다시 돌리면
**덮어쓴다.** 그래서 이런 재실행이 정상 운영 절차다::

    15:40  자동 정산 — 15:30 마지막 폴링값으로 평가
    16:05  `sync_stock_master` 가 끝난 뒤 손으로 한 번 더 — 확정 종가로 덮어쓴다

두 번째를 꼭 해야 하는 것은 아니다. 차이는 대개 0 이다
(`contests.services.closing_prices` 의 주석 참조).

★ **`--date` 를 주면 휴장일 판정을 건너뛴다.** 달력이 아직 안 채워졌거나 공휴일
  판정이 틀렸을 때 손으로 메우는 경로다. 자동 실행(cron)은 날짜를 넘기지 않으므로
  휴장일에는 아무것도 하지 않는다.
"""

from datetime import datetime

from django.core.management.base import CommandError

from contests.jobs import settle_daily
from core.management.base import JobCommand


class Command(JobCommand):
    help = "참가자별 일별 스냅샷·NAV·순위·위반·주간 회전율 잠정치를 기록한다 (F-20 잡 7)"
    job_name = "settle_daily"

    def add_job_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            metavar="YYYY-MM-DD",
            help="정산할 영업일 (기본: 오늘 KST). 지정하면 휴장일 판정을 건너뛴다",
        )

    def run_job(self, **options):
        return settle_daily(
            target_date=_parse_date(options["date"]),
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  {message}"),
        )


def _parse_date(raw: str | None):
    """`YYYY-MM-DD` 를 `date` 로. 형식이 틀리면 **무엇이 맞는지 알려준다** (규약 8.5)."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(
            f"--date 는 YYYY-MM-DD 형식이어야 합니다 (받은 값: {raw!r}). 예: --date 2026-08-13"
        ) from exc
