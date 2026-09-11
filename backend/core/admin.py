"""core Admin — 배치 이력 · 감사 로그 · 설정 (F-19 5장).

세션 5 는 "모델 전부 자동 등록"이었다. 여기서 **운영에 실제로 쓰는 화면**으로 바꾼다.

Django 관점 — FastAPI 에는 이런 게 없어서 운영 화면을 직접 만들거나 DB 를 psql 로
봐야 했다. `ModelAdmin` 은 목록 컬럼·필터·검색·읽기전용을 선언만 하면 화면이 나온다.
**선언 순서가 곧 화면 순서**라 코드가 그대로 UI 명세가 된다.

이 앱의 3종은 전부 **기록물**이다. 사람이 손으로 만들거나 고칠 것이 아니므로
`has_add_permission` · `has_change_permission` 을 닫는다 —
감사 로그를 Admin 에서 고칠 수 있으면 감사 로그가 아니다.
"""

from django.contrib import admin
from django.utils.html import format_html

from core.models import AdminAuditLog, AppSetting, CronJobRunDetail, DataSyncLog
from core.pg_cron import pg_cron_available


class ReadOnlyAdminMixin:
    """추가·수정·삭제를 전부 막고 조회만 남긴다.

    Django 관점 — 목록/상세 화면은 그대로 쓰면서 쓰기만 막는 방법이다.
    `readonly_fields` 로 필드를 하나씩 막는 것과 달리, **새 필드가 늘어나도
    자동으로 보호된다.**
    """

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DataSyncLog)
class DataSyncLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """배치 실행 이력.

    **"조용히 실패하는 배치"를 없애는 것이 이 화면의 목적이다** (E-07 2장).
    기본 정렬을 최신순으로 두고, 실패를 빨간 글씨로 띄운다.
    """

    list_display = ("job_name", "status_badge", "started_at", "duration_display",
                    "rows_affected", "triggered_by")
    list_filter = ("status", "triggered_by", "job_name")
    search_fields = ("job_name",)
    date_hierarchy = "started_at"
    ordering = ("-started_at",)
    list_per_page = 50

    @admin.display(description="상태", ordering="status")
    def status_badge(self, obj):
        colors = {"SUCCESS": "#15803d", "FAILED": "#b91c1c", "RUNNING": "#a16207"}
        return format_html(
            '<b style="color:{}">{}</b>', colors.get(obj.status, "#334155"), obj.get_status_display()
        )

    @admin.display(description="소요")
    def duration_display(self, obj):
        """실행 시간. 갑자기 느려진 잡을 목록에서 바로 알아채기 위한 컬럼이다."""
        if not obj.finished_at:
            return "실행중"
        seconds = (obj.finished_at - obj.started_at).total_seconds()
        return f"{seconds:.1f}초"


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """운영자 조치 감사 로그.

    ★ **읽기 전용이 본질이다.** 순위에 영향을 주는 조작이 흔적 없이 일어나면 안 되는데,
    그 흔적을 Admin 에서 고칠 수 있으면 장치 전체가 무의미해진다 (E-07 3장).
    """

    list_display = ("created_at", "actor", "action", "target_display", "reason", "ip")
    list_filter = ("action", "target_model")
    search_fields = ("reason", "target_id", "actor__username", "actor__email")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_select_related = ("actor",)   # N+1 방지 — 목록마다 actor 를 조인해 온다
    list_per_page = 50

    @admin.display(description="대상")
    def target_display(self, obj):
        return f"{obj.target_model}#{obj.target_id}"


@admin.register(AppSetting)
class AppSettingAdmin(admin.ModelAdmin):
    """런타임 설정값.

    **추가·삭제는 막고 수정만 연다.** 키는 코드가 읽는 이름이라 Admin 에서 새로
    만들면 아무도 안 읽는 행이 생기고, 지우면 코드가 기본값으로 조용히 되돌아간다.
    키를 늘리는 것은 마이그레이션의 일이다 (`core/0002_seed_app_settings`).
    """

    list_display = ("key", "value_preview", "description", "updated_at")
    search_fields = ("key", "description")
    readonly_fields = ("key", "updated_at")   # 키는 코드가 참조한다 — 못 바꾸게 한다
    ordering = ("key",)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="값")
    def value_preview(self, obj):
        text = str(obj.value)
        return text if len(text) <= 60 else text[:60] + "…"


@admin.register(CronJobRunDetail)
class CronJobRunDetailAdmin(ReadOnlyAdminMixin, admin.ModelAdmin):
    """pg_cron 자체 실행 이력 — **운영 DB 에서만 보인다** (F-20 5장).

    `managed=False` + `db_table='cron"."job_run_details'` 라 pg_cron 이 설치된
    DB 에만 존재한다. 로컬 개발 DB 에서 목록을 열면 곧장 ProgrammingError 다.
    그래서 **권한 훅으로 화면 자체를 숨긴다** — 링크를 눌러야 에러가 나는 것보다
    링크가 없는 편이 낫다.

    ★★ **이 화면의 status 는 잡의 성패가 아니다.** `net.http_post` 는 비동기라
    "요청을 걸었다"까지만 말한다. 잡이 무엇을 했는지는 **배치 실행 이력**
    (`DataSyncLog`)이 말한다. 자세한 설명은 `core/pg_cron.py` 모듈 docstring 참조.
    """

    list_display = ("start_time", "status_badge", "jobid", "duration_display",
                    "command_preview", "message_preview")
    # ★ 상세 화면으로 가는 링크를 만들지 않는다. `jobid` 가 pk 로 잡혀 있지만
    #   실제로는 **잡 하나에 실행 이력이 여러 건**이라 pk 가 유일하지 않다.
    #   링크를 열면 MultipleObjectsReturned 가 난다.
    list_display_links = None
    ordering = ("-start_time",)
    list_per_page = 50
    # 전체 건수 COUNT 를 생략한다. 초 단위 잡(F-20 잡 1·2·3)이 돌기 시작하면
    # 이 테이블은 하루에 수만 행씩 늘어난다.
    show_full_result_count = False

    def has_module_permission(self, request):
        """pg_cron 이 없는 DB 에서는 Admin 첫 화면의 목록에서 아예 감춘다."""
        return pg_cron_available() and super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        return pg_cron_available() and super().has_view_permission(request, obj)

    @admin.display(description="상태", ordering="status")
    def status_badge(self, obj):
        color = "#15803d" if obj.status == "succeeded" else "#b91c1c"
        return format_html('<b style="color:{}">{}</b>', color, obj.status)

    @admin.display(description="소요")
    def duration_display(self, obj):
        if not obj.end_time:
            return "실행중"
        return f"{(obj.end_time - obj.start_time).total_seconds():.1f}초"

    @admin.display(description="명령")
    def command_preview(self, obj):
        text = (obj.command or "").strip().replace("\n", " ")
        return text if len(text) <= 80 else text[:80] + "…"

    @admin.display(description="반환 메시지")
    def message_preview(self, obj):
        text = (obj.return_message or "").strip().replace("\n", " ")
        return text if len(text) <= 80 else text[:80] + "…"
