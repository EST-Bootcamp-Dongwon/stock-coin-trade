"""호가 폴링 커맨드 (F-20 잡 2).

    python manage.py poll_orderbook             # 1회
    python manage.py poll_orderbook --dry-run   # 무엇을 조회할지만 본다

★ **상시 기동은 이 커맨드가 아니라 `run_trading_loop` 다.** 잡 1·2·3 은 순서가
  중요해서(시세를 갱신한 **뒤에** 체결해야 한다) 한 프로세스가 이어서 돌린다.
  이 커맨드는 단독 점검·수동 재실행용이다.
"""

from core.jobs import SyncResult
from core.management.base import JobCommand
from market.jobs import poll_orderbook


class Command(JobCommand):
    help = "대회 종목의 호가 10단계를 KIS 로 갱신한다 (F-20 잡 2)"
    job_name = "poll_orderbook"

    def run_job(self, **options) -> SyncResult:
        return poll_orderbook(
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  · {message}"),
        )
