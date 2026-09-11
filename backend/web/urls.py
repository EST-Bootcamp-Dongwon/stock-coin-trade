"""홈 · 공통 프래그먼트 URL.

★ **`app_name` 을 두지 않는다.** 홈의 URL name 은 라우팅 문서 2.1 이 정한 대로
  네임스페이스 없는 `home` 이어야 한다 (`{% url 'home' %}`). 홈은 서비스에 하나뿐이라
  이름이 겹칠 일이 없고, `settings.LOGIN_REDIRECT_URL` 같은 곳에서도 짧게 쓰인다.

  프래그먼트는 반대로 앞으로 계속 늘어나므로 이름에 `frag_` 접두를 붙여 구분한다
  (규약 2.1 의 명명 규칙: URL 은 `/fragments/` 하위, name 은 `frag_*`).
"""

from django.urls import path

from web import views

urlpatterns = [
    path("", views.home, name="home"),
    path("fragments/market/", views.fragment_market, name="frag_market"),
    path("fragments/my-contests/", views.fragment_my_contests, name="frag_my_contests"),
    path("fragments/my-accounts/", views.fragment_my_accounts, name="frag_my_accounts"),
]
