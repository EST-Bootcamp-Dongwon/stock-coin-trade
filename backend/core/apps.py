"""공통 · 배치 이력 · 감사 로그 앱 설정.

★ Admin 사이트를 갈아끼우는 `OpsAdminConfig` 는 **여기 두지 않는다.**
  `core/admin_apps.py` 에 따로 있다 — 이유는 그 파일 docstring 참조.
"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "공통 · 배치 이력 · 감사 로그"

    # ready() 에 스케줄러나 DDL 을 붙이지 않는다 (05 문서 6장).
    # ready() 는 manage.py 커맨드에서도 호출되어 마이그레이션 중에 실행되는 사고가 난다.
