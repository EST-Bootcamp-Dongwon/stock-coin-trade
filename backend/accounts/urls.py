"""계정 URL — `/account/*` (→ [ui/00-화면목록-라우팅] 2.1 · 2.6).

★ `app_name` 이 네임스페이스를 만든다. 템플릿에서는 `{% url 'account:login' %}`
  처럼 쓴다. 앱마다 `detail` · `list` 같은 흔한 이름이 겹쳐도 충돌하지 않는다.

Django 관점 — FastAPI 는 `@app.get("/account/login")` 처럼 경로를 함수 옆에 적었다.
Django 는 **경로와 뷰를 분리**해 여기 모은다. 처음엔 번거로워 보이지만,
"이 서비스에 어떤 화면이 있는가" 가 파일 하나로 보이고, 템플릿이 경로를 이름으로
참조하므로 **주소를 바꿔도 템플릿을 고칠 필요가 없다** (라우팅 문서 1.3).

프로필·API 키(2.6 의 #28·#29)는 아직 없고, GNB 는 그 사실을 '준비 중' 으로
표시한다 (web/navigation.py 참조).
"""

from django.urls import path

from accounts import views

app_name = "account"

urlpatterns = [
    path("register/", views.register, name="register"),
    path("login/", views.login, name="login"),
    path("logout/", views.logout, name="logout"),
    path("contests/", views.contests, name="contests"),
]
