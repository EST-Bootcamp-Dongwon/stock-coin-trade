"""홈 화면과 공통 프래그먼트 (F-21 · 01-HTMX 규약 2장).

    GET  /                        홈
    GET  /fragments/market/       시장 현황 (폴링 30초)
    GET  /fragments/my-contests/  내 대회 현황 (폴링 60초)
    GET  /fragments/my-accounts/  내 계좌 요약 (이벤트 갱신)

★★ **왜 프래그먼트를 별도 URL 로 두는가** ─────────────────────────────────

규약 2장의 3계층(페이지 → 프래그먼트 → 셀) 중 가운데 층이다. 프래그먼트가
**자기 URL 을 가지면** 두 가지가 된다.

    ① 폴링    `hx-get="{% url 'web:frag_market' %}" hx-trigger="every 30s"`
    ② 이벤트   주문이 체결되면 그 프래그먼트만 다시 당겨온다

페이지 전체를 30초마다 새로 그리는 것과 비교하면, 시장 위젯 하나가 느려도
나머지 화면은 멀쩡하고(F-21 4장) 전송량도 수십 분의 1이다.

★ 프래그먼트 뷰는 **`_`로 시작하는 템플릿 하나만** 렌더한다. 페이지 껍데기
  (`base.html`)를 상속하지 않는다 — 상속하면 `<html>` 통째로 응답해서
  화면 속에 화면이 겹친다.
"""

from django.shortcuts import render

from web import services


def home(request):
    """홈 (F-21).

    ★ **비로그인도 볼 수 있다** (라우팅 문서 5장). 이 서비스가 무엇을 하는 곳인지
      가입 전에 알 수 있어야 한다. 로그인 상태에 따라 위젯 구성이 바뀔 뿐이다.
    """
    context = services.home_context(request.user)
    # AI 분석 패널이 "지금 어느 화면인가" 를 서버에 알릴 때 쓴다 (U-01 3.3).
    # v1.0 은 이걸 DOM 스크래핑으로 알아냈고, 화면 구조가 바뀔 때마다 깨졌다.
    context["context_type"] = "home"
    return render(request, "web/home.html", context)


# ─────────────────────────────────────────────────────────────────
# 프래그먼트
# ─────────────────────────────────────────────────────────────────


def fragment_market(request):
    """시장 현황 — 폴링 30초 (규약 5.1: 지수는 30~60초).

    ★ 로그인을 요구하지 않는다. 비로그인 홈에도 이 위젯이 있다.
    """
    return render(request, "web/_market.html", {"market": services.market_overview()})


def fragment_my_contests(request):
    """내 대회 현황 — 폴링 60초 (규약 5.1: 랭킹은 스냅샷이 10분 단위).

    ★ 비로그인 요청에 401 을 주지 않고 **빈 조각**을 준다. 이 프래그먼트는
      로그인 사용자에게만 렌더되지만, 세션이 만료된 채 폴링이 계속될 수 있다.
      그때 에러 조각이 화면에 박히는 것보다 조용히 비는 편이 낫다 —
      다음 사용자 조작에서 `@login_required` 가 로그인 화면으로 보낸다.
    """
    if not request.user.is_authenticated:
        return render(request, "web/_my_contests.html", {"my_contests": []})
    return render(
        request,
        "web/_my_contests.html",
        {"my_contests": services.my_contest_cards(request.user)},
    )


def fragment_my_accounts(request):
    """내 계좌 요약 — 주문 체결·계좌 초기화 이벤트에 반응해 갱신 (규약 4.1)."""
    if not request.user.is_authenticated:
        return render(request, "web/_my_accounts.html", {"account_cards": []})
    return render(
        request,
        "web/_my_accounts.html",
        {"account_cards": services.account_cards(request.user)},
    )
