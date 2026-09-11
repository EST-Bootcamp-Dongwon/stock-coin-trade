"""HTMX 응답 헬퍼 (01-HTMX 부분갱신 규약 4장 · 7장).

HTMX 는 **응답 헤더로 브라우저에게 지시를 내린다.** 이 파일은 그 헤더들을
문자열로 흩뿌리지 않기 위한 얇은 층이다.

    HX-Trigger    응답이 도착하면 이 이벤트를 발생시켜라
    HX-Redirect   (본문을 갈아끼우지 말고) 이 주소로 페이지를 이동해라
    HX-Request    (요청 헤더) 이 요청은 HTMX 가 보낸 것이다

★ `core` 에 두는 이유 — 어느 도메인도 참조하지 않고 Django 만 안다. 규약 1.1 의
  의존 방향을 지키면서 모든 앱이 가져다 쓸 수 있는 자리다.

Django 관점 ─────────────────────────────────────────────────────────────────

Next.js 에서는 `router.push()` 로 클라이언트가 이동을 결정했다. HTMX 는 **서버가
결정**한다. "폼이 성공했으니 홈으로 보내라" 를 서버가 헤더 한 줄로 말한다.
클라이언트 자바스크립트를 우리가 한 줄도 안 써도 되는 이유가 여기에 있다.
"""

import json

from django.http import HttpResponse

# 토스트 레벨 — 템플릿·JS 가 색을 고르는 데 쓴다 (U-01 4장)
TOAST_SUCCESS = "success"
TOAST_ERROR = "error"
TOAST_WARN = "warn"
TOAST_INFO = "info"


def is_htmx(request) -> bool:
    """HTMX 가 보낸 요청인가.

    HTMX 는 모든 요청에 `HX-Request: true` 를 붙인다. 이 값으로 **같은 뷰가
    전체 페이지와 조각을 모두 응답**할 수 있다 — 뷰를 두 벌 만들지 않아도 된다.

    ★ 주소창에 직접 URL 을 쳐도 화면이 나와야 한다. 프래그먼트 전용 뷰가 아니라면
      HTMX 가 아닐 때의 응답(전체 페이지)을 항상 함께 만든다.
    """
    return request.headers.get("HX-Request") == "true"


def add_trigger(response: HttpResponse, events: dict, *, header: str = "HX-Trigger") -> HttpResponse:
    """응답에 클라이언트 이벤트를 실어 보낸다 (규약 4.1).

    Args:
        events: `{"order:filled": {"symbol": "005930"}, …}`.
        header: `HX-Trigger`(스왑 전) · `HX-Trigger-After-Swap` · `HX-Trigger-After-Settle`.

    ★ **이미 붙어 있는 이벤트를 덮어쓰지 않고 합친다.** 뷰가 토스트를 띄우고
      서비스 계층이 `order:filled` 를 얹는 식으로 두 번 호출되는 일이 흔하다.
      단순 대입으로 짜면 나중에 부른 쪽이 앞의 것을 조용히 지운다.
    """
    merged = json.loads(response.headers[header]) if header in response.headers else {}
    merged.update(events)
    # `ensure_ascii=False` 를 쓰지 않는다 — 헤더 값은 ASCII 만 안전하다.
    # 한글 메시지는 `가` 형태로 이스케이프되고, 브라우저의 JSON.parse 가 되돌린다.
    response.headers[header] = json.dumps(merged)
    return response


def toast(response: HttpResponse, level: str, message: str) -> HttpResponse:
    """공통 토스트 한 줄 (규약 4.1 의 `toast` 이벤트)."""
    return add_trigger(response, {"toast": {"level": level, "message": message}})


def hx_redirect(url: str, *, toast_message: str = "", toast_level: str = TOAST_SUCCESS):
    """HTMX 요청에 "이 주소로 이동해라" 를 응답한다.

    ★★ **왜 `redirect()` 를 쓰면 안 되는가** ────────────────────────────────

    Django 의 `redirect()` 는 302 를 준다. 그런데 HTMX 요청에 302 가 오면
    **브라우저(fetch)가 알아서 따라가** 목적지 HTML 을 받아오고, HTMX 는 그
    전체 문서를 원래 갈아끼우려던 작은 `<div>` 안에 쑤셔 넣는다.
    화면 안에 화면이 겹쳐 그려지는, 원인을 찾기 어려운 증상이 된다.

    `HX-Redirect` 는 200 응답에 헤더만 실어 보내고, HTMX 가 그것을 보고
    `window.location` 을 바꾼다. **폼 성공 후 페이지 이동은 항상 이쪽이다.**
    """
    response = HttpResponse(status=204)     # 본문 없음 — 갈아끼울 것이 없다
    response.headers["HX-Redirect"] = url
    if toast_message:
        toast(response, toast_level, toast_message)
    return response
