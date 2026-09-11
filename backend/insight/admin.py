"""insight Admin — 지식 문서 승인 · AI 사용량 · 수집 로그 (F-19).

**승인 대기 문서를 처리하는 화면이 이 앱의 핵심이다** — v1.0 은 `POST /add` 가
인증 없이 열려 있어 누구나 지식 베이스를 오염시킬 수 있었다(결함 D-6).
v2.0 은 사용자가 넣은 문서를 `is_approved=False` 로 격리하고, 여기서 검토한다.
"""

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from core.admin import ReadOnlyAdminMixin
from insight.models import AiUsageLog, KnowledgeDocument, SavedSheet, WebFetchLog


@admin.register(KnowledgeDocument)
class KnowledgeDocumentAdmin(admin.ModelAdmin):
    """RAG 지식 문서.

    ★ **미승인 문서가 목록 맨 위에 오게 정렬한다.** 검토 대기가 쌓여 있는데
    최신순으로만 보이면 놓친다.

    ★ **`objects` 를 쓴다** — 모델에는 매니저가 둘이고(`objects` 전체 · `approved`
    승인분만), Admin 은 반드시 전체를 봐야 한다. 승인 화면에서 미승인 문서가
    안 보이면 화면의 목적이 사라진다. 모델이 `objects` 를 기본으로 남겨 둔 이유다.

    `embedding` 은 화면에 띄우지 않는다 — 384차원 실수 배열이라 사람이 볼 것이 아니고,
    목록에 넣으면 행마다 그 벡터를 전부 끌어온다. **`has_embedding` 불리언으로 갈음한다.**
    """

    list_display = ("title", "category", "source", "is_approved", "has_embedding",
                    "created_by", "created_at")
    list_filter = ("is_approved", "source", "category")
    search_fields = ("title", "content", "category")
    date_hierarchy = "created_at"
    ordering = ("is_approved", "-created_at")   # 미승인(False) 이 먼저 온다
    readonly_fields = ("embedding_state", "source", "created_by",
                       "approved_by", "approved_at", "created_at")
    exclude = ("embedding",)
    list_select_related = ("created_by",)
    actions = ["approve_documents", "unapprove_documents"]

    fieldsets = (
        ("문서", {"fields": ("title", "category", "content")}),
        ("승인", {
            "fields": ("is_approved", "approved_by", "approved_at"),
            "description": "<b>승인하지 않은 문서는 검색 결과에 나오지 않는다.</b> "
                           "사용자가 추가한 문서를 검토하는 자리다 (결함 D-6).",
        }),
        ("벡터", {
            "fields": ("embedding_state",),
            "description": "임베딩은 <code>python manage.py backfill_embeddings</code> 가 채운다. "
                           "<b>벡터가 없는 문서는 승인해도 검색에 나오지 않는다.</b>",
        }),
        ("출처", {"fields": ("source", "created_by", "created_at")}),
    )

    @admin.display(description="벡터", boolean=True, ordering="embedding")
    def has_embedding(self, obj):
        return obj.embedding is not None

    @admin.display(description="임베딩 상태")
    def embedding_state(self, obj):
        if obj.embedding is None:
            return format_html(
                '<b style="color:#a16207">없음</b> — backfill_embeddings 대기 중'
            )
        return format_html('<span style="color:#15803d">있음 ({}차원)</span>', len(obj.embedding))

    @admin.display(description="선택한 문서를 승인")
    def approve_documents(self, request, queryset):
        """일괄 승인. **누가 언제 승인했는지 함께 남긴다.**"""
        updated = queryset.filter(is_approved=False).update(
            is_approved=True, approved_by=request.user, approved_at=timezone.now()
        )
        self.message_user(request, f"{updated}건을 승인했습니다.")

    @admin.display(description="선택한 문서의 승인을 취소")
    def unapprove_documents(self, request, queryset):
        """승인 취소 — 지우지 않고 검색에서만 빼는 안전한 되돌리기다."""
        updated = queryset.filter(is_approved=True).update(
            is_approved=False, approved_by=None, approved_at=None
        )
        self.message_user(request, f"{updated}건의 승인을 취소했습니다.")

    def save_model(self, request, obj, form, change):
        # 체크박스로 승인했을 때도 승인자·시각이 남게 한다
        if obj.is_approved and obj.approved_by_id is None:
            obj.approved_by = request.user
            obj.approved_at = timezone.now()
        elif not obj.is_approved:
            obj.approved_by = None
            obj.approved_at = None
        super().save_model(request, obj, form, change)


@admin.register(AiUsageLog)
class AiUsageLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """AI 호출 이력 — 쿼터·비용 추적.

    `is_error` 를 필터에 두는 이유 — **실패한 호출은 쿼터를 차감하지 않는다.**
    "쿼터가 이상하게 빨리 닳는다"는 문의가 오면 여기서 실패율부터 본다.
    """

    list_display = ("created_at", "member", "scope", "model", "tokens_in",
                    "tokens_out", "latency_ms", "is_error")
    list_filter = ("scope", "model", "is_error")
    search_fields = ("member__username", "member__email")
    date_hierarchy = "created_at"
    list_select_related = ("member",)
    ordering = ("-created_at",)


@admin.register(WebFetchLog)
class WebFetchLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """웹 수집 로그 — 오남용 추적.

    ★ **`is_blocked` 가 이 화면의 핵심이다.** SSRF 방어에 **걸린 시도**야말로
    봐야 한다. 사설망 주소를 반복해서 넣는 사용자가 있으면 그게 신호다.
    """

    list_display = ("created_at", "member", "url_preview", "status_code",
                    "size_bytes", "elapsed_ms", "is_blocked", "block_reason")
    list_filter = ("is_blocked", "block_reason")
    search_fields = ("url", "member__username")
    date_hierarchy = "created_at"
    list_select_related = ("member",)
    ordering = ("-created_at",)

    @admin.display(description="URL")
    def url_preview(self, obj):
        return obj.url if len(obj.url) <= 70 else obj.url[:70] + "…"


# `SavedSheet` 은 등록하지 않는다 — 회원 개인의 작업물이다.
# 운영자가 목록으로 훑을 이유가 없고, 열어 보는 것 자체가 사생활 침해에 가깝다.
# 지원 요청이 들어오면 그때 조건부로 붙인다.
_ = SavedSheet
