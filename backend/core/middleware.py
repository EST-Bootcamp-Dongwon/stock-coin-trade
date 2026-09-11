"""내부 잡 엔드포인트 접근 통제 (F-20 3.1).

    /internal/ 로 시작하는 모든 요청은 여기를 먼저 지난다.
    `X-Internal-Token` 이 환경변수와 정확히 같지 않으면 **404** 를 준다.

★★ **왜 403 이 아니라 404 인가** ────────────────────────────────────────────

403 은 "여기 뭔가 있는데 너는 못 들어온다" 는 뜻이다. 경로가 실재한다는 사실이
드러나면 공격자는 그 경로에만 집중하면 된다. 404 는 아무것도 알려주지 않는다.

    403 Forbidden  → "/internal/jobs/sync-stock-master 는 있다" (정보 노출)
    404 Not Found  → "그런 건 없다"                              (정보 없음)

v1.0 결함 D-6 의 교훈이다 — AI·RAG 엔드포인트가 **인증 없이** 열려 있었다.
v2.0 은 내부 엔드포인트를 처음부터 잠그고 시작한다.

★ **거부 사유는 응답이 아니라 서버 로그에 적는다.** 응답을 빈 404 로 두면
운영자도 아무 단서가 없어지는데, 그건 규약 8.5("환경 가드는 막다른 길로 만들지
않는다")에 어긋난다. 답은 **사유를 반대편에 적는 것**이다 — 밖에는 404, 안에는
"토큰 헤더가 없었다 / 값이 달랐다 / 서버에 토큰 설정이 아예 없다".

Django 관점 ─────────────────────────────────────────────────────────────

FastAPI 였다면 `Depends(verify_internal_token)` 를 라우터마다 붙였을 것이다.
그 방식의 약점은 **붙이는 것을 잊으면 그냥 뚫린다**는 점이다. 새 잡 엔드포인트를
추가하면서 의존성 한 줄을 빠뜨리면 아무도 모르게 공개된다.

Django 미들웨어는 **경로 접두사로 일괄 검사**한다. `/internal/` 아래에 뭘 추가하든
자동으로 잠긴다 — 잊을 수가 없는 구조다. F-20 3.1 이 "미들웨어에서 일괄 검사"라고
못박은 이유가 이것이다.

미들웨어는 `__init__(get_response)` 로 한 번 만들어지고 요청마다 `__call__` 이
불린다. `__init__` 은 프로세스당 1회라 **설정 읽기·검증을 여기서 끝낸다.**
"""

import hmac
import logging

from django.conf import settings
from django.http import Http404, HttpResponseNotFound
from django.views.defaults import page_not_found

logger = logging.getLogger(__name__)

# 이 접두사로 시작하는 요청은 전부 검사한다. 뒤에 뭐가 붙든 상관없다.
INTERNAL_URL_PREFIX = "/internal/"

# pg_cron 이 `net.http_post(headers := ...)` 로 보내는 헤더 이름 (F-20 3장).
INTERNAL_TOKEN_HEADER = "X-Internal-Token"

# 이보다 짧은 토큰은 기동 시 경고한다. 막지는 않는다 —
# 로컬에서 짧은 값으로 시험해 보는 것까지 막을 이유는 없다.
MIN_TOKEN_LENGTH = 32


class InternalEndpointMiddleware:
    """`/internal/*` 를 토큰으로 잠근다.

    통과하지 못한 요청은 **뷰에 도달하지 않는다.** URL 해석도, 세션 로드도,
    DB 조회도 일어나지 않는다 — 그래서 이 미들웨어를 `MIDDLEWARE` 목록의
    앞쪽(세션·인증보다 위)에 둔다.
    """

    def __init__(self, get_response):
        self.get_response = get_response

        # ★ 매 요청 `os.getenv` 를 하지 않는다. 프로세스가 도는 동안 값은 바뀌지 않고,
        #   요청 경로에서 환경변수를 읽는 것은 그 자체로 낭비다.
        self.expected_token = (getattr(settings, "INTERNAL_JOB_TOKEN", "") or "").strip()
        # 비교는 바이트로 한다 (아래 `_rejection_reason` 의 ★★ 참조). 미리 만들어 둔다.
        self.expected_token_bytes = self.expected_token.encode("utf-8", "surrogateescape")

        if not self.expected_token:
            # 설정이 없으면 **전부 막힌다**(fail closed). 열어두는 쪽으로 실패하지 않는다.
            logger.error(
                "INTERNAL_JOB_TOKEN 이 비어 있습니다 — /internal/* 이 전부 404 로 막힙니다. "
                "backend/.env 에 INTERNAL_JOB_TOKEN 을 설정하십시오 "
                '(생성: python -c "import secrets; print(secrets.token_urlsafe(48))")'
            )
        elif not self.expected_token.isascii():
            # ★ 헤더는 전송 규약상 latin-1 로 오간다. 토큰에 한글 같은 비ASCII 가
            #   섞이면 **보내는 쪽과 받는 쪽의 바이트가 달라져** 정상 요청까지
            #   영구히 404 가 된다. 원인을 짐작하기 매우 어려우므로 기동 때 말한다.
            logger.error(
                "INTERNAL_JOB_TOKEN 에 ASCII 가 아닌 문자가 있습니다 — 정상 요청도 404 가 됩니다. "
                'ASCII 토큰으로 바꾸십시오 (python -c "import secrets; print(secrets.token_urlsafe(48))")'
            )
        elif len(self.expected_token) < MIN_TOKEN_LENGTH:
            logger.warning(
                "INTERNAL_JOB_TOKEN 이 %d자로 짧습니다 (권장 %d자 이상). "
                "운영에 올리기 전에 긴 무작위 값으로 바꾸십시오.",
                len(self.expected_token),
                MIN_TOKEN_LENGTH,
            )

    def __call__(self, request):
        # ★ `request.path` 가 아니라 `request.path_info` 다. 앱을 하위 경로
        #   (예: https://example.com/app/)에 붙여 배포하면 `path` 에는 그 접두사가
        #   남아 있어 `/internal/` 로 시작하지 않는다 — **검사가 통째로 건너뛰어진다.**
        #   `path_info` 는 배포 접두사가 제거된 경로라 어디에 붙이든 같은 값이다.
        if request.path_info.startswith(INTERNAL_URL_PREFIX):
            # ★★ 판정 자체가 터져도 **404 로 끝낸다** (2026-08-16 운영 확인).
            #   여기서 예외가 새어 나가면 Django 가 500 을 주는데, 그 순간
            #   "이 경로는 특별하다"가 드러난다 — 404 로 숨긴 의미가 사라진다.
            #   아래 `_rejection_reason` 은 이미 방어돼 있지만, 판정 로직이
            #   나중에 어떻게 바뀌든 이 불변식이 깨지지 않도록 한 겹 더 감싼다.
            try:
                reason = self._rejection_reason(request)
            except Exception:  # noqa: BLE001 — 사유를 못 가려도 통과시키지 않는다
                logger.exception("내부 엔드포인트 토큰 판정 중 예외 — 요청을 거부합니다")
                reason = "토큰 판정 중 예외"

            if reason:
                logger.warning(
                    "내부 엔드포인트 거부: %s %s — %s (from %s)",
                    request.method,
                    request.path_info,
                    reason,
                    # 프록시 뒤에 있으면 프록시 IP 가 찍힌다. 원본 IP 가 필요하면
                    # 신뢰할 수 있는 프록시에서 X-Forwarded-For 를 세워야 한다.
                    request.META.get("REMOTE_ADDR", "?"),
                )
                return self._not_found(request)

        return self.get_response(request)

    def _not_found(self, request):
        """**없는 경로와 구별되지 않는** 404 를 만든다.

        ★★ **왜 빈 본문으로 두면 안 되는가** (2026-08-16 운영에서 실측) ─────────

        빈 `HttpResponseNotFound()` 를 돌려주면 상태코드는 404 로 같아도 **본문
        길이가 다르다.** 운영에서 재어 보면:

            /internal/jobs/        404 · **0 B**    ← 이 미들웨어
            /없는-경로/            404 · **179 B**  ← Django 기본 404

        상태코드로 숨긴 것을 **본문 크기가 그대로 알려준다.** 공격자는 길이 0 인
        404 만 골라 내면 `/internal/` 아래가 실재한다는 사실을 확인할 수 있다 —
        403 대신 404 를 쓴 이유가 통째로 무너진다.

        답은 **Django 가 만들었을 바로 그 404 를 그대로 돌려주는 것**이다.
        `page_not_found` 는 `handler404` 의 기본 구현이라 본문이 바이트까지 같다.

        ★ 단, `DEBUG=True` 일 때는 예외다. 그때 `page_not_found` 는 **URL 패턴
          목록을 통째로 보여주는** 기술 404 페이지를 낸다 — 내부 경로를 숨기려는
          목적과 정면으로 어긋난다. 개발 중에는 빈 본문으로 둔다. 개발 기계에서
          경로 길이를 재는 공격자는 없고, 운영에서만 성립하면 되는 성질이다.

        ★★ **`templates/404.html` 을 만들 때 주의** — 지금은 그 템플릿이 없어서
          Django 내장 문자열이 쓰이고 **DB 를 건드리지 않는다.** 나중에 404 페이지를
          만들면서 `base.html` 을 확장하면, 그 안의 네비게이션 조회가 **거부된 요청마다**
          돌게 된다. 토큰 없이 `/internal/` 을 두드리는 것만으로 DB 부하를 만들 수 있다.
          그때는 404 템플릿을 DB 없이 그리도록 하거나, 여기서만 내장 404 를 쓰게 한다.
        """
        if settings.DEBUG:
            return HttpResponseNotFound()

        response = page_not_found(request, Http404())

        # ★★ **아래쪽 미들웨어가 붙였을 헤더를 손으로 채운다** ─────────────────
        #
        #   조기 반환이라 이 응답은 `MIDDLEWARE` 목록에서 **우리 아래 있는 것들을
        #   거치지 않는다.** 그중 응답 헤더를 붙이는 것이 `XFrameOptionsMiddleware`
        #   이고, 그래서 운영에서 재어 보면 이렇게 갈린다:
        #
        #       /internal/jobs/   404 · x-frame-options **없음**   ← 우리
        #       /없는-경로/        404 · x-frame-options: DENY
        #
        #   본문을 똑같이 맞춰 놓고 헤더로 들키면 아무 소용이 없다.
        #
        #   ★ **그럼 미들웨어를 맨 아래로 내리면 되지 않나?** 안 된다. 그러면
        #     `CommonMiddleware` 의 APPEND_SLASH 가 404 응답을 보고 "슬래시를 붙이면
        #     풀리는 주소인가" 를 확인해 **301 로 바꿔 버린다.** `/internal/jobs` 는
        #     실제로 풀리는 주소라 301 이 나가고, 404 와 301 이 갈리는 **새 오라클**이
        #     생긴다. 위치는 그대로 두고 헤더만 맞추는 편이 안전하다.
        if not response.has_header("X-Frame-Options"):
            # Django 의 `XFrameOptionsMiddleware` 와 같은 근거를 쓴다.
            response["X-Frame-Options"] = getattr(settings, "X_FRAME_OPTIONS", "DENY").upper()

        return response

    def _rejection_reason(self, request) -> str:
        """거부 사유. 통과면 빈 문자열.

        사유 문자열은 **로그에만** 쓴다. 응답에 실으면 404 로 숨긴 의미가 없다.
        """
        if not self.expected_token:
            return "서버에 INTERNAL_JOB_TOKEN 이 설정되지 않음"

        # Django 관점 — `request.headers` 는 대소문자를 가리지 않는 매핑이다.
        # `request.META["HTTP_X_INTERNAL_TOKEN"]` 로 읽던 옛 방식보다 오타가 적다.
        provided = request.headers.get(INTERNAL_TOKEN_HEADER)
        if not provided:
            return f"{INTERNAL_TOKEN_HEADER} 헤더 없음"

        # ★ `==` 가 아니라 `hmac.compare_digest` 다. 문자열 비교는 다른 글자가
        #   나오는 순간 멈추므로, 응답 시간 차이로 토큰을 앞에서부터 한 글자씩
        #   맞춰갈 수 있다(타이밍 공격). `compare_digest` 는 길이가 같으면 항상
        #   같은 시간이 걸린다.
        #
        # ★★ **바이트로 비교한다** (2026-08-16 운영에서 500 을 실측하고 고침) ──────
        #
        #   `compare_digest` 에 str 을 넘기면 **양쪽 다 ASCII 여야 한다.** 아니면
        #   비교에 실패하는 게 아니라 `TypeError` 를 던진다:
        #
        #       TypeError: comparing strings with non-ASCII characters is not supported
        #
        #   헤더는 바깥에서 오는 값이라 한글이든 뭐든 들어올 수 있다. 그대로 두면
        #   예외가 미들웨어를 뚫고 나가 **500** 이 된다 — 실제로 운영에서 이랬다:
        #
        #       X-Internal-Token: 한글토큰값   → **500** (본문 145 B)
        #       X-Internal-Token: plainwrong   → 404
        #
        #   틀린 토큰 하나로 404 와 500 이 갈리므로, 이것만으로 **경로가 실재한다는
        #   사실이 드러난다.** 404 로 숨긴 설계를 비ASCII 한 글자가 무너뜨린 셈이다.
        #
        #   `encode()` 해서 바이트끼리 비교하면 그런 제약이 없다. 기대 토큰은
        #   `token_urlsafe` 라 항상 ASCII 이므로, 비ASCII 입력은 **조용히 불일치**로
        #   떨어진다 — 우리가 원한 동작 그대로다.
        provided_bytes = provided.strip().encode("utf-8", "surrogateescape")
        if not hmac.compare_digest(provided_bytes, self.expected_token_bytes):
            return f"{INTERNAL_TOKEN_HEADER} 불일치"

        return ""
