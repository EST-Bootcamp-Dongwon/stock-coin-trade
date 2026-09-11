"""대회 운영 루프 — 잡 2 → 잡 1 → 잡 3 을 한 프로세스에서 순서대로 (F-20 2.1).

    python manage.py run_trading_loop --loop        # 대회 기간 상시 기동 (Ctrl-C 종료)
    python manage.py run_trading_loop               # 1회차만
    python manage.py run_trading_loop --dry-run     # 무엇이 갱신·체결될지만

★★ **순서가 이 커맨드의 존재 이유다** ─────────────────────────────────────────

F-20 2.1 이 요구한 흐름이다::

    5초마다:
      ① 호가·현재가 갱신 (잡 2 · 잡 1)
      ② PARTIAL/ACCEPTED 주문 스캔
      ③ 시장 체결 조건 충족분 체결      (잡 3)

문서는 "같은 트랜잭션" 이라고 적었지만 **그렇게 하지 않는다.** 시세 갱신은 외부
호출이고, 외부 호출을 트랜잭션 안에 두지 않는 것이 이 프로젝트의 규약이다
(market/services.py 머리말). 대신 **같은 프로세스에서 순서대로** 돌려 문서가 막으려던
문제 — *시세는 갱신됐는데 주문은 옛 가격으로 체결되는 창* — 를 없앤다.
체결이 읽는 값은 방금 이 프로세스가 써 넣은 값이다.

터미널 두 개로 나눠 띄우면 이 순서가 보장되지 않는다. **그래서 하나로 묶는다.**

★★ **잡 하나가 실패해도 나머지는 돈다** ────────────────────────────────────

KIS 가 죽어도 체결은 계속돼야 한다. 낡은 호가라도 5분 안이면 쓸 수 있고
(F-16 3.4), 연습 모드는 애초에 KIS 와 무관하다. 한 잡의 실패가 회차를 끝내지 않는다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 였다면 `asyncio` 태스크 3개를 각자의 주기로 돌렸을 것이다. 여기서는
**순차가 오히려 맞다** — 세 잡이 같은 데이터를 이어서 다루고, 병렬로 돌리면
시세 갱신 도중에 체결이 끼어드는 창이 다시 생긴다.

비용 이야기는 `match_pending_orders.py` 의 머리말과 같다 — Vercel Hobby 도
pg_cron 도 파이썬을 초 단위로 상시 돌릴 수 없어서, **대회 기간에는 로컬에서
이 커맨드를 띄워 두는 것**이 가장 단순하고 비용이 0 이다 (변경노트 E-28 · E-36).
"""

import time

from django.core.management.base import BaseCommand

from core.constants import TriggeredBy
from core.jobs import ExternalDataError, SyncResult
from market.jobs import poll_orderbook, poll_quotes
from market.sessions import SessionState, session_state
from trading.jobs import match_interval_seconds, match_pending_orders

# 한 회차에 돌리는 잡. **순서가 의미를 갖는다** (모듈 docstring).
#
# ★ 호가를 현재가보다 **먼저** 받는다. 체결에서 더 중요한 값이고, 유량이 모자랄 때
#   앞의 것이 살아남기 때문이다. 현재가는 네이버로 폴백되지만 호가는 KIS 뿐이다.
STEPS = [
    ("poll_orderbook", poll_orderbook, "호가"),
    ("poll_quotes", poll_quotes, "시세"),
    ("match_pending_orders", match_pending_orders, "체결"),
]


class Command(BaseCommand):
    help = "호가·시세 갱신과 미체결 체결을 순서대로 돌린다 (F-20 잡 1·2·3 통합)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--loop",
            action="store_true",
            help="종료하지 않고 주기마다 반복한다. 대회 기간 상시 기동용 (Ctrl-C 로 종료)",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=None,
            help="--loop 의 주기(초). 생략하면 AppSetting 'trading.match_interval_seconds'",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="외부 조회는 하되 DB 에는 쓰지 않는다",
        )
        parser.add_argument(
            "--market-hours-only",
            action="store_true",
            help=(
                "장중(영업일 09:00~15:30 KST)에만 잡을 돌린다. "
                "장외에는 회차를 건너뛴다 — 밤새 켜 두어도 외부 API 를 두드리지 않는다"
            ),
        )

    def handle(self, *args, **options):
        if not options["loop"]:
            self._run_once(options, verbose=True)
            return

        interval = options["interval"] or match_interval_seconds()
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"대회 운영 루프 — {interval}초마다 "
            f"{' → '.join(label for _, _, label in STEPS)} 순서로 돕니다. Ctrl-C 로 종료합니다."
        ))
        if options["market_hours_only"]:
            self.stdout.write("  장중에만 돕니다 (--market-hours-only).")

        rounds = skipped = 0
        try:
            while True:
                rounds += 1
                if options["market_hours_only"] and session_state() != SessionState.OPEN:
                    skipped += 1
                    # ★ 조용히 넘어간다. 장외 6시간을 "건너뜀" 로그로 채우면
                    #   정작 봐야 할 줄이 묻힌다 (`coalesce_idle` 과 같은 원칙).
                    time.sleep(interval)
                    continue
                self._run_once(options, verbose=False)
                time.sleep(interval)
        except KeyboardInterrupt:
            self.stdout.write("")
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"종료합니다 — {rounds:,}회차"
                + (f" (장외로 건너뛴 회차 {skipped:,})" if skipped else "")
            ))

    # ── 한 회차 ──────────────────────────────────────────────────
    def _run_once(self, options: dict, *, verbose: bool) -> None:
        """세 잡을 순서대로 돌린다. **한 잡의 실패가 나머지를 막지 않는다.**

        Args:
            verbose: 조용한 결과도 찍을 것인가. 1회 실행은 참(무슨 일이 있었는지
                알아야 한다), `--loop` 는 거짓(5초마다 0건을 찍으면 터미널이 흘러간다).
        """
        for job_name, service, label in STEPS:
            try:
                result: SyncResult = service(
                    triggered_by=TriggeredBy.CLI,
                    dry_run=options["dry_run"],
                    on_progress=(lambda message: self.stdout.write(f"    · {message}")) if verbose else _drop,
                )
            except ExternalDataError as exc:
                # 외부 장애 — 남의 서버 문제다. 경고만 하고 다음 잡으로.
                self.stdout.write(self.style.WARNING(f"  ⚠ {label}: {exc}"))
                continue
            except Exception as exc:        # noqa: BLE001 — 루프를 지킨다
                self.stdout.write(self.style.ERROR(f"  ✖ {label}: {type(exc).__name__}: {exc}"))
                continue

            if verbose:
                for note in result.notes:
                    self.stdout.write(f"    · {note}")
            if result.rows or verbose:
                style = self.style.SUCCESS if result.rows else self.style.HTTP_INFO
                self.stdout.write(style(
                    f"  {label}: 신규 {result.created} · 갱신 {result.updated}"
                    + (f" · 건너뜀 {result.skipped}" if result.skipped else "")
                ))


def _drop(_message: str) -> None:
    """`--loop` 에서 진행 로그를 버린다. 잡 자체는 `DataSyncLog` 에 남는다."""
