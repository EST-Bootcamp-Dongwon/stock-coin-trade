"""대회 URL — `/contests/*` (→ [ui/00-화면목록-라우팅] 2.1 의 #2 · #3 · #4 · #8 · #13).

★★ **URL name 을 `web/navigation.py` 와 맞춰야 메뉴가 살아난다** ──────────────

GNB 는 `reverse("contests:list")` 가 성공하는지로 '준비 중' 여부를 판단한다
(`navigation.NavItem.resolve`). 이름을 다르게 지으면 화면을 다 만들어도 메뉴는
계속 회색으로 남는다. `app_name = "contests"` + `name="list"` 가 그 약속이다.

★ **인자가 필요한 화면은 GNB 에 넣지 않는다.** `resolve()` 는 `reverse()` 를
  **인자 없이** 부르므로 `contests:detail` 은 등록해도 영원히 `NoReverseMatch` 다.
  메뉴에는 목록만 올린다 (navigation.py 는 이미 그렇게 돼 있다).

★ 프래그먼트는 `frag_` 접두를 붙인다 (규약 2.1 의 명명 규칙). 경로도 `/fragments/`
  아래 둔다 — 다만 web 앱과 달리 **대회별**이라 slug 뒤에 붙는다.
"""

from django.urls import path

from contests import views

app_name = "contests"

urlpatterns = [
    path("", views.contest_list, name="list"),
    # ★ 구체적인 경로를 slug 보다 **위**에 둘 필요는 없다. `<slug:slug>/` 는
    #   빈 경로가 아니고 뒤에 `ranking/` 같은 꼬리가 붙은 패턴들과 겹치지 않는다.
    #   (`<slug:slug>` 는 `/` 를 포함하지 않기 때문이다.)
    path("<slug:slug>/", views.contest_detail, name="detail"),
    path("<slug:slug>/ranking/", views.contest_ranking, name="ranking"),
    path("<slug:slug>/join/", views.contest_join, name="join"),
    path(
        "<slug:slug>/participants/<int:participation_id>/",
        views.participant_detail,
        name="participant",
    ),
    path(
        "<slug:slug>/fragments/ranking/",
        views.fragment_ranking,
        name="frag_ranking",
    ),
]
