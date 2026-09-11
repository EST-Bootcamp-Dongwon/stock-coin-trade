"""장중 스냅샷 커맨드 (F-20 잡 4 · F-05 2.3).

사용법::

    python manage.py snapshot_intraday                 # 1회 실행
    python manage.py snapshot_intraday --loop          # 10분마다 반복 (Ctrl-C 로 종료)
    python manage.py snapshot_intraday --dry-run       # 몇 명분이 찍힐지만 본다

운영에서는 pg_cron 이 **장중 10분마다** 부른다. 로컬 상시 기동이라면 `--loop` 다.

★ **장외에는 아무것도 하지 않는다.** 잡이 스스로 영업일·시간대를 보고 조기 종료한다
  (`contests.services.is_intraday_window`). cron 스케줄을 넉넉히 잡아도 안전하다.

★ 시각은 **10분 단위로 내림**된다. 09:07 에 돌든 09:09 에 돌든 09:00 칸에 들어가므로
  잡이 밀려도 행이 늘지 않는다 (`UNIQUE(participation, at)`).
"""

import time

from contests.jobs import snapshot_intraday
from contests.services import INTRADAY_INTERVAL_MINUTES
from core.jobs import ExternalDataError
from core.management.base import JobCommand


class Command(JobCommand):
    help = "참가자별 장중 수익률을 10분 간격으로 기록한다 (F-20 잡 4)"
    job_name = "snapshot_intraday"

    def add_job_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="종료하지 않고 주기마다 반복한다. 대회 기간 상시 기동용 (Ctrl-C 로 종료)",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=None,
            help=f"--loop 의 주기(초). 생략하면 {INTRADAY_INTERVAL_MINUTES}분",
        )

    def handle(self, *args, **options):
        if options["loop"]:
            return self._run_forever(**options)
        return super().handle(*args, **options)

    def run_job(self, **options):
        return snapshot_intraday(
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  {message}"),
        )

    # ── 상시 기동 ────────────────────────────────────────────────
    def _run_forever(self, **options):
        """주기마다 잡을 돌린다. `match_pending_orders --loop` 와 같은 구조다.

        ★ **한 회차가 실패해도 멈추지 않는다.** 장중 차트가 10분 비는 것과
          프로세스가 죽어 그날 내내 비는 것은 무게가 다르다.
        """
        interval = options["interval"] or INTRADAY_INTERVAL_MINUTES * 60
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{self.job_name} — {interval}초마다 반복합니다. Ctrl-C 로 종료합니다."
        ))

        rounds = written = failures = 0
        try:
            while True:
                rounds += 1
                try:
                    result = self.run_job(**options)
                except ExternalDataError as exc:
                    failures += 1
                    self.stdout.write(self.style.WARNING(f"  ⚠ {exc}"))
                except Exception as exc:            # noqa: BLE001 — 루프를 지킨다
                    failures += 1
                    self.stdout.write(self.style.ERROR(f"  ✖ {type(exc).__name__}: {exc}"))
                else:
                    if result.rows:
                        written += result.rows
                        self.stdout.write(self.style.SUCCESS(f"  {result.rows}명 기록"))
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"종료합니다 — {rounds:,}회차 · 기록 {written:,}행 · 실패 {failures}건"
            ))
