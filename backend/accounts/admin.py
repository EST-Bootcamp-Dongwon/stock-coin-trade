"""accounts Admin — 회원 · 계좌 · API 키 · 관심종목 (F-19).

`Member` 는 `AbstractUser` 상속이라 **`UserAdmin` 을 상속해야 한다.**
그냥 `admin.ModelAdmin` 을 쓰면 비밀번호 칸이 **평문 입력창**이 되어,
운영자가 비밀번호를 고치는 순간 해시가 아닌 평문이 저장된다. 가장 위험한 실수다.

Django 관점 — FastAPI 에서는 회원 관리 화면을 통째로 만들어야 했고, 비밀번호
해싱을 잊는 사고도 우리 책임이었다. `UserAdmin` 은 비밀번호를 **읽기 전용 해시 + 별도
변경 링크**로 다루는 화면을 이미 갖고 있다.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.html import format_html

from accounts.models import Account, ApiKey, Member, RateLimitCounter, Watchlist


class AccountInline(admin.TabularInline):
    """회원 상세에서 계좌를 함께 본다.

    v2.0 은 회원 1명에 계좌가 4개 이상(연습 3 + 대회 n)이라, 계좌를 따로 찾아
    들어가면 "이 사람 상태가 어떤가"를 한눈에 볼 수 없다.
    """

    model = Account
    extra = 0
    fields = ("mode", "contest", "cash", "initial_capital", "is_frozen", "reset_at")
    readonly_fields = ("mode", "contest", "reset_at")
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        # 계좌 생성은 `accounts.services.register_member()` · 대회 참가 승인이 한다.
        # Admin 에서 손으로 만들면 부분 유니크 제약·CHECK 에 걸리거나,
        # 걸리지 않더라도 초기 자본이 어긋난 계좌가 생긴다.
        return False


@admin.register(Member)
class MemberAdmin(UserAdmin):
    """회원.

    `UserAdmin` 의 `fieldsets` 를 **덮어쓰지 않고 이어 붙인다** — 원본에는
    비밀번호·권한·중요 일자가 이미 잘 배치되어 있다.
    """

    list_display = ("username", "email", "display_name", "account_count",
                    "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = ("username", "email", "display_name")
    ordering = ("-date_joined",)
    inlines = [AccountInline]

    # UserAdmin 기본 fieldsets 에 v2.0 이 추가한 필드만 끼워 넣는다
    fieldsets = UserAdmin.fieldsets + (
        ("v2.0 추가 정보", {"fields": ("display_name", "avatar")}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ("v2.0 추가 정보", {"fields": ("email", "display_name")}),
    )

    @admin.display(description="계좌 수")
    def account_count(self, obj):
        return obj.accounts.count()

    def get_queryset(self, request):
        # 위 account_count 가 목록 행마다 COUNT 를 날리는 것을 막는다
        from django.db.models import Count
        return super().get_queryset(request).annotate(_account_count=Count("accounts"))


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    """계좌 — v2.0 데이터 모델의 심장.

    **추가를 막는다.** 계좌 생성 경로는 둘뿐이다:
      · 연습 계좌 3종 — 가입 시 `accounts.services.register_member()`
      · 대회 계좌 — 참가 승인 시점

    Admin 에서 손으로 만들면 `account_ck_mode_contest_match` CHECK 에 걸리거나,
    통과하더라도 `initial_capital` 이 대회 설정과 어긋난 계좌가 생긴다.
    """

    list_display = ("id", "member", "mode", "contest", "cash_display",
                    "return_pct", "is_frozen", "reset_at")
    list_filter = ("mode", "is_frozen", "contest")
    search_fields = ("member__username", "member__email", "member__display_name")
    readonly_fields = ("member", "mode", "contest", "created_at", "updated_at", "reset_at")
    list_select_related = ("member", "contest")
    ordering = ("-id",)

    def has_add_permission(self, request):
        return False

    @admin.display(description="현금", ordering="cash")
    def cash_display(self, obj):
        return f"{obj.cash:,}원"

    @admin.display(description="현금 기준 수익률")
    def return_pct(self, obj):
        """**현금만 본 값이다** — 보유 평가액은 포함하지 않는다.

        정확한 수익률은 `contests.DailySnapshot` 이 매 영업일에 계산한다.
        여기서 포지션까지 평가하려면 목록 행마다 시세 조회가 붙어 Admin 이 느려진다.
        오해를 막으려고 컬럼 이름에 "현금 기준"을 박아 둔다.
        """
        if not obj.initial_capital:
            return "-"
        pct = (obj.cash - obj.initial_capital) / obj.initial_capital * 100
        color = "#b91c1c" if pct < 0 else "#15803d"
        # ★ `format_html` 은 인자를 먼저 이스케이프해 SafeString 으로 바꾼다.
        #   그래서 `{:.2f}` 같은 **숫자 포맷 스펙을 쓸 수 없다**
        #   (ValueError: Unknown format code 'f' for object of type 'SafeString').
        #   숫자는 미리 문자열로 만들어 넘긴다.
        return format_html('<span style="color:{}">{}%</span>', color, f"{pct:.2f}")


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    """Open API 키.

    ★ **`key_hash` 는 목록에도 상세에도 띄우지 않는다.** 평문은 애초에 저장하지 않지만,
    해시라도 화면에 뿌릴 이유가 없다. 식별은 `key_prefix` 로 충분하다.

    **추가를 막는다** — 키 발급은 평문을 1회만 보여주는 절차가 있어야 한다.
    Admin 에서 행을 만들면 `key_hash` 에 아무 문자열이나 들어가고, 그 키로는
    아무도 인증할 수 없는 유령 행이 된다.
    """

    list_display = ("name", "member", "account", "key_prefix", "is_active",
                    "last_used_at", "revoked_at")
    list_filter = ("is_active",)
    search_fields = ("name", "key_prefix", "member__username", "member__email")
    readonly_fields = ("member", "account", "key_prefix", "key_hash",
                       "last_used_at", "created_at", "updated_at")
    exclude = ("key_hash",)
    list_select_related = ("member", "account")
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False


@admin.register(Watchlist)
class WatchlistAdmin(admin.ModelAdmin):
    list_display = ("member", "asset_class", "symbol", "sort_order", "created_at")
    list_filter = ("asset_class",)
    search_fields = ("symbol", "member__username")
    list_select_related = ("member",)


# `RateLimitCounter` 는 등록하지 않는다.
# 분당 1행 × API 키 수로 불어나는 **기계용 카운터**라 사람이 볼 화면이 아니고,
# cleanup 잡이 지운다. 유량 문제를 조사할 일이 생기면 그때 조건부로 붙인다.
_ = RateLimitCounter
