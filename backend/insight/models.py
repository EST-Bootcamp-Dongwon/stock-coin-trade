"""insight — AI · RAG 지식 · 웹 수집기 (E-06).

**모델 4종.** v1.0 결함 **D-6(인증 없이 열린 엔드포인트)** · **D-8(Qdrant `:memory:`)** 를
데이터 모델 수준에서 막는다.

이 앱이 지키는 원칙:

| 원칙 | 반영 |
|---|---|
| 비용이 드는 엔드포인트는 반드시 로그인 + 쿼터 | `AiUsageLog` · `WebFetchLog` |
| 사용자 입력 데이터는 승인 전까지 격리        | `KnowledgeDocument.is_approved` |
| 실패는 사용자에게 청구하지 않는다            | `is_error` 는 쿼터 미차감 |
| 막은 시도도 기록한다                        | `is_blocked` |
"""

from django.db import models
from pgvector.django import HnswIndex, VectorField

# 임베딩 모델 paraphrase-multilingual-MiniLM-L12-v2 의 출력 차원.
# 모델을 바꾸면 이 값도 바뀌고 기존 벡터를 전부 다시 만들어야 한다.
EMBEDDING_DIMENSIONS = 384


class KnowledgeSource(models.TextChoices):
    SEED = "SEED", "시드"
    USER = "USER", "사용자 추가"
    BATCH = "BATCH", "배치 수집"


class AiScope(models.TextChoices):
    CRYPTO = "CRYPTO", "코인"
    STOCK = "STOCK", "주식"
    MARKET = "MARKET", "시장"
    MY_PORTFOLIO = "MY_PORTFOLIO", "내 포트폴리오"
    CONTEST = "CONTEST", "대회"


class BlockReason(models.TextChoices):
    PRIVATE_IP = "PRIVATE_IP", "사설 IP"
    SCHEME = "SCHEME", "허용되지 않은 스킴"
    PORT = "PORT", "허용되지 않은 포트"
    REDIRECT = "REDIRECT", "리디렉션 최종 URL 거부"
    CONTENT_TYPE = "CONTENT_TYPE", "HTML 이 아님"
    SIZE = "SIZE", "크기 초과"
    CREDENTIALS = "CREDENTIALS", "URL 에 자격증명 포함"


class ApprovedKnowledgeManager(models.Manager):
    """승인된 문서만 반환한다. **검색 경로는 반드시 이걸 쓴다.**"""

    def get_queryset(self):
        return super().get_queryset().filter(is_approved=True)


class KnowledgeDocument(models.Model):
    """RAG 지식 문서 — Qdrant 에서 pgvector 로 옮긴다.

    ★ v1.0 은 Qdrant 기본값이 `:memory:` 라 재기동하면 사용자가 추가한 지식이
    통째로 사라졌다(D-8). 게다가 `POST /add` 가 인증 없이 열려 있어
    누구나 지식 베이스를 오염시킬 수 있었다(D-6).

    Supabase Postgres 를 쓰기로 확정한 이상 **벡터 DB 를 따로 운영할 이유가 없다.**

    Django 관점 — pgvector 는 Django ORM 에 기본 내장이 아니다.
    `pip install pgvector` 후 `VectorField` 를 쓰고, 확장 설치는 마이그레이션에서
    `VectorExtension()` 오퍼레이션으로 한다 (05 문서 2장).

    ★ **`embedding` 이 `null=True` 인 이유** — fastembed 모델 로드가 수십 초~수 분이라
    마이그레이션에서 벡터를 만들면 배포 파이프라인이 멈춘다. **문서는 먼저 저장되고
    벡터는 배치가 나중에 붙인다.** 벡터가 없는 문서는 검색에 나오지 않으므로
    승인 대기와 자연스럽게 맞물린다 (E-06 2.5).
    """

    title = models.CharField(max_length=200, verbose_name="제목")
    category = models.CharField(max_length=30, db_index=True, verbose_name="카테고리")
    content = models.TextField(verbose_name="본문", help_text="사용자 입력은 2000자 제한")
    embedding = VectorField(
        dimensions=EMBEDDING_DIMENSIONS, null=True, blank=True, verbose_name="임베딩",
        help_text="배치(backfill_embeddings)가 채운다. 비어 있으면 검색에 나오지 않는다",
    )

    source = models.CharField(
        max_length=10, choices=KnowledgeSource.choices, default=KnowledgeSource.USER
    )
    created_by = models.ForeignKey(
        "accounts.Member", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="knowledge_docs", verbose_name="추가한 회원",
    )
    is_approved = models.BooleanField(
        default=False, db_index=True, verbose_name="승인",
        help_text="검토 대기 상태(False)인 문서는 검색 결과에 나오지 않는다",
    )
    approved_by = models.ForeignKey(
        "accounts.Member", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="approved_docs", verbose_name="승인한 운영자",
    )
    approved_at = models.DateTimeField(null=True, blank=True, verbose_name="승인 시각")
    created_at = models.DateTimeField(auto_now_add=True)

    # Django 관점 — 첫 번째로 정의된 매니저가 기본 매니저가 된다.
    # objects 를 승인분만 반환하도록 덮어쓰면 Admin 목록에서 미승인 문서가 사라지는
    # 사고가 난다. objects 는 전체로 두고 **검색 경로에서 approved 를 쓰는 규약**이 안전하다.
    objects = models.Manager()          # 전체 (Admin·운영용)
    approved = ApprovedKnowledgeManager()   # 검색은 이것만 쓴다

    class Meta:
        db_table = "knowledge_document"
        verbose_name = "지식 문서"
        verbose_name_plural = "지식 문서"
        indexes = [
            # 코사인 유사도 HNSW 인덱스. 문서가 적어도 붙여두면 늘어나도 안전하다
            HnswIndex(
                name="knowledge_hnsw_cosine",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self):
        return self.title


class AiUsageLog(models.Model):
    """AI 호출 쿼터·비용 집계 — 결함 D-6.

    ★ v1.0 은 `/api/ai/analyze` 가 **인증 없이 열려 있었다.**
    Claude API 비용이 발생하는 엔드포인트가 누구에게나 열려 있던 것이다.

    **실패한 호출(`is_error=True`)은 쿼터를 차감하지 않는다** —
    우리 쪽 장애로 사용자가 손해를 보면 안 된다.

    **모델명을 컬럼에 저장하는 이유** — 모델마다 비용이 다르고, 심층 분석은 쿼터를
    더 크게 차감한다. 사후 비용 집계에도 쓴다.
    모델명은 `core.AppSetting` 으로 빼고 코드에 하드코딩하지 않는다.
    """

    member = models.ForeignKey(
        "accounts.Member", on_delete=models.PROTECT, related_name="ai_usage_logs"
    )
    scope = models.CharField(max_length=20, choices=AiScope.choices, verbose_name="분석 범위")
    model = models.CharField(max_length=40, verbose_name="모델명")
    tokens_in = models.IntegerField(default=0, verbose_name="입력 토큰")
    tokens_out = models.IntegerField(default=0, verbose_name="출력 토큰")
    latency_ms = models.IntegerField(default=0, verbose_name="응답 시간(ms)")
    is_error = models.BooleanField(
        default=False, verbose_name="실패", help_text="실패는 쿼터를 차감하지 않는다"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "ai_usage_log"
        verbose_name = "AI 사용 로그"
        verbose_name_plural = "AI 사용 로그"
        indexes = [
            # 쿼터 조회가 유일한 패턴이다
            models.Index(fields=["member", "created_at"], name="ai_usage_idx_member_date"),
        ]

    def __str__(self):
        return f"{self.member_id} {self.scope} {self.model}"


class SavedSheet(models.Model):
    """수집한 표 저장. v1.0 은 새로고침하면 결과가 사라져 실용성이 떨어졌다."""

    member = models.ForeignKey(
        "accounts.Member", on_delete=models.CASCADE, related_name="saved_sheets"
    )
    title = models.CharField(max_length=100, verbose_name="제목")
    source_url = models.TextField(blank=True, verbose_name="원본 URL")
    data = models.JSONField(
        default=dict, verbose_name="표 데이터", help_text="헤더 + 행 배열 (편집된 상태 그대로)"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "saved_sheet"
        verbose_name = "저장한 표"
        verbose_name_plural = "저장한 표"
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title


class WebFetchLog(models.Model):
    """외부 URL 수집 로그 — 오남용 추적.

    외부 URL 을 **우리 서버가 대신 호출**해 주는 기능이라 오남용 위험이 있다.
    v1.0 은 `/api/ai-sheet/crawl` 이 인증 없이 열려 있었다.

    ★ **`is_blocked` 를 남기는 이유** — SSRF 방어에 **걸린 시도**야말로 기록해야 한다.
    사설망 주소를 반복해서 넣는 사용자가 있으면 그게 신호다.

    방어는 v1.0 로직을 그대로 포팅한다 — scheme(http/https만) · port(80/443만) ·
    URL 자격증명 거부 · 모든 A/AAAA 레코드가 global 인지 DNS 검증 ·
    **리디렉션 최종 URL 재검증**(흔히 빠뜨리는 지점) · Content-Type · 2MB 크기 제한.

    사용량 제한은 회원당 1일 50회. 이 테이블로 센다.
    이름은 "AI Sheet" → **"웹 데이터 수집기"** 로 바꾼다 (결함 D-11).
    """

    member = models.ForeignKey(
        "accounts.Member", on_delete=models.PROTECT, related_name="web_fetch_logs"
    )
    url = models.TextField(verbose_name="요청 URL")
    status_code = models.IntegerField(null=True, blank=True, verbose_name="응답 코드")
    size_bytes = models.IntegerField(default=0, verbose_name="응답 크기")
    elapsed_ms = models.IntegerField(default=0, verbose_name="소요 시간(ms)")
    is_blocked = models.BooleanField(default=False, verbose_name="차단됨")
    block_reason = models.CharField(
        max_length=50, choices=BlockReason.choices, blank=True, verbose_name="차단 사유"
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "web_fetch_log"
        verbose_name = "웹 수집 로그"
        verbose_name_plural = "웹 수집 로그"
        indexes = [
            models.Index(fields=["member", "created_at"], name="webfetch_idx_member_date"),
        ]

    def __str__(self):
        return f"{self.url[:50]} [{self.status_code}]"
