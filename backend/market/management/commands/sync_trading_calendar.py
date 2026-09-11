"""영업일 적재 — 운영 초기 데이터 ① (05 문서 5.3 · E-04 4장).

사용법::

    python manage.py sync_trading_calendar --year 2026
    python manage.py sync_trading_calendar --year 2026 --dry-run   # 무엇이 바뀔지만 본다
    python manage.py sync_trading_calendar --year 2025 --soft-fail # 실패해도 종료코드 0

★ **미래는 채워지지 않는다.** KRX 는 지나간 영업일만 안다. 올해를 지정하면
   오늘까지만 들어가고, 남은 달은 그 달이 지난 뒤 다시 돌려야 한다.
   자세한 이유는 `market.services.sync_trading_calendar` 참조.

★ **운영자가 Admin 에서 고친 날(`source=MANUAL`)은 덮어쓰지 않는다.**
   임시 휴장 지정이 다음 배치에 되살아나면 그날 대회 정산이 도는 사고가 난다.
"""

from core.management.base import JobCommand
from core.time import today_kst
from market.services import sync_trading_calendar


class Command(JobCommand):
    help = "pykrx 로 한 해의 영업일을 TradingCalendar 에 채운다"
    job_name = "sync_trading_calendar"

    def add_job_arguments(self, parser):
        parser.add_argument(
            "--year",
            type=int,
            default=None,
            metavar="YYYY",
            help="대상 연도 (기본: 올해). 미래 연도는 조회할 수 없다",
        )

    def run_job(self, **options):
        year = options["year"] or today_kst().year
        return sync_trading_calendar(
            year=year,
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  {message}"),
        )
