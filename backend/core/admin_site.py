"""운영 Admin 사이트 — 대시보드 경고 배너 (F-20 5장 · F-19).

`config/settings.py` 의 `INSTALLED_APPS` 가 `core.apps.OpsAdminConfig` 를 통해
이 사이트를 기본 `admin.site` 로 세운다. 각 앱의 `@admin.register` 는 그대로 동작한다.
"""

from django.contrib import admin
from django.utils.functional import SimpleLazyObject

from core.job_health import collect_job_alerts


class OpsAdminSite(admin.AdminSite):
    """배치 경고 배너를 얹은 Admin.

    ★★ **`index_template` 을 다른 이름으로 두는 이유** ─────────────────────
    배너는 `admin/index.html` 을 확장해 붙이고 싶은데, 우리 템플릿을
    `templates/admin/index.html` 로 두고 `{% extends "admin/index.html" %}` 를
    적으면 **자기 자신을 확장하는 무한 재귀**가 된다 (템플릿 로더가 같은 이름으로
    우리 파일을 먼저 찾기 때문이다).

    `AdminSite.index_template` 은 바로 이런 경우를 위해 열려 있는 속성이다.
    이름을 `core/admin_index.html` 로 바꿔 두면 그 안에서 원본
    `admin/index.html` 을 안전하게 확장할 수 있다.
    """

    site_header = "주식·코인 모의투자 v2.0 운영"
    site_title = "v2.0 운영"
    index_title = "운영 대시보드"
    index_template = "core/admin_index.html"

    def each_context(self, request):
        """모든 Admin 화면이 공유하는 컨텍스트에 배치 상태를 얹는다.

        ★★ **`SimpleLazyObject` 로 감싸는 이유** — `each_context()` 는 Admin 의
        **모든 페이지**에서 불린다. 여기서 곧바로 쿼리를 돌리면 종목 마스터 목록을
        넘길 때마다, 회원 상세를 열 때마다 배치 이력 조회가 따라붙는다.

        지연 객체로 두면 **템플릿이 실제로 값을 꺼낼 때만** 쿼리가 나간다.
        지금은 대시보드(`core/admin_index.html`) 하나만 꺼내 쓰므로,
        다른 화면에서는 비용이 0 이다.
        """
        context = super().each_context(request)
        context["job_alerts"] = SimpleLazyObject(collect_job_alerts)
        return context
