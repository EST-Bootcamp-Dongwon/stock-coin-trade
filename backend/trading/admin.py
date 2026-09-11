"""trading Admin — 주문 · 체결 · 포지션 · 대체자산 카탈로그 (F-19).

★ **주문·체결·포지션은 전부 읽기 전용이다** ─────────────────────────────
운영자가 참가자의 주문을 고칠 수 있으면 **순위를 조작할 수 있다.**
정정이 정말 필요하면 `AdminAuditLog` 에 사유가 남는 별도 경로로 하고,
그 경로는 슈퍼유저만 쓴다 (F-19 6장).

**고칠 수 있는 것은 `AlternativeProduct` 하나뿐이다** — v1.0 이 파이썬 상수로
하드코딩하던 걸 테이블로 옮긴 목적이 "운영자가 Admin 에서 고칠 수 있게"였다.
"""

from django.contrib import admin
from django.utils.html import format_html

from core.admin import ReadOnlyAdminMixin
from trading.models import (
    AlternativeProduct,
    AvgDownSimulation,
    Execution,
    Order,
    OrderStatus,
    Position,
)


class ExecutionInline(admin.TabularInline):
    """주문 상세에서 체결 조각을 함께 본다.

    `Order.avg_fill_price` 가 이 조각들의 가중평균이라, 값이 이상할 때
    되짚어 볼 곳이 바로 여기다.
    """

    model = Execution
    extra = 0
    fields = ("seq", "qty", "price", "amount", "price_level", "is_assumed_depth", "executed_at")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Order)
class OrderAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """주문 — 주식·코인·대체자산 통합.

    `source` 필터가 중요하다 — `DEMO_SEED` 로 들어온 샘플 주문과 실제 주문을
    목록에서 바로 갈라 볼 수 있어야 한다 (05 문서 5.4).
    """

    list_display = ("id", "created_at", "account", "symbol", "asset_class", "side",
                    "order_type", "status_badge", "requested_qty", "filled_qty",
                    "avg_fill_price", "realized_pnl_display", "source")
    list_filter = ("status", "asset_class", "side", "order_type", "source")
    search_fields = ("symbol", "account__member__username", "account__member__email")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("account", "account__member")
    list_per_page = 50
    inlines = [ExecutionInline]

    @admin.display(description="상태", ordering="status")
    def status_badge(self, obj):
        colors = {
            OrderStatus.FILLED: "#15803d",
            OrderStatus.PARTIAL: "#a16207",
            OrderStatus.REJECTED: "#b91c1c",
            OrderStatus.CANCELLED: "#64748b",
        }
        return format_html(
            '<b style="color:{}">{}</b>', colors.get(obj.status, "#2563eb"), obj.get_status_display()
        )

    @admin.display(description="실현손익", ordering="realized_pnl")
    def realized_pnl_display(self, obj):
        """매도 체결 시점에 기록된 값이다 (B-1). 매수 주문에는 없다."""
        if obj.realized_pnl is None:
            return "-"
        color = "#b91c1c" if obj.realized_pnl < 0 else "#15803d"
        # `format_html` 은 인자를 SafeString 으로 바꾸므로 `{:,}` 를 쓸 수 없다.
        # 숫자 포맷은 미리 끝내고 문자열만 넘긴다 (accounts/admin.py 같은 이유).
        return format_html(
            '<span style="color:{}">{}원</span>', color, f"{obj.realized_pnl:,}"
        )


@admin.register(Execution)
class ExecutionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """체결 조각.

    `is_assumed_depth` 를 목록에 띄운다 — 10호가를 넘어 **추정 잔량으로 체결된**
    조각이다(B-4). 실제 시장보다 유리한 가정이라 "얼마나 자주 일어나는가"를
    운영자가 볼 수 있어야 한다. v2.0 관통 원칙 ②의 적용이다.
    """

    list_display = ("order", "seq", "qty", "price", "amount", "price_level",
                    "is_assumed_depth", "executed_at")
    list_filter = ("is_assumed_depth",)
    search_fields = ("order__symbol",)
    date_hierarchy = "executed_at"
    list_select_related = ("order",)
    ordering = ("-executed_at",)


@admin.register(Position)
class PositionAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """현재 보유. 계좌 × 종목당 1행이고, **수량이 0 이 되면 행이 사라진다** (F-03 7.2)."""

    list_display = ("account", "symbol", "asset_class", "qty", "avg_price",
                    "principal", "updated_at")
    list_filter = ("asset_class",)
    search_fields = ("symbol", "account__member__username")
    list_select_related = ("account", "account__member")
    ordering = ("-updated_at",)


@admin.register(AlternativeProduct)
class AlternativeProductAdmin(admin.ModelAdmin):
    """대체자산 카탈로그 11종 — **이 앱에서 유일하게 편집 가능한 모델.**

    v1.0 은 `alternatives.py` 의 `CATALOG` 상수라 기준가를 바꾸려면 재배포해야 했다.

    `symbol` 은 PK 이자 `Order.symbol` · `Position.symbol` 과 문자열로 맞물리는 값이라
    **이미 만들어진 행에서는 읽기 전용**으로 둔다. 바꾸면 과거 주문이 고아가 된다.
    """

    list_display = ("sort_order", "symbol", "name", "category", "base_price_display",
                    "multiplier", "unit_label", "margin_rate_pct", "volatility_pct", "is_active")
    list_filter = ("category", "is_active")
    search_fields = ("symbol", "name", "description")
    ordering = ("sort_order", "symbol")
    list_editable = ("is_active",)

    fieldsets = (
        ("식별", {"fields": ("symbol", "name", "category", "description")}),
        ("가격·계약", {
            "fields": ("base_price", "multiplier", "unit_label", "margin_rate_pct",
                       "volatility_pct", "point_scale", "actual_multiplier"),
            "description": "<b>volatility_pct</b> 는 일간 진폭이다 — 옵션·파생 1.8 / 나머지 0.9. "
                           "가격은 이 값을 진폭으로 하는 결정론적 수식으로 만들어진다.",
        }),
        ("부동산 지도", {
            "fields": ("map_lat", "map_lng", "map_label"),
            "description": "부동산 상품만 채운다. Leaflet 마커 위치와 팝업 표시명이다.",
        }),
        ("노출", {"fields": ("is_active", "sort_order")}),
    )

    def get_readonly_fields(self, request, obj=None):
        # 새로 만들 때만 symbol 을 입력할 수 있다
        return ("symbol",) if obj else ()

    @admin.display(description="기준가", ordering="base_price")
    def base_price_display(self, obj):
        return f"{obj.base_price:,}원"


@admin.register(AvgDownSimulation)
class AvgDownSimulationAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """물타기 시뮬레이션 기록.

    **1차에는 저장만 하고 회고 화면은 v2.1** 이다(F-11 3.4). 그때까지 이 화면이
    "얼마나 쓰이는가 · 실제 주문으로 이어지는가"를 보는 유일한 창구다.
    """

    list_display = ("created_at", "account", "symbol", "executed_order")
    search_fields = ("symbol", "account__member__username")
    date_hierarchy = "created_at"
    list_select_related = ("account", "account__member", "executed_order")
    ordering = ("-created_at",)
