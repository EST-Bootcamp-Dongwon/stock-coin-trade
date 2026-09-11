"""insight 서비스 계층 — 지식 문서 임베딩 (E-06 2.5 · 05 문서 5.2).

    문서는 먼저 저장되고, 벡터는 배치가 나중에 붙인다.

`KnowledgeDocument.embedding` 이 `null=True` 인 이유가 이것이다. fastembed 는
모델 파일(약 220MB)을 로드하므로 **마이그레이션에서 벡터를 만들면 배포 파이프라인이
멈춘다.** 벡터가 없는 문서는 검색에 나오지 않으므로 승인 대기와 자연스럽게 맞물린다.

★★ **실행처 — 로컬 CPU 다** (규약 8.2) ────────────────────────────────────

판정표는 "대규모 임베딩 생성·추론 배치 → Colab Pro" 라고 적지만, **이 잡은 대규모가
아니다.** 시드 지식 21건 · 문서당 수백 자다. 실제 비용은 임베딩 연산이 아니라
**모델 로드**이고, 그건 GPU 로도 줄지 않는다. 게다가 원자료(Postgres)가 로컬에 있어
Colab 으로 보내려면 반출 절차가 더 붙는다.

    21건 임베딩 = 로컬 몇 초. Colab 에 올리면 오히려 느리다.

문서가 수만 건으로 늘어나면 그때 판정표를 다시 본다. 그 갈림길은 "GPU 가 실제로
쓰이는가" 하나다.
"""

import os
from typing import Callable

from django.conf import settings
from django.db import transaction

from core.constants import TriggeredBy
from core.jobs import ExternalDataError, SyncResult, job_run
from insight.models import EMBEDDING_DIMENSIONS, KnowledgeDocument

ProgressFn = Callable[[str], None]


def _noop(_message: str) -> None:
    """`on_progress` 를 안 넘겼을 때 쓰는 빈 콜백."""


# ★★ 이 이름을 바꾸면 **기존 벡터를 전부 다시 만들어야 한다.** 서로 다른 모델이 만든
#   벡터는 같은 공간에 있지 않아 코사인 유사도가 의미를 잃는다. 섞이면 검색 결과가
#   조용히 엉망이 되고, **에러는 나지 않는다** — 그래서 더 위험하다.
#   바꿀 때는 반드시 `backfill_embeddings --rebuild` 를 함께 돌린다.
#
#   ★ **모델 이름이 같아도 fastembed 버전이 다르면 벡터가 달라진다.**
#     2026-08-13 실측 — fastembed 0.8.0 은 이 모델에 mean pooling 을 쓰는데
#     0.5.1 이하는 CLS 임베딩을 썼다고 실행 중 경고를 띄운다. 같은 문장을 넣어도
#     결과 벡터가 다르다는 뜻이다. `requirements.txt` 의 fastembed 핀을 올릴 때도
#     `--rebuild` 를 함께 돌려야 한다. 핀을 고정해 둔 이유가 이것이다.
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# 한 번에 임베딩할 문서 수. 모델 로드가 지배적이라 크게 잡을 이유가 없다.
DEFAULT_BATCH_SIZE = 32


def _cache_dir() -> str:
    """모델 파일을 둘 경로.

    ★ 기본 캐시가 임시 디렉터리라 재부팅하면 220MB 를 다시 받는다.
    프로젝트 안에 고정해 두고 `.gitignore` 로 막는다 (규약 8.1 — 산출물은 로컬이 정본,
    단 **모델 가중치는 소스가 아니므로 커밋하지 않는다**).
    """
    return os.getenv("FASTEMBED_CACHE_DIR") or str(settings.BASE_DIR / ".fastembed-cache")


def load_embedding_model(*, on_progress: ProgressFn = _noop):
    """fastembed 모델을 **지금** 불러온다. 모듈 최상단에서 import 하지 않는다.

    ★ `import fastembed` 는 onnxruntime 을 함께 끌어온다. 최상단에 두면 임베딩과
    상관없는 모든 `manage.py` 명령이 그 비용을 낸다. `market.services._load_pykrx()`
    와 같은 이유의 지연 import 다.

    Raises:
        ExternalDataError: 패키지가 없거나 모델을 받지 못한 경우.
            **"막다른 길로 만들지 않는다"**(규약 8.5) — 무엇을 하면 되는지까지 적는다.
    """
    try:
        from fastembed import TextEmbedding      # noqa: PLC0415 — 지연 import 가 의도다
    except ImportError as exc:
        raise ExternalDataError(
            "fastembed 가 설치되어 있지 않습니다.",
            hint=(
                "backend 가상환경에서 `pip install -r requirements.txt` 를 실행하십시오.\n"
                "    onnxruntime 을 함께 받으므로 200MB 가량 필요합니다."
            ),
        ) from exc

    cache_dir = _cache_dir()
    on_progress(f"모델 {EMBEDDING_MODEL_NAME}")
    on_progress(f"캐시 {cache_dir} (처음 실행이면 약 220MB 를 내려받습니다)")

    try:
        model = TextEmbedding(model_name=EMBEDDING_MODEL_NAME, cache_dir=cache_dir)
    except Exception as exc:      # noqa: BLE001 — 모델 다운로드 실패는 원인이 다양하다
        raise ExternalDataError(
            f"임베딩 모델을 준비하지 못했습니다: {type(exc).__name__}: {exc}",
            hint=(
                "네트워크(허깅페이스 접근)를 확인하십시오.\n"
                f"    캐시 경로에 쓰기 권한이 있는지도 확인하십시오: {cache_dir}"
            ),
        ) from exc

    return model


def _document_text(document: KnowledgeDocument) -> str:
    """임베딩할 텍스트.

    ★ **제목을 본문 앞에 붙인다.** 시드 지식은 "이동평균선이란" 처럼 제목이 곧
    검색어인 경우가 많다. 본문만 넣으면 정작 사용자가 치는 말이 벡터에 약하게 들어간다.
    """
    return f"{document.title}\n\n{document.content}".strip()


def backfill_embeddings(
    *,
    limit: int | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    rebuild: bool = False,
    triggered_by: str = TriggeredBy.CRON,
    dry_run: bool = False,
    on_progress: ProgressFn = _noop,
) -> SyncResult:
    """`embedding` 이 비어 있는 지식 문서에 벡터를 채운다 (E-06 2.5).

    Args:
        limit: 이번 실행에서 처리할 최대 문서 수. `None` 이면 전부.
        batch_size: 한 번에 임베딩할 문서 수.
        rebuild: 이미 벡터가 있는 문서까지 **다시** 만든다. 모델을 바꿨을 때만 쓴다.

    ★ **승인 여부로 거르지 않는다.** 검색 경로는 `KnowledgeDocument.approved`
    매니저가 이미 미승인 문서를 막는다(E-06 2.3). 벡터를 미리 만들어 두면 운영자가
    승인하는 순간 바로 검색된다 — 승인하고 나서 배치를 또 기다릴 이유가 없다.
    로컬 CPU 연산이라 비용도 사실상 없다.
    """
    result = SyncResult()

    # ★★ 모델 로드까지 `job_run` 안에 둔다. fastembed 다운로드 실패·디스크 부족도
    #    `DataSyncLog` 에 FAILED 로 남아야 Admin 에서 보인다 (E-07 2.2 · F-20 5장).
    #    "할 일이 없었다"(0건)도 기록한다 — **잡이 돌긴 했다**는 사실 자체가 정보다.
    with job_run("backfill_embeddings", triggered_by, dry_run=dry_run) as record:
        _run_backfill(
            result=result,
            limit=limit,
            batch_size=batch_size,
            rebuild=rebuild,
            dry_run=dry_run,
            on_progress=on_progress,
        )
        record["rows"] = result.rows

    return result


def _run_backfill(
    *,
    result: SyncResult,
    limit: int | None,
    batch_size: int,
    rebuild: bool,
    dry_run: bool,
    on_progress: ProgressFn,
) -> None:
    """`backfill_embeddings` 의 본체. 결과는 넘겨받은 `result` 에 채운다."""
    queryset = KnowledgeDocument.objects.all().order_by("pk")
    if not rebuild:
        queryset = queryset.filter(embedding__isnull=True)

    total = queryset.count()

    if total == 0:
        result.note(
            "채울 문서가 없습니다 — 모든 지식 문서에 이미 벡터가 있습니다. "
            "모델을 바꿨다면 --rebuild 를 쓰십시오."
        )
        return

    if limit is not None and limit < total:
        queryset = queryset[:limit]
        result.note(f"--limit {limit} — 대상 {total:,}건 중 {limit:,}건만 처리합니다")
        total = limit

    documents = list(queryset)
    on_progress(f"대상 문서 {total:,}건" + (" (--rebuild: 기존 벡터도 다시 만듭니다)" if rebuild else ""))

    if dry_run:
        # ★ dry-run 은 **모델을 불러오지 않는다.** 220MB 다운로드가 목적이 아니다.
        result.updated = total
        result.note("--dry-run: 모델을 불러오지 않고 대상 건수만 셌습니다")
        return

    model = load_embedding_model(on_progress=on_progress)

    done = 0
    for start in range(0, len(documents), batch_size):
        chunk = documents[start:start + batch_size]
        vectors = list(model.embed([_document_text(document) for document in chunk]))

        # ★ 차원을 **매번** 확인한다. 모델을 바꾸면 컬럼 차원과 어긋나는데,
        #   pgvector 는 그때 INSERT 단계에서야 막는다. 여기서 먼저 잡으면
        #   "왜 안 들어가는지" 를 찾는 시간을 아낀다 (insight/models.py 19행).
        for vector in vectors:
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise ExternalDataError(
                    f"임베딩 차원이 맞지 않습니다: 모델 {len(vector)} "
                    f"≠ 컬럼 {EMBEDDING_DIMENSIONS}",
                    hint=(
                        f"모델({EMBEDDING_MODEL_NAME})을 바꿨다면 "
                        "insight.models.EMBEDDING_DIMENSIONS 와 마이그레이션도 "
                        "함께 고쳐야 합니다."
                    ),
                )

        for document, vector in zip(chunk, vectors):
            document.embedding = vector.tolist()

        with transaction.atomic():
            KnowledgeDocument.objects.bulk_update(chunk, ["embedding"])

        done += len(chunk)
        on_progress(f"{done:,}/{total:,}건 완료")

    result.updated = done
