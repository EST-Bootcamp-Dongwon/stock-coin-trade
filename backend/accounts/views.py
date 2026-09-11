"""계정 화면 — 회원가입 · 로그인 · 로그아웃 (F-01).

화면 3개 (→ [ui/00-화면목록-라우팅] 2.1 의 #5 · #6):

    GET/POST  /account/register/   회원가입
    GET/POST  /account/login/      로그인
    POST      /account/logout/     로그아웃

★★ **폼은 HTMX 로도, 평범한 폼 전송으로도 동작한다** ────────────────────────

템플릿은 `hx-post` 로 보내지만, 자바스크립트가 꺼져 있거나 HTMX 로딩이 실패해도
`<form method="post" action="…">` 가 그대로 살아 있어 **평범한 전송으로 되돌아간다**
(점진적 향상, progressive enhancement).

뷰 쪽에서 이걸 지탱하는 것은 아래 두 줄짜리 분기다.

    실패: HTMX 면 폼 조각만 → 화면 깜빡임 없이 에러가 뜬다
          아니면 페이지 전체를 다시 그린다
    성공: HTMX 면 `HX-Redirect` 헤더 → 브라우저가 이동
          아니면 302 리다이렉트

**로그인 화면 하나를 위해 뷰를 두 벌 만들지 않는다.**
"""

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from accounts.forms import LoginForm, RegisterForm
from core.htmx import hx_redirect, is_htmx


def _safe_next(request, fallback: str) -> str:
    """`?next=` 로 받은 이동 주소를 검증한다.

    ★★ **검증 없이 쓰면 오픈 리다이렉트 취약점이다.** 공격자가
      `/account/login/?next=https://악성사이트/` 링크를 뿌리면, 사용자는 우리
      도메인에서 정상적으로 로그인한 뒤 남의 사이트로 튕겨 간다. 방금 로그인을
      했으므로 그 사이트의 가짜 로그인 폼을 의심 없이 다시 채우게 된다.

    `url_has_allowed_host_and_scheme()` 이 **우리 호스트인지**를 본다. Django 의
    `LoginView` 도 내부에서 같은 함수를 쓴다.
    """
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        url=target,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return target
    return fallback


def _leave(request, message: str):
    """로그인·가입·로그아웃이 끝난 뒤 화면을 옮긴다.

    ★★ **알림은 `HX-Trigger` 토스트가 아니라 Django 메시지로 남긴다** ──────────

    두 경로 모두 **페이지를 통째로 이동**하기 때문이다. `HX-Redirect` 를 받은
    HTMX 는 `window.location` 을 바꾸고, 그러면 응답 헤더에 실었던 토스트 이벤트는
    **그 응답과 함께 버려진다.** 이동한 다음 페이지에서는 아무 일도 일어나지 않는다.

    `messages` 는 세션에 담겼다가 **다음 렌더 때 한 번 소비된다.** 그래서 이동
    후에도 살아남는다. base.html 이 이것을 토스트로 그린다.

    `core.htmx.toast()` 는 **이동하지 않는** 갱신(주문 체결 등)에 쓴다 — 화면이
    그대로 있으니 이벤트가 도착할 곳이 있다.

    Django 관점 — Next.js 에서 `router.push()` 후 토스트를 띄우려면 상태를 쿼리
    파라미터나 전역 스토어에 실어 날라야 했다. Django 의 messages 프레임워크가
    그 "한 번만 소비되는 세션 저장소" 를 프레임워크 차원에서 제공한다.
    """
    target = _safe_next(request, reverse("home"))
    messages.success(request, message)
    if is_htmx(request):
        return hx_redirect(target)
    return redirect(target)


def _form_response(request, template: str, fragment: str, context: dict, *, status: int = 200):
    """검증 실패 응답 — HTMX 면 폼 조각만, 아니면 페이지 전체.

    `status=422`(Unprocessable Content) 를 쓰지 않고 200 을 준다. HTMX 는 기본적으로
    **4xx 응답의 본문을 화면에 넣지 않기** 때문이다. 에러 메시지가 담긴 폼을
    보여줘야 하므로 200 으로 보낸다 — 화면을 갈아끼우라는 뜻이지 "성공" 이라는
    뜻이 아니다.
    """
    name = fragment if is_htmx(request) else template
    return render(request, name, context, status=status)


@require_http_methods(["GET", "POST"])
def register(request):
    """회원가입 (F-01 3장).

    가입이 끝나면 **바로 로그인 상태로 만든다** (v1.0 동작 승계). 방금 비밀번호를
    입력한 사람에게 다시 입력을 요구할 이유가 없다.
    """
    # 이미 로그인한 사람이 가입 화면에 오면 홈으로 돌린다.
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            # 여기서 회원 1행 + 연습 계좌 3행이 한 트랜잭션으로 만들어진다
            # (accounts/forms.py 의 RegisterForm.save 주석 참조).
            member = form.save()

            # ★ `login()` 은 세션에 사용자 id 를 심고 **세션 키를 새로 발급**한다.
            #   세션 고정(session fixation) 공격 방어라 직접 세션을 만지면 안 된다.
            auth_login(request, member)

            return _leave(
                request,
                f"{member.display_name or member.username}님, 가입이 완료되었습니다. "
                "연습 계좌 3개(주식·코인·대체자산)가 각 1억원으로 준비됐습니다.",
            )
    else:
        form = RegisterForm()

    context = {"form": form, "next": request.GET.get("next", "")}
    return _form_response(request, "accounts/register.html", "accounts/_register_form.html", context)


@require_http_methods(["GET", "POST"])
def login(request):
    """로그인.

    ★ Django 가 제공하는 `LoginView`(클래스형 뷰)를 쓰지 않고 함수로 적었다.
      `LoginView` 는 편하지만 **무슨 일이 일어나는지가 상속 구조 안에 숨는다.**
      인증은 이 프로젝트에서 몇 안 되는 "직접 읽을 수 있어야 하는" 흐름이라
      다섯 줄을 눈에 보이게 두는 편을 골랐다. 검증·비밀번호 대조·비활성 계정
      차단은 여전히 `AuthenticationForm` 이 한다 (accounts/forms.py 참조).
    """
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        # `AuthenticationForm` 은 첫 인자로 request 를 받는다 — 일반 폼과 다르다.
        # 이 request 로 인증 백엔드가 로그인 시도를 기록할 수 있다.
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            # 검증 과정에서 이미 `authenticate()` 가 끝났고, 그 결과가 여기 담겨 있다.
            member = form.get_user()
            auth_login(request, member)

            return _leave(request, f"{member.display_name or member.username}님, 환영합니다.")
    else:
        form = LoginForm(request)

    context = {"form": form, "next": request.GET.get("next", "")}
    return _form_response(request, "accounts/login.html", "accounts/_login_form.html", context)


# ★★ **로그아웃은 POST 다. GET 이면 안 된다.** ────────────────────────────────
#
# GET 로그아웃은 링크·이미지 태그만으로 남을 로그아웃시킬 수 있다(CSRF).
# 브라우저 확장이나 프리페치가 링크를 미리 긁어 **저절로 로그아웃되는** 일도 생긴다.
# Django 5 부터는 `LogoutView` 도 GET 을 거부한다.
@require_http_methods(["POST"])
@login_required
def logout(request):
    """로그아웃 — 세션을 끊는다.

    ★ `logout()` 은 세션을 통째로 비운다. 그 뒤에 `messages.success()` 를 부르는
      순서가 중요하다 — 반대로 하면 방금 담은 메시지가 함께 지워진다.
    """
    auth_logout(request)
    messages.success(request, "로그아웃되었습니다.")
    home = reverse("home")
    if is_htmx(request):
        return hx_redirect(home)
    return redirect(home)


@login_required
def contests(request):
    """내 대회 — 참가 이력 (U-04 8장 · 화면 목록 #30).

    ★★ **왜 `contests` 앱이 아니라 여기 있는가** — 이 화면의 주어는 대회가 아니라
      **나**다. 경로도 `/account/contests/` 이고 GNB 의 계정 메뉴에 들어간다.
      대회 하나를 보는 화면(`/contests/<slug>/`)과 섞으면, 로그인하지 않은
      사람에게도 열려야 하는 화면과 그렇지 않은 화면이 같은 앱에 섞인다.

    조립은 `contests.services` 가 한다 — 대회 도메인의 데이터를 읽는 규칙(순위를
    조회 시점에 계산하지 않는다 등)은 그쪽에 모여 있어야 한다.
    """
    from contests.services import my_contest_rows      # noqa: PLC0415

    return render(
        request,
        "accounts/contests.html",
        {"context_type": "my_contests", "rows": my_contest_rows(request.user)},
    )
