"""현재가 폴링 커맨드 (F-20 잡 1).

    python manage.py poll_quotes             # 1회
    python manage.py poll_quotes --dry-run   # 무엇을 조회할지만 본다

★ 상시 기동은 `run_trading_loop` 다 (`poll_orderbook.py` 의 주석 참조).
"""

from core.jobs import SyncResult
from core.management.base import JobCommand
from market.jobs import poll_quotes


class Command(JobCommand):
    help = "우선순위 기반으로 현재가를 갱신한다 — KIS → 네이버 → 시뮬레이션 (F-20 잡 1)"
    job_name = "poll_quotes"

    def run_job(self, **options) -> SyncResult:
        return poll_quotes(
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  · {message}"),
        )
