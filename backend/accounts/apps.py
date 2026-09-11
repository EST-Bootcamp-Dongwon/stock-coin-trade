"""회원 · 계좌 · API 키 앱 설정."""

from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "회원 · 계좌 · API 키"

    # ready() 에 스케줄러나 DDL 을 붙이지 않는다 (05 문서 6장).
    # ready() 는 manage.py 커맨드에서도 호출되어 마이그레이션 중에 실행되는 사고가 난다.
