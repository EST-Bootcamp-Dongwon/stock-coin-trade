"""대회 · 참가 · 스냅샷 · 랭킹 앱 설정."""

from django.apps import AppConfig


class ContestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "contests"
    verbose_name = "대회 · 참가 · 스냅샷 · 랭킹"

    # ready() 에 스케줄러나 DDL 을 붙이지 않는다 (05 문서 6장).
    # ready() 는 manage.py 커맨드에서도 호출되어 마이그레이션 중에 실행되는 사고가 난다.
