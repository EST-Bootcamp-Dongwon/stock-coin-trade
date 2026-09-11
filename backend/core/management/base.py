"""외부 데이터 적재 커맨드의 공통 껍데기 (05 문서 5.3 · F-16 5.3).

    실제 로직은 각 앱의 `services.py` 에 있다. 커맨드는 인자를 풀어 서비스 함수를
    부르고, 결과를 사람이 읽게 찍는 일만 한다.

**왜 이렇게 나누는가** — F-16 5.3 이 요구한 구조다::

    pg_cron ──HTTP──▶ /internal/jobs/sync-stock-master ─┐
                                                        ├─▶ market.services.sync_stock_master()
    운영자 ──CLI───▶ python manage.py sync_stock_master ─┘

같은 함수를 양쪽에서 부른다. 로직이 커맨드 안에 있으면 HTTP 엔드포인트가 그것을
다시 적게 되고, 두 벌이 조금씩 어긋나기 시작한다.

Django 관점 — FastAPI 에서는 라우터 함수에 로직을 그대로 적고 CLI 가 필요하면
`typer` 로 따로 만드는 일이 흔했다. Django 의 `BaseCommand` 는 `handle()` 하나만
채우면 되는 얇은 껍데기라, **처음부터 서비스 계층을 분리해 두는 편이 자연스럽다.**
`accounts/services.py` 와 같은 원칙이다.
"""

from django.core.management.base import BaseCommand, CommandError

from core.constants import TriggeredBy
from core.jobs import ExternalDataError, SyncResult, has_consecutive_failures


class JobCommand(BaseCommand):
    """외부 데이터 적재 커맨드의 베이스.

    하위 클래스가 채울 것은 두 가지다::

        class Command(JobCommand):
            job_name = "sync_upbit_markets"

            def run_job(self, **options) -> SyncResult:
                return sync_upbit_markets(...)

    공통 인자 3개(`--dry-run` · `--soft-fail` · `--triggered-by`)와 결과 출력,
    그리고 외부 장애와 코드 버그를 가르는 처리는 여기서 한 번만 적는다.
    """

    job_name = ""

    # ── 인자 ────────────────────────────────────────────────────
    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "외부 데이터는 실제로 가져오되 DB 에는 쓰지 않는다. "
                "무엇이 몇 건 바뀔지만 보고 끝낸다"
            ),
        )
        parser.add_argument(
            "--soft-fail",
            action="store_true",
            help=(
                "외부 데이터 소스 장애를 경고로 낮추고 종료 코드 0 으로 끝낸다. "
                "배포 스크립트·스케줄러가 쓴다 (05 문서 5.3)"
            ),
        )
        parser.add_argument(
            "--triggered-by",
            choices=[TriggeredBy.CLI, TriggeredBy.CRON, TriggeredBy.ADMIN],
            default=TriggeredBy.CLI,
            help="DataSyncLog 에 남길 실행 주체 (기본 CLI)",
        )
        self.add_job_arguments(parser)

    def add_job_arguments(self, parser):
        """잡별 인자. 필요한 하위 클래스만 덮어쓴다."""

    # ── 실행 ────────────────────────────────────────────────────
    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        soft_fail = options["soft_fail"]

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"{self.job_name}{'  (dry-run — DB 에 쓰지 않습니다)' if dry_run else ''}"
        ))

        # 이미 3회 연속 실패한 잡이면 먼저 알린다. 막지는 않는다 —
        # 운영자가 손으로 재실행하는 것은 보통 **복구를 시도하는 중**이기 때문이다.
        if self.job_name and has_consecutive_failures(self.job_name):
            self.stdout.write(self.style.WARNING(
                f"  ⚠ 이 잡은 최근 3회 연속 실패했습니다. "
                f"스케줄러는 건너뛰는 상태입니다 (F-20 5장).\n"
                f"    원인은 Admin > 배치 실행 이력 에서 스택트레이스를 확인하십시오."
            ))

        try:
            result = self.run_job(**options)
        except ExternalDataError as exc:
            # ★ 외부 장애는 우리 잘못이 아니다. 다만 **조용히 넘어가지는 않는다** —
            #   `DataSyncLog` 에는 이미 FAILED 로 남아 Admin 경고 배너에 뜬다.
            message = str(exc)
            hint = f"\n    {exc.hint}" if exc.hint else ""
            if soft_fail:
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ 외부 데이터를 가져오지 못했습니다 — 건너뜁니다.\n"
                    f"    {message}{hint}"
                ))
                return
            raise CommandError(f"{message}{hint}") from exc

        self._report(result, dry_run=dry_run)

    def run_job(self, **options) -> SyncResult:
        """실제 작업. 하위 클래스가 반드시 채운다."""
        raise NotImplementedError

    # ── 출력 ────────────────────────────────────────────────────
    def _report(self, result: SyncResult, *, dry_run: bool):
        """결과를 사람이 읽게 찍는다.

        ★ `created` 와 `updated` 를 나눠 찍는 이유 — **두 번째 실행부터 `created` 가
        0이 아니면 upsert 키가 잘못 잡힌 것이다.** 합계만 찍으면 그 사고가 보이지 않는다.
        """
        for note in result.notes:
            self.stdout.write(f"  · {note}")

        verb = "바뀔 예정" if dry_run else "완료"
        self.stdout.write(self.style.SUCCESS(
            f"  {verb}: 신규 {result.created:,}건 · 갱신 {result.updated:,}건"
            + (f" · 건너뜀 {result.skipped:,}건" if result.skipped else "")
        ))
        if dry_run:
            self.stdout.write("  --dry-run 이라 DB 에는 아무것도 쓰지 않았습니다.")
