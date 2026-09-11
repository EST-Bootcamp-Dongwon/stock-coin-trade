"""market Admin — 종목 마스터 · 영업일 · 외부 토큰 (F-19).

**캐시 5종은 등록하지 않는다.** 기계가 쓰고 지우는 테이블이고, TTL 이 5~300초라
Admin 에서 열어 봤을 땐 이미 다른 값이다. 시세가 이상하면 캐시 행이 아니라
`core.DataSyncLog` 의 수집 잡 실패를 본다.

**손으로 고칠 일이 실제로 있는 것만 연다** — 영업일(임시 휴장)과 수업용 14종 지정이다.
"""

from django.contrib import admin
from django.utils.html import format_html

from core.admin import ReadOnlyAdminMixin
from market.models import (
    ApiCallBudget,
    ChartCache,
    CryptoRank,
    ExternalToken,
    IndexCache,
    MarketIndexSnapshot,
    NewsCache,
    OrderbookCache,
    QuoteCache,
    StockMaster,
    SubscriptionRegistry,
    TradingCalendar,
    UpbitMarket,
)


@admin.register(StockMaster)
class StockMasterAdmin(admin.ModelAdmin):
    """종목 마스터 — pykrx 일배치(매 영업일 16:00 KST)가 적재한다.

    ★ **편집은 `is_featured` 하나만 의도한 것이다** (F-06 3장 수업용 14종).
    나머지 컬럼은 다음 배치가 덮어쓰므로 고쳐도 하루를 못 간다.
    그래서 배치가 채우는 값은 전부 읽기 전용으로 막아 뒀다 —
    "고쳤는데 내일 원복됐다"는 혼란을 미리 없앤다.
    """

    list_display = ("symbol", "name", "market", "stock_type", "sector_name",
                    "market_cap_display", "is_supervised", "alert_level",
                    "is_delisted", "is_featured")
    list_filter = ("market", "stock_type", "is_featured", "is_supervised", "is_delisted")
    search_fields = ("symbol", "name", "sector_name")
    ordering = ("-market_cap",)
    list_editable = ("is_featured",)
    list_per_page = 50

    readonly_fields = ("symbol", "name", "market", "stock_type", "sector_code", "sector_name",
                       "listing_date", "market_cap", "shares_outstanding", "avg_turnover_5d",
                       "close_price", "is_supervised", "alert_level", "is_delisted", "updated_at")

    def has_add_permission(self, request):
        return False   # 적재는 sync_stock_master 배치가 한다

    @admin.display(description="시가총액", ordering="market_cap")
    def market_cap_display(self, obj):
        if not obj.market_cap:
            return "-"
        return f"{obj.market_cap / 1_0000_0000_0000:.2f}조"


@admin.register(TradingCalendar)
class TradingCalendarAdmin(admin.ModelAdmin):
    """영업일.

    ★ **손으로 고칠 일이 실제로 있는 화면이다** — 임시 휴장(재난·시스템 장애)은
    pykrx 가 미리 알 수 없다. 운영자가 여기서 `is_open` 을 끄고 `note` 를 남긴다.

    `source` 를 `MANUAL` 로 바꿔 두면 **배치가 덮어쓰지 않는다** (E-04 4장).
    고쳤는데 다음 배치가 되돌리면 아무 소용이 없기 때문이다.
    """

    list_display = ("date", "is_open", "source", "note")
    list_filter = ("is_open", "source")
    search_fields = ("note",)
    date_hierarchy = "date"
    ordering = ("-date",)
    list_editable = ("is_open", "source", "note")
    list_per_page = 60


@admin.register(UpbitMarket)
class UpbitMarketAdmin(admin.ModelAdmin):
    """업비트 마켓. `is_warning`(유의 종목)만 손으로 만질 일이 있다."""

    list_display = ("market", "korean_name", "english_name", "is_warning", "is_active", "updated_at")
    list_filter = ("is_warning", "is_active")
    search_fields = ("market", "korean_name", "english_name")
    ordering = ("market",)
    list_editable = ("is_warning", "is_active")

    def has_add_permission(self, request):
        return False   # 적재는 sync_upbit_markets 배치가 한다


@admin.register(ExternalToken)
class ExternalTokenAdmin(admin.ModelAdmin):
    """외부 브로커 토큰.

    ★ **`access_token` 을 화면에 절대 띄우지 않는다.** 유효한 자격증명이라
    화면에 보이는 순간 어깨너머로도 새어 나간다. 앞 8자와 만료 여부만 보여준다.

    **`APP_KEY` · `APP_SECRET` 은 애초에 DB 에 없다** — 환경변수에만 둔다.
    여기 있는 건 그것으로 발급받은 24시간짜리 액세스 토큰뿐이다.

    삭제는 열어 둔다 — 토큰이 꼬였을 때 **지우고 재발급받는 것이 정상 복구 경로**다.
    """

    list_display = ("provider", "environment", "token_preview", "validity", "issued_at", "expires_at")
    list_filter = ("provider", "environment")
    readonly_fields = ("provider", "environment", "access_token", "token_type",
                       "issued_at", "expires_at")
    ordering = ("provider", "environment")

    def has_add_permission(self, request):
        return False   # 발급은 브로커 클라이언트가 한다

    @admin.display(description="토큰")
    def token_preview(self, obj):
        return f"{obj.access_token[:8]}…" if obj.access_token else "-"

    @admin.display(description="유효")
    def validity(self, obj):
        if obj.is_valid:
            return format_html('<b style="color:#15803d">유효</b>')
        return format_html('<b style="color:#b91c1c">만료</b>')


@admin.register(CryptoRank)
class CryptoRankAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """코인 랭킹 — **KRW 기준으로 재정의**했다 (결함 D-3).

    v1.0 은 CoinMarketCap Top 100(USD 기준)을 매시 TRUNCATE 후 재적재했는데
    **어느 화면에서도 쓰지 않았다.** 이 목록이 비어 있으면 수집 잡이 죽은 것이다.
    """

    list_display = ("rank", "market", "trade_price", "acc_trade_price_24h",
                    "change_rate", "updated_at")
    search_fields = ("market",)
    ordering = ("rank",)


@admin.register(MarketIndexSnapshot)
class MarketIndexSnapshotAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """지수 일별 종가 — NAV 차트의 벤치마크 곡선 원본.

    **`nav` 컬럼이 없는 것이 맞다** (A-1). NAV 는 대회 시작일을 1000 으로 놓은
    값인데 이 테이블은 대회와 무관한 전역 시계열이라, 대회가 2개면 같은 날짜에
    NAV 가 2개여야 한다. 종가만 저장하고 조회 시 환산한다.
    """

    list_display = ("date", "index_code", "close", "change_pct")
    list_filter = ("index_code",)
    date_hierarchy = "date"
    ordering = ("-date", "index_code")


@admin.register(SubscriptionRegistry)
class SubscriptionRegistryAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """시세 폴링 우선순위.

    초당 5건 = 분당 300건인데 전 종목이 2,700여 개다. **무엇을 갱신하고 있는가**를
    보는 화면이고, 시세가 늦다는 문의가 오면 여기서 우선순위부터 확인한다 (E-04 7장).
    """

    list_display = ("priority", "asset_class", "symbol", "reason",
                    "last_polled_at", "expires_at")
    list_filter = ("priority", "asset_class")
    search_fields = ("symbol",)
    ordering = ("priority", "last_polled_at")


# 등록하지 않는 것들 ─────────────────────────────────────────────
#   캐시 5종 (Quote·Orderbook·Chart·Index·News) — TTL 5~300초. 열어 봤을 땐 이미 다른 값이다
#   ApiCallBudget — 초 단위 윈도우 카운터. 사람이 볼 화면이 아니다
_ = (QuoteCache, OrderbookCache, ChartCache, IndexCache, NewsCache, ApiCallBudget)
