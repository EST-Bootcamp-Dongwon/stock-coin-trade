"""learning Admin — 레슨 · 가이드 · 진행률 (F-19 4장 content_admin 의 주 작업장).

**콘텐츠를 DB 로 뺀 목적이 바로 이 화면이다** — 강사님이 수업 자료를 갱신할 때
코드 배포 없이 반영되게 하는 것 (E-05 2장).
"""

from datetime import timedelta

from django.contrib import admin
from django.utils.html import format_html

from core.admin import ReadOnlyAdminMixin
from core.time import today_kst
from learning.models import Guide, LearningProgress, Lesson

# 가이드가 낡았다고 보는 기간. `core.AppSetting["guide.stale_after_days"]` 의 기본값과
# 같은 값이다 — 목록 배지 하나 때문에 행마다 설정을 읽지는 않는다.
GUIDE_STALE_DAYS = 180


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    """투자분석 레슨 13종.

    ★ **`key` 는 이미 만들어진 행에서 읽기 전용이다.** URL(`/learn/analysis/<key>/`)이자
    v1.0 `localStorage` 진행률을 서버로 옮길 때의 매칭 키다(E-05 3.3).
    바꾸면 링크가 죽고 이관이 어긋난다.

    `content` 는 jsonb 원시 편집이다. 구조는::

        {"icon", "subtitle", "description", "theory":[3줄], "tip", "code",
         "sample_data":{"headers","rows"}, "result":{"value","label","bars"},
         "explain", "is_interactive"}

    필드별 폼으로 펼치는 것은 학습 화면(F-17) 작업과 함께 한다 — 지금 폼을 만들면
    화면이 실제로 무엇을 읽는지 확정되기 전에 스키마를 굳히게 된다.
    """

    list_display = ("group", "order", "title", "key", "max_points",
                    "is_interactive", "is_published", "updated_at")
    list_filter = ("group", "is_published")
    search_fields = ("key", "title", "group")
    ordering = ("group", "order")
    list_editable = ("order", "is_published")

    def get_readonly_fields(self, request, obj=None):
        return ("key", "created_at", "updated_at") if obj else ("created_at", "updated_at")

    @admin.display(description="인터랙티브", boolean=True)
    def is_interactive(self, obj):
        """본문 없이 클라이언트가 통째로 그리는 레슨인가 (E-05 2.1).

        `quant-day5` · `json-consistency` 두 건이다. 값을 바꿀 때마다 즉시 반응해야
        하는 계산이라 서버로 옮기지 않았다. **`content` 를 채워도 화면에 안 나온다**는
        사실을 운영자가 알 수 있어야 한다.
        """
        return bool(obj.content.get("is_interactive"))


@admin.register(Guide)
class GuideAdmin(admin.ModelAdmin):
    """학습가이드 4종.

    ★ **`last_verified_at` 배지가 이 화면의 존재 이유다** (E-05 4.1) —
    외부 서비스(KB증권·KIS·TradingView)의 UI 를 설명하는 문서라 **원본이 바뀌면 낡는다.**
    6개월이 지나면 목록에 빨간 경고가 뜬다.

    시드는 `content=""` · `is_published=False` 로 들어와 있다. HTML 원본
    (`frontend/learning/*.html` · `frontend/trade/pine-guide.html`)을 마크다운으로
    옮긴 뒤 공개로 바꾼다. **본문 없는 가이드가 노출되지 않게** 하는 장치다.
    """

    # ★ `list_editable` 에 넣은 필드가 `list_display` 의 **첫 칸이면 안 된다** (admin.E124).
    #   첫 칸은 상세로 들어가는 링크 자리인데, 편집 위젯이 되면 링크가 사라진다.
    #   그래서 `title` 을 앞에 두고 `order` 를 뒤로 뺐다.
    list_display = ("title", "order", "slug", "freshness", "has_content", "is_published")
    list_filter = ("is_published",)
    search_fields = ("slug", "title", "content")
    ordering = ("order", "slug")
    list_editable = ("order", "is_published")

    def get_readonly_fields(self, request, obj=None):
        return ("slug", "created_at", "updated_at") if obj else ("created_at", "updated_at")

    @admin.display(description="최종 확인일", ordering="last_verified_at")
    def freshness(self, obj):
        if not obj.last_verified_at:
            return format_html('<b style="color:#b91c1c">확인일 없음</b>')
        stale_on = obj.last_verified_at + timedelta(days=GUIDE_STALE_DAYS)
        if today_kst() > stale_on:
            return format_html(
                '<b style="color:#b91c1c">{} · 갱신 필요</b>', obj.last_verified_at
            )
        return format_html('<span style="color:#15803d">{}</span>', obj.last_verified_at)

    @admin.display(description="본문", boolean=True)
    def has_content(self, obj):
        return bool(obj.content.strip())


@admin.register(LearningProgress)
class LearningProgressAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """학습 진행률.

    **읽기 전용이다.** 진행률은 회원이 학습하며 쌓는 값이고, 대회 참가 조건
    (`Contest.entry_requirement = {"min_learning_points": 30}`)으로 쓰인다(E-05 3.4).
    운영자가 손으로 올려 주면 참가 자격을 임의로 주는 것과 같다.
    """

    list_display = ("member", "lesson", "progress_points", "completed_at", "updated_at")
    list_filter = ("lesson__group", "lesson")
    search_fields = ("member__username", "member__email", "lesson__title")
    list_select_related = ("member", "lesson")
    ordering = ("-updated_at",)
