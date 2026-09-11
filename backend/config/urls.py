"""루트 URL 설정.

    /            사용자 화면 — 홈 · 공통 프래그먼트 (web 앱)
    /account/    계정 — 로그인 · 회원가입 · 로그아웃 (F-01)
    /admin/      운영 화면 (F-19)
    /internal/   pg_cron 이 부르는 잡 엔드포인트 (F-20 3장) — **토큰 없으면 404**

★ `/internal/` 은 `InternalEndpointMiddleware` 가 접두사로 일괄 검사한다.
  여기에 경로를 더 추가해도 자동으로 잠긴다 (config/internal_urls.py 참조).

★★ **`web.urls` 를 맨 아래 두는 이유** ─────────────────────────────────────

Django 는 `urlpatterns` 를 **위에서부터 차례로** 맞춰 보고 처음 걸리는 것을 쓴다.
`web.urls` 는 `""`(빈 경로)에서 시작하므로 위에 두면 문제가 될 여지가 있고,
무엇보다 "구체적인 접두사를 먼저, 포괄적인 것을 나중에" 가 읽기 쉽다.

Django 관점 — FastAPI 도 라우트 등록 순서가 우선순위였지만, 경로가 데코레이터에
흩어져 있어 순서를 한눈에 볼 수 없었다. Django 는 이 목록 하나가 전부다.

아직 붙지 않은 화면 (→ [ui/00-화면목록-라우팅] 2장의 30종 중 나머지):
`/practice/` · `/portfolio/` · `/orders/` · `/learn/` · `/tools/` · `/api/docs/`.
GNB 는 이들을 '준비 중' 으로 표시한다 (web/navigation.py 참조).
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("internal/", include("config.internal_urls")),
    path("account/", include("accounts.urls")),
    path("contests/", include("contests.urls")),
    path("", include("web.urls")),
]

if settings.DEBUG:
    # 개발 중에만 Django 가 미디어 파일(Member.avatar)을 서빙한다.
    # 운영에서는 Nginx·오브젝트 스토리지가 맡는다.
    #
    # ★ 정적 파일(CSS·JS)은 여기 없다 — WhiteNoise 미들웨어가 DEBUG 와 무관하게
    #   항상 서빙한다 (settings.py 의 WHITENOISE_USE_FINDERS 주석 참조).
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
