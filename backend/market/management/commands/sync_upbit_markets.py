"""업비트 마켓 목록 적재 — 운영 초기 데이터 ③ (05 문서 5.3 · E-04 9.1 · F-16 6장).

사용법::

    python manage.py sync_upbit_markets
    python manage.py sync_upbit_markets --dry-run

운영에서는 pg_cron 이 **매일 18:00 KST** 에 부른다 (F-20 잡 11번).

★ 인증이 필요 없는 공개 API 라 **4종 커맨드 중 유일하게 자격증명 없이 돈다.**
  네트워크만 되면 어디서든 검증할 수 있어, 파이프라인 전체를 점검할 때 첫 번째로
  돌려보기 좋다.
"""

from core.management.base import JobCommand
from market.services import sync_upbit_markets


class Command(JobCommand):
    help = "업비트 마켓 목록을 UpbitMarket 에 upsert 한다"
    job_name = "sync_upbit_markets"

    def run_job(self, **options):
        return sync_upbit_markets(
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  {message}"),
        )
