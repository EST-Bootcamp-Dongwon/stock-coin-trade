"""contests Admin — 대회 운영자가 실제로 쓰는 화면 (F-19).

**모델 11종을 전부 등록하지 않는다.** 스냅샷·랭킹·회전율은 정산 잡이 쓰고 지우는
파생 데이터라, Admin 에 CRUD 화면을 열어 두면 손댈 수 있게 된다.
손대면 정산 멱등성(E-02 12장)이 깨진다. **읽기 전용으로만 노출한다.**

    운영자가 고칠 수 있어야 하는 것 : Contest · Participation · RuleViolation
    보기만 해야 하는 것             : Snapshot · Ranking · Result · Turnover · Universe
"""

from django.contrib import admin
from django.utils.html import format_html

from contests.models import (
    Contest,
    ContestRanking,
    ContestResult,
    ContestSectorWeight,
    ContestStatus,
    ContestUniverse,
    DailySnapshot,
    IntradaySnapshot,
    Participation,
    RuleViolation,
    SnapshotHolding,
    WeeklyTurnover,
)
from core.admin import ReadOnlyAdminMixin


class ClosedContestGuardMixin:
    """정산이 끝난(`CLOSED`) 대회에 딸린 데이터는 수정할 수 없게 한다 (F-19 5장).

    확정된 결과를 나중에 고치면 참가자에게 통보한 순위와 DB 가 어긋난다.
    되돌릴 필요가 생기면 정산 재실행으로 하고, 그 사실이 `AdminAuditLog` 에 남아야 한다.
    """

    def _contest_of(self, obj):
        raise NotImplementedError

    def has_change_permission(self, request, obj=None):
        if obj is not None and self._contest_of(obj).status == ContestStatus.CLOSED:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and self._contest_of(obj).status == ContestStatus.CLOSED:
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Contest)
class ContestAdmin(admin.ModelAdmin):
    """대회 본체.

    ★ **상태(`status`)를 손으로 바꾸지 못하게 읽기 전용으로 둔다.** 상태 전이는
    pg_cron 의 `settle_contest` 잡이 한다(F-02 2장). 운영자가 중간에 `CLOSED` 로
    바꿔 버리면 정산이 돌지 않은 채 대회가 끝난다.

    `rule_set` JSON 을 폼으로 펼치는 것은 F-19 5장의 과제인데, **규칙 엔진(F-04)
    작업과 함께 해야 의미가 있다.** 지금은 원시 JSON 편집이고, 그 위험을 안내
    문구로 덮어 둔다.
    """

    list_display = ("name", "slug", "status_badge", "asset_class", "start_date",
                    "end_date", "participant_count", "approval_mode", "visibility")
    list_filter = ("status", "asset_class", "approval_mode", "visibility")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}   # 대회명을 치면 slug 가 따라온다
    date_hierarchy = "start_date"
    ordering = ("-start_date",)

    readonly_fields = ("status", "universe_frozen_at", "created_by", "created_at", "updated_at")

    fieldsets = (
        ("기본", {"fields": ("name", "slug", "description", "asset_class", "status")}),
        ("기간·정원", {"fields": ("start_date", "end_date", "entry_deadline", "capacity")}),
        ("참가·공개", {
            "fields": ("approval_mode", "visibility", "portfolio_visibility",
                       "portfolio_visible_top_n", "entry_requirement"),
            "description": (
                "<b>공개 범위</b>는 <b>대회가 목록에 뜨는지</b>를 정한다 "
                "(PUBLIC=목록에 표시 · LINK=주소를 아는 사람만 · PRIVATE=참가자만). "
                "<b>포트폴리오 공개 범위</b>는 <b>남의 보유 종목을 볼 수 있는지</b>다 — 이름이 "
                "비슷하지만 전혀 다른 설정이다.<br>"
                "화면의 판정 규칙(<code>contests.services.can_view_portfolio</code>):<br>"
                "· <b>ALL</b>(기본) — 전 참가자 공개. 동아리 대회는 이것을 쓴다. "
                "벤치마크의 '50등 이후 조회 불가' 결함을 없애는 것이 v2.0 의 설계다.<br>"
                "· <b>TOP_N</b> — 아래 '공개 상위 N명' 안에 드는 참가자만 공개한다. "
                "다만 <b>본인 것, 운영자, 그리고 순위가 없는 참가자(실격·포기·정산 전)는 "
                "언제나 공개</b>된다. 숨기면 회고 자료가 사라지고 '내가 왜 안 보이지' 문의가 온다.<br>"
                "· <b>SELF_ONLY</b> — 본인 것만. 외부 공개 대회에서 쓴다.<br>"
                "어느 값이든 <b>랭킹 표 자체(순위·별칭·기준가·편입비·종목수)는 전원 공개</b>다. "
                "이 설정이 가리는 것은 포트폴리오 상세(보유 종목·수익 종목·거래 이력)뿐이다."
            ),
        }),
        ("자본·요율", {
            "fields": ("initial_capital", "fee_bp", "tax_bp"),
            "description": "요율은 bp 단위다 — 10bp = 0.10%. "
                           "<b>진행 중인 대회의 요율을 바꾸면 이미 체결된 주문과 어긋난다.</b>",
        }),
        ("규칙", {
            "fields": ("rule_set",),
            "description": "원시 JSON 이다. "
                           "<b>키 이름을 틀리면 규칙 엔진이 조용히 기본값으로 동작한다.</b> "
                           "폼으로 펼치는 작업은 F-04 규칙 엔진과 함께 한다.",
        }),
        ("운영", {"fields": ("universe_frozen_at", "created_by", "created_at", "updated_at")}),
    )

    @admin.display(description="상태", ordering="status")
    def status_badge(self, obj):
        colors = {
            ContestStatus.ONGOING: "#15803d",
            ContestStatus.SETTLING: "#a16207",
            ContestStatus.CLOSED: "#334155",
            ContestStatus.CANCELLED: "#b91c1c",
        }
        return format_html(
            '<b style="color:{}">{}</b>', colors.get(obj.status, "#2563eb"), obj.get_status_display()
        )

    @admin.display(description="참가자")
    def participant_count(self, obj):
        return obj.participations.count()

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Participation)
class ParticipationAdmin(ClosedContestGuardMixin, admin.ModelAdmin):
    """참가 신청 · 승인 · 실격.

    ★ **실격은 삭제가 아니다.** `is_ranked=False` 로만 만들고 계좌·주문 이력은
    그대로 둔다 (F-02 4.3). 지우면 회고가 불가능하고 오판을 되돌릴 수 없다.
    그래서 `status` 와 `is_ranked` 는 편집 가능하고, `account` 는 읽기 전용이다.

    `disqualified_reason` 은 **비워 두고 실격시키지 못하게** 폼에서 검증한다 —
    사유 없는 실격은 나중에 아무도 설명할 수 없다 (F-19 6장 감사 원칙).
    """

    list_display = ("contest", "nickname", "member", "status", "is_ranked",
                    "joined_at", "approved_at")
    list_filter = ("status", "is_ranked", "contest")
    search_fields = ("nickname", "member__username", "member__email")
    readonly_fields = ("contest", "member", "account", "joined_at",
                       "created_at", "updated_at")
    list_select_related = ("contest", "member")
    ordering = ("-joined_at",)

    def _contest_of(self, obj):
        return obj.contest

    def has_add_permission(self, request):
        # 참가 신청은 회원이 화면에서 한다. Admin 에서 만들면 계좌가 없는 참가가 생긴다.
        return False

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)

        class GuardedForm(form):
            def clean(self):
                cleaned = super().clean()
                from contests.models import ParticipationStatus
                if (
                    cleaned.get("status") == ParticipationStatus.DISQUALIFIED
                    and not (cleaned.get("disqualified_reason") or "").strip()
                ):
                    from django.core.exceptions import ValidationError
                    raise ValidationError(
                        {"disqualified_reason": "실격 처리에는 사유 입력이 필수입니다."}
                    )
                return cleaned

        return GuardedForm


@admin.register(RuleViolation)
class RuleViolationAdmin(ClosedContestGuardMixin, admin.ModelAdmin):
    """규칙 위반 기록.

    **자동 실격은 하지 않는다** (회전율 4회 제외). "적극적으로 해소하려 노력했는가"는
    정성 판단이라, 시스템은 **운영자에게 근거를 주는 선까지**가 역할이다 (F-04 3.4).
    그래서 `is_resolved` 만 손으로 바꿀 수 있게 열어 둔다.
    """

    list_display = ("date", "participation", "rule", "severity", "is_resolved", "resolved_at")
    list_filter = ("rule", "severity", "is_resolved")
    search_fields = ("participation__nickname",)
    readonly_fields = ("participation", "rule", "date", "severity", "detail", "created_at")
    list_select_related = ("participation", "participation__contest")
    date_hierarchy = "date"
    ordering = ("-date",)

    def _contest_of(self, obj):
        return obj.participation.contest

    def has_add_permission(self, request):
        return False


# ── 이하 전부 읽기 전용 ─────────────────────────────────────────
# 정산 잡이 쓰고 지우는 파생 데이터다. Admin 에서 고치면 다음 정산이 덮어쓰거나
# (멱등성 덕분에) 조용히 원복되어, 고친 사람만 고쳤다고 믿는 상태가 된다.


@admin.register(DailySnapshot)
class DailySnapshotAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("date", "participation", "nav", "cumulative_return_pct",
                    "total_asset", "position_count", "invested_ratio_pct")
    list_filter = ("date", "participation__contest")
    search_fields = ("participation__nickname",)
    list_select_related = ("participation",)
    date_hierarchy = "date"
    ordering = ("-date", "-nav")


@admin.register(ContestRanking)
class ContestRankingAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("contest", "date", "rank", "prev_rank", "participation",
                    "nav", "cumulative_return_pct")
    list_filter = ("contest", "date")
    search_fields = ("participation__nickname",)
    list_select_related = ("contest", "participation")
    date_hierarchy = "date"
    ordering = ("-date", "rank")


@admin.register(ContestResult)
class ContestResultAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """최종 결과.

    `port_hhi` · `pnl_hhi` 를 목록에 띄우는 이유 — 관리 점수 산식은 1회 대회 후
    조정을 전제한다(F-04 6.2). **중간 값이 보여야 산식이 말이 되는지 판단할 수 있다.**
    """

    list_display = ("contest", "final_rank", "participation", "final_score", "grade",
                    "return_percentile", "management_percentile", "port_hhi", "pnl_hhi")
    list_filter = ("contest", "grade")
    search_fields = ("participation__nickname",)
    list_select_related = ("contest", "participation")
    ordering = ("contest", "final_rank")


@admin.register(WeeklyTurnover)
class WeeklyTurnoverAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """주간 회전율.

    `is_confirmed=False` 는 **이번 주 잠정치**다. 매일 `settle_daily` 가 덮어쓴다.
    목록에서 확정/잠정을 구분해 보여주지 않으면 문의가 들어온다 (E-02 11장).
    """

    list_display = ("week_start", "participation", "turnover_pct", "is_violation",
                    "is_confirmed", "violation_seq")
    list_filter = ("is_violation", "is_confirmed", "participation__contest")
    search_fields = ("participation__nickname",)
    list_select_related = ("participation",)
    ordering = ("-week_start",)


@admin.register(ContestUniverse)
class ContestUniverseAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("contest", "symbol", "name", "market", "sector_name",
                    "is_tradable_at_start", "frozen_at")
    list_filter = ("contest", "market")
    search_fields = ("symbol", "name")
    list_select_related = ("contest",)


@admin.register(ContestSectorWeight)
class ContestSectorWeightAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("contest", "sector_code", "sector_name", "market_weight_pct", "limit_pct")
    list_filter = ("contest",)
    search_fields = ("sector_code", "sector_name")
    list_select_related = ("contest",)


# `SnapshotHolding` · `IntradaySnapshot` 은 등록하지 않는다.
#   · SnapshotHolding — 참가자 100명 × 15종목 × 20영업일 ≈ 3만 행. 목록으로 볼 것이 아니고,
#     필요한 조회는 Top Pick 매트릭스 화면(F-05)이 대신한다. JSONB 원본이 정본이라
#     여기서 뭘 고쳐도 다음 재생성에 사라진다
#   · IntradaySnapshot — 당일분만 살고 매일 04:00 cleanup 이 지운다
_ = (SnapshotHolding, IntradaySnapshot)
