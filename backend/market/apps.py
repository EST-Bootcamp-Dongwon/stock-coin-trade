"""시장 데이터 · 종목 마스터 · 시세 캐시 앱 설정."""

from django.apps import AppConfig


class MarketConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "market"
    verbose_name = "시장 데이터 · 종목 마스터 · 시세 캐시"

    # ready() 에 스케줄러나 DDL 을 붙이지 않는다 (05 문서 6장).
    # ready() 는 manage.py 커맨드에서도 호출되어 마이그레이션 중에 실행되는 사고가 난다.
