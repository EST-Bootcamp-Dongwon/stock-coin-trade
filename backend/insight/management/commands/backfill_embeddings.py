"""지식 문서 임베딩 채우기 — 운영 초기 데이터 ④ (05 문서 5.2·5.3 · E-06 2.5).

사용법::

    python manage.py backfill_embeddings              # 벡터가 빈 문서만
    python manage.py backfill_embeddings --limit 5    # 5건만 (모델 동작 확인용)
    python manage.py backfill_embeddings --rebuild    # 모델을 바꿨을 때 전부 다시
    python manage.py backfill_embeddings --dry-run    # 모델을 받지 않고 대상 건수만

★ **왜 마이그레이션이 아니라 커맨드인가** — fastembed 모델 로드가 수십 초~수 분이라
   마이그레이션에 넣으면 배포 파이프라인이 그만큼 멈추고, 롤백도 어려워진다.
   시드 마이그레이션(`insight/0002`)은 문서 21건을 `embedding=NULL` 로 넣기만 하고,
   벡터는 이 커맨드가 나중에 붙인다.

★ **실행처는 로컬 CPU 다** (규약 8.2). 21건 · 짧은 텍스트라 GPU 이득이 없고
   지배적 비용은 모델 로드다. 근거는 `insight.services` 모듈 주석 참조.
"""

from django.core.management.base import CommandError

from core.management.base import JobCommand
from insight.services import DEFAULT_BATCH_SIZE, backfill_embeddings


class Command(JobCommand):
    help = "KnowledgeDocument 의 빈 embedding 을 fastembed 로 채운다"
    job_name = "backfill_embeddings"

    def add_job_arguments(self, parser):
        parser.add_argument(
            "--limit", type=int, default=None, metavar="N",
            help="이번 실행에서 처리할 최대 문서 수 (기본: 전부)",
        )
        parser.add_argument(
            "--batch-size", type=int, default=DEFAULT_BATCH_SIZE, metavar="N",
            help=f"한 번에 임베딩할 문서 수 (기본 {DEFAULT_BATCH_SIZE})",
        )
        parser.add_argument(
            "--rebuild", action="store_true",
            help="이미 벡터가 있는 문서까지 다시 만든다 — **모델을 바꿨을 때만** 쓴다",
        )

    def run_job(self, **options):
        # ★ 인자를 **모델을 불러오기 전에** 검증한다. `--batch-size 0` 은
        #   `range(0, n, 0)` 에서 ValueError 로 죽고 음수는 조용히 0건을 처리한 뒤
        #   SUCCESS 로 기록된다. 둘 다 220MB 모델을 다 받고 나서 벌어지는 일이라
        #   여기서 먼저 막는 편이 훨씬 싸다.
        if options["batch_size"] < 1:
            raise CommandError(
                f"--batch-size 는 1 이상이어야 합니다: {options['batch_size']}"
            )
        if options["limit"] is not None and options["limit"] < 1:
            raise CommandError(
                f"--limit 은 1 이상이어야 합니다 (전부 처리하려면 생략하십시오): "
                f"{options['limit']}"
            )

        return backfill_embeddings(
            limit=options["limit"],
            batch_size=options["batch_size"],
            rebuild=options["rebuild"],
            triggered_by=options["triggered_by"],
            dry_run=options["dry_run"],
            on_progress=lambda message: self.stdout.write(f"  {message}"),
        )
