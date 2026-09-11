"""벤치마크 지수 적재 커맨드 (F-05 4.4).

사용법::

    python manage.py sync_market_index                      # 최근 7일
    python manage.py sync_market_index --days 90            # 대회 시작 전 과거 채우기
    python manage.py sync_market_index --date 2026-08-13    # 그날까지를 기준으로
    python manage.py sync_market_index --dry-run

운영에서는 pg_cron 이 **매 영업일 15:40 직전**(잡 7 앞)에 부른다.

★ **NAV 차트의 두 번째·세 번째 선이 이 데이터다.** 참가자 포트폴리오만 그리면
  "10% 벌었다" 가 잘한 것인지 알 수 없다 — 같은 기간 KOSPI 가 15% 올랐다면
  못한 것이다. F-05 3.3 의 상세 모달이 세 선을 겹쳐 그리는 이유다.

★★ **대회를 열기 전에 `--days` 로 과거를 먼저 채우십시오.** 지수 NAV 는
  **대회 시작일 종가를 1000 으로** 환산하는데, 시작일 행이 없으면 기준을 잡을 수
  없어 차트에 벤치마크가 통째로 빠진다.
"""

from datetime import datetime

from django.core.management.base import CommandError

from core.management.base import JobCommand
from market.services import DEFAULT_INDEX_LOOKBACK_DAYS, sync_market_index


class Command(JobCommand):
    help = "KOSPI·KOSDAQ 일별 종가를 MarketIndexSnapshot 에 upsert 한다 (F-05 4.4)"
    job_name = "sync_market_index"

    def add_job_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            default=None,
            metavar="YYYY-MM-DD",
            help="조회 기준 마지막 날 (기본: 오늘 KST)",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=None,
            help=f"거슬러 올라갈 달력 일수 (기본 {DEFAULT_INDEX_LOOKBACK_DAYS}일)",
        )

    def run_job(self, **options):
        return sync_market_index(
            base_date=_parse_date(options["date"]),
            days=options["days"],
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
            f"--date 는 YYYY-MM-DD 형식이어야 합니다 (받은 값: {raw!r})."
        ) from exc
