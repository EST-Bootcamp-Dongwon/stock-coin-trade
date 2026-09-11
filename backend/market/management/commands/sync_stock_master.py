"""종목 마스터 적재 — 운영 초기 데이터 ② (05 문서 5.3 · F-16 5장 · E-04 3장).

사용법::

    python manage.py sync_stock_master                     # 최근 영업일 기준 ~2,700종
    python manage.py sync_stock_master --with-sectors      # + KRX 업종분류 (10분 이상)
    python manage.py sync_stock_master --date 2026-08-12   # 특정 영업일 기준
    python manage.py sync_stock_master --dry-run

운영에서는 pg_cron 이 **매 영업일 16:00 KST** 에 HTTP 로 같은 서비스 함수를 부른다
(F-20 잡 8번). 이 커맨드는 그 잡을 손으로 돌리는 입구다.

★ **`--with-sectors` 를 언제 쓰는가** — 업종분류는 대회의 **섹터 한도 규칙**에만
   쓰인다 (F-04). 업종지수 50여 개의 구성종목을 하나씩 받아야 해서 10분이 넘는다.
   매일 바뀌는 값이 아니므로 대회를 열기 전에 한 번 돌려두면 된다.

★ **덮어쓰지 않는 필드가 있다** — `listing_date` · `is_supervised` · `alert_level` 은
   **pykrx 가 주지 않는다**(2026-08-13 실측). `is_featured` 는 `seed_demo` 가 켠
   수업용 14종 표시다. 넷 다 배치가 손대지 않으므로 Admin 에서 관리한다.
"""

from datetime import datetime

from django.core.management.base import CommandError

from core.management.base import JobCommand
from market.services import sync_stock_master


class Command(JobCommand):
    help = "pykrx 로 종목 마스터(~2,700종)를 StockMaster 에 적재한다"
    job_name = "sync_stock_master"

    def add_job_arguments(self, parser):
        parser.add_argument(
            "--date",
            default=None,
            metavar="YYYY-MM-DD",
            help=(
                "기준 영업일 (기본: KRX 가 아는 최근 영업일). "
                "과거를 지정하면 상장폐지 판정은 하지 않는다"
            ),
        )
        parser.add_argument(
            "--with-sectors",
            action="store_true",
            help="KRX 업종분류까지 채운다 — 섹터 한도 규칙에 필요하다. **10분 이상 걸린다**",
        )

    def run_job(self, **options):
        base_date = None
        if options["date"]:
            try:
                base_date = datetime.strptime(options["date"], "%Y-%m-%d").date()
            except ValueError as exc:
                raise CommandError(
                    f"--date 는 YYYY-MM-DD 형식입니다: {options['date']!r}"
                ) from exc

        if options["with_sectors"]:
            self.stdout.write(self.style.WARNING(
                "  --with-sectors: 업종지수 50여 개의 구성종목을 받습니다. "
                "10분 이상 걸립니다."
            ))

        return sync_stock_master(
            base_date=base_date,
            with_sectors=options["with_sectors"],
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  {message}"),
        )
