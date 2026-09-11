"""주간 정산 커맨드 (F-20 잡 9 · F-04 5장).

사용법::

    python manage.py settle_weekly                      # 지난 주 확정
    python manage.py settle_weekly --week 2026-08-10    # 특정 주(월요일 날짜)를 확정
    python manage.py settle_weekly --dry-run            # 회전율만 계산하고 실격은 하지 않는다

운영에서는 pg_cron 이 **매주 월요일 06:00 KST** 에 부른다.

★★ **이 잡만이 자동 실격을 낸다** ─────────────────────────────────────────

주간 회전율 확정 위반이 4회에 도달하면 `Participation.status = DISQUALIFIED` ·
`is_ranked = False` 가 된다 (F-04 5.4). 편입 한도 초과는 *"적극적으로 해소하려
노력했는가"* 라는 정성 판단이 필요해 자동화하지 않는다 (F-04 3.4).

**계좌·주문 이력은 지우지 않는다.** 랭킹에서만 빠지고, 운영자는 Admin 에서
되돌릴 수 있다. 되돌리면 다음 실행이 `violation_seq` 를 다시 매긴다.

★ `--dry-run` 은 **아무것도 저장하지 않는다.** 회전율을 계산해 보여주고,
  이대로 확정하면 **누가 잘리는지**까지 별칭으로 알려준다. 월요일 아침에 결과를
  먼저 확인하고 싶을 때 쓴다.
"""

from datetime import datetime

from django.core.management.base import CommandError

from contests.jobs import settle_weekly
from core.management.base import JobCommand


class Command(JobCommand):
    help = "지난 주 회전율을 확정하고 4회 위반자를 자동 정지한다 (F-20 잡 9)"
    job_name = "settle_weekly"

    def add_job_arguments(self, parser):
        parser.add_argument(
            "--week",
            type=str,
            default=None,
            metavar="YYYY-MM-DD",
            help="확정할 주의 **월요일** 날짜 (기본: 지난 주). 주는 월~일 KST 다",
        )

    def run_job(self, **options):
        return settle_weekly(
            week_start=_parse_monday(options["week"]),
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  {message}"),
        )


def _parse_monday(raw: str | None):
    """`YYYY-MM-DD` → `date`. **월요일이 아니면 거부한다.**

    ★ 조용히 그 주의 월요일로 보정하지 않는다. `WeeklyTurnover.week_start` 는
      월요일이라는 약속 위에 서 있고(F-04 5.1), 수요일 날짜를 받아 보정해 주면
      운영자는 자기가 무엇을 확정했는지 헷갈린 채로 끝난다.
    """
    if not raw:
        return None
    try:
        day = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(
            f"--week 는 YYYY-MM-DD 형식이어야 합니다 (받은 값: {raw!r}). 예: --week 2026-08-10"
        ) from exc
    if day.weekday() != 0:
        monday = day.fromordinal(day.toordinal() - day.weekday())
        raise CommandError(
            f"--week 에는 **월요일** 날짜를 주십시오 ({raw} 은(는) "
            f"{'월화수목금토일'[day.weekday()]}요일입니다). 그 주의 월요일은 {monday} 입니다."
        )
    return day
