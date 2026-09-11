"""미체결 주문 체결 추종 커맨드 (F-20 잡 3).

    python manage.py match_pending_orders              # 1회 실행
    python manage.py match_pending_orders --loop       # 상시 기동 (Ctrl-C 로 종료)
    python manage.py match_pending_orders --dry-run    # 무엇이 체결될지만 본다

★★ **`--loop` 가 이 커맨드의 존재 이유다** ─────────────────────────────────

이 잡은 5초마다 돌아야 하는데, **비용을 들이지 않고 파이썬을 상시 돌릴 곳이
마땅치 않다** (변경노트 E-28 · E-36):

    Vercel Hobby   함수 실행 10초 한도 · Active CPU 월 4시간
    Supabase       pg_cron 은 무료지만 SQL 만 돌린다

그래서 대회 기간에는 **로컬에서 이 커맨드를 띄워 두는 것**이 가장 단순하고 확실한
답이다. 비용이 0 이고, 주기도 우리가 정한다. 나중에 상시 기동 서버가 생기면
같은 커맨드를 그대로 옮기면 되고, pg_cron 을 쓰기로 하면
`/internal/jobs/match-pending-orders` 를 부르면 된다 — **체결 로직은 셋 다 같다.**

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 였다면 `APScheduler` 를 앱 안에 띄우거나 `asyncio.create_task` 로 백그라운드
루프를 돌렸을 것이다. Django 에서도 `AppConfig.ready()` 에 같은 짓을 할 수 있지만
**하지 않는다** — `ready()` 는 `migrate` · `shell` · `test` 에서도 불려서
마이그레이션 중에 체결 잡이 뜨는 사고가 난다 (F-20 1장).

**별도 프로세스로 띄우는 편이 정직하다.** 죽으면 죽은 게 보이고, 끄고 싶으면
Ctrl-C 를 누르면 된다. 웹 서버의 수명과 얽히지 않는다.
"""

import time

from core.jobs import ExternalDataError, SyncResult
from core.management.base import JobCommand
from trading.jobs import match_interval_seconds, match_pending_orders


class Command(JobCommand):
    help = "미체결 주문을 시장 체결에 맞춰 채우고 STOP 을 발동한다 (F-20 잡 3)"
    job_name = "match_pending_orders"

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
            help=(
                "--loop 의 주기(초). 생략하면 AppSetting "
                "'trading.match_interval_seconds' 를 따른다"
            ),
        )

    def handle(self, *args, **options):
        if options["loop"]:
            return self._run_forever(**options)
        return super().handle(*args, **options)

    def run_job(self, **options) -> SyncResult:
        return match_pending_orders(
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  · {message}"),
        )

    # ── 상시 기동 ────────────────────────────────────────────────
    def _run_forever(self, **options):
        """주기마다 잡을 돌린다.

        ★ **한 회차가 실패해도 멈추지 않는다.** 시세 서버가 잠깐 죽었다고 체결
          프로세스가 종료되면, 복구된 뒤에도 아무도 다시 띄우지 않는다.
          실패는 화면과 `DataSyncLog` 에 남기고 다음 회차로 간다.

        ★ **조용한 회차는 한 줄도 찍지 않는다.** 5초마다 "0건" 을 찍으면 터미널이
          흘러가 정작 체결이 일어난 줄을 놓친다. `DataSyncLog` 의 무변화 합치기와
          같은 원칙이다 (`core/jobs.py` 의 `coalesce_idle`).
        """
        interval = options["interval"] or match_interval_seconds()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{self.job_name} — {interval}초마다 반복합니다. Ctrl-C 로 종료합니다."
        ))

        rounds = filled = partial = failures = 0
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
                        filled += result.created
                        partial += result.updated
                        self.stdout.write(self.style.SUCCESS(
                            f"  체결 완료 {result.created}건 · 부분 체결 {result.updated}건"
                        ))
                time.sleep(interval)
        except KeyboardInterrupt:
            # ★ 요약을 남기고 조용히 끝낸다. 스택트레이스를 뱉으면 정상 종료가
            #   사고처럼 보인다.
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"종료합니다 — {rounds:,}회차 · 전량 체결 {filled}건 · "
                f"부분 체결 {partial}건 · 실패 {failures}건"
            ))
