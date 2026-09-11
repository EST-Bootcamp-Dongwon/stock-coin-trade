"""한국투자증권 KIS Developers 클라이언트 (F-16 3장 · api_발급_가이드 2장).

**v2.0 이 실제로 의존하는 유일한 증권사 API 다.** 대회 모드의 호가 10단계가 여기서 온다.
이 모듈이 하는 일은 셋뿐이다.

    ① 접근토큰을 받아 `ExternalToken` 에 캐시한다   (24시간 · 재발급에도 제한이 있다)
    ② 호가 10단계를 조회한다                        → 잡 2 가 `OrderbookCache` 에 넣는다
    ③ 현재가를 조회한다                             → 잡 1 이 `QuoteCache` 에 넣는다

**여기서 DB 캐시를 쓰지 않는다.** 이 모듈은 "KIS 에서 받아온 값"만 돌려주고,
어디에 어떻게 저장할지는 `market/jobs.py` 가 정한다. 조회와 적재를 나눠 두면
테스트에서 **HTTP 만 목으로 갈아끼우고 적재 로직은 진짜로** 돌릴 수 있다.

★★ **자격증명은 절대 밖으로 나가지 않는다** ─────────────────────────────────

강사님 원본 `broker_test.py` 의 첫 원칙을 그대로 승계한다::

    class BrokerApiError(RuntimeError):
        \"\"\"A user-safe error that never includes credentials or access tokens.\"\"\"

이 모듈이 던지는 `ExternalDataError` 에는 **앱키·시크릿·토큰이 한 글자도 담기지
않는다.** 예외 메시지는 화면과 `DataSyncLog.error` 를 거쳐 Admin 에까지 흘러가고,
`DataSyncLog` 는 운영자 계정이면 누구나 읽을 수 있기 때문이다.
그래서 `response.text` 를 통째로 붙이는 짓도 하지 않는다 — 요청을 되비추는
API 가 있으면 그 안에 앱키가 섞여 돌아온다.

★★ **KIS 는 HTTP 200 으로 실패를 알린다** ────────────────────────────────────

이게 이 API 를 다룰 때 가장 틀리기 쉬운 지점이다::

    HTTP 200 OK
    {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}

`raise_for_status()` 만 믿으면 **유량 초과가 성공으로 통과**하고, 빈 `output` 을
파싱해 호가 0 원짜리 캐시가 써진다. 그 값으로 대회 체결이 일어난다.
그래서 `rt_cd` 를 반드시 본다 (`_check_rt_cd`).

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 였다면 `httpx.AsyncClient` 를 앱 수명에 묶어 두고 의존성 주입으로 넘겼을
것이다. 여기서는 **서버리스라 프로세스 수명을 믿을 수 없어서** 커넥션 풀도
토큰도 프로세스 밖(DB)에 둔다. 모듈 전역에 클라이언트 객체를 만들지 않는 이유다
(규약 10장 — "요청을 넘어서 사는 프로세스 메모리 캐시는 만들지 않는다").
"""

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from core.constants import BrokerProvider
from core.jobs import ExternalDataError, retry
from market.models import ExternalToken
from market.services import spend_api_budget

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# 0. 환경 · 상수
# ─────────────────────────────────────────────────────────────────

# 도메인 (api_발급_가이드 2장). **포트까지가 주소의 일부다** — 빼면 붙지 않는다.
KIS_HOSTS = {
    "MOCK": "https://openapivts.koreainvestment.com:29443",
    "REAL": "https://openapi.koreainvestment.com:9443",
}

# 거래 ID. 시세 조회는 모의·실전이 **같은 값**을 쓴다.
# (모의투자에서 `VTTC` 계열로 갈리는 것은 주문·잔고 쪽이다 — 우리는 주문을 내지 않는다)
TR_ORDERBOOK = "FHKST01010200"      # 주식현재가 호가/예상체결
TR_QUOTE = "FHKST01010100"          # 주식현재가 시세

PATH_TOKEN = "/oauth2/tokenP"
PATH_ORDERBOOK = "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
PATH_QUOTE = "/uapi/domestic-stock/v1/quotations/inquire-price"

# 시장 구분. J = 주식/ETF/ETN (KRX). 우리는 국내 주식만 다룬다.
MARKET_DIV_CODE = "J"

HTTP_TIMEOUT_SECONDS = 8

# 호가 단계 수. KIS 응답이 `askp1` ~ `askp10` 으로 **컬럼으로 펼쳐져** 온다.
ORDERBOOK_DEPTH = 10

# ★★ **유량 한도 — 자료가 엇갈린다** ─────────────────────────────────────────
#
#   F-16 2.4 와 발급 가이드는 "모의 초당 5건 · 실전 20건" 으로 적었는데,
#   2026-08-13 재확인 결과 공개 자료가 **초당 5건 · 2건 · 1건**으로 갈린다.
#   실계정이 없어 실측할 수 없는 상태다(→ 변경노트 E-43).
#
#   틀리는 방향을 고른다 — **낮게 잡으면 폴링이 느려질 뿐**이고, 높게 잡으면
#   EGW00201 이 연발해 대회 체결이 멈춘다. 그래서 기본값을 보수적으로 두고
#   `.env` 에서 올릴 수 있게 한다. 키가 생겨 실측하면 그 값을 넣으면 된다.
DEFAULT_RATE_LIMIT = {"MOCK": 2, "REAL": 15}

# 토큰을 만료 **얼마 전에** 갈아치울 것인가.
#
# ★ 넉넉히 잡는 이유 — 만료 직전 토큰으로 호출하면 요청이 날아가는 사이에 만료돼
#   `EGW00123`(유효하지 않은 토큰)이 난다. 재발급은 1분에 한 번뿐이라(EGW00133)
#   그 순간 복구가 1분 뒤로 밀린다. 30분이면 폴링 잡이 여러 번 도는 동안 여유가 있다.
TOKEN_RENEW_MARGIN = timedelta(minutes=30)

# 토큰 발급 실패 후 **다시 시도하기까지** 최소로 기다리는 시간.
#
# ★★ KIS 는 토큰 발급을 **1분에 1회**로 제한한다(EGW00133). 5초 잡이 실패할 때마다
#    재발급을 시도하면 12번 두드리고 전부 막힌다. 실패를 기억해 두고 참는다.
TOKEN_RETRY_COOLDOWN = timedelta(seconds=70)


class KisNotConfigured(ExternalDataError):
    """앱키·시크릿이 없다. **장애가 아니라 미설정이다.**

    ★ 타입을 나누는 이유 — 폴링 잡은 "KIS 가 죽었다"와 "아직 안 붙였다"를
      다르게 다뤄야 한다. 전자는 재시도·경고 대상이지만, 후자는 매 5초마다
      경고를 띄울 일이 아니라 **한 번 알리고 조용히 네이버로 넘어가면 된다.**
      `ExternalDataError` 를 상속하므로 `--soft-fail` · 503 처리는 그대로 받는다.
    """


@dataclass(frozen=True)
class KisConfig:
    """이번 호출에 쓸 KIS 설정. `.env` 에서 읽어 만든다."""

    app_key: str
    app_secret: str
    environment: str            # "MOCK" | "REAL" — `ExternalToken.Environment` 와 같은 값
    host: str
    rate_limit: int             # 초당 허용 호출 수

    @property
    def budget_name(self) -> str:
        """유량 예산 카운터 이름.

        ★ 모의·실전을 **다른 카운터**로 센다. 한도가 다르므로 한 카운터에 섞으면
          어느 쪽 기준으로 판정할지 정할 수 없다. 실제로 두 환경을 동시에 쓸 일은
          없지만, 섞이지 않게 해 두는 편이 사고를 미리 없앤다.
        """
        return "KIS" if self.environment == "MOCK" else "KIS_REAL"


def load_config() -> KisConfig:
    """`.env` 에서 KIS 설정을 읽는다.

    Raises:
        KisNotConfigured: 앱키·시크릿이 비어 있는 경우. **무엇을 해야 하는지까지**
            메시지에 담는다 (규약 8.5 — "환경 가드는 막다른 길로 만들지 않는다").
    """
    app_key = (os.getenv("KIS_APP_KEY") or "").strip()
    app_secret = (os.getenv("KIS_APP_SECRET") or "").strip()
    mode = (os.getenv("KIS_MODE") or "mock").strip().lower()
    environment = ExternalToken.Environment.REAL if mode == "real" else ExternalToken.Environment.MOCK

    if not (app_key and app_secret):
        raise KisNotConfigured(
            "KIS 자격증명이 없습니다 (KIS_APP_KEY · KIS_APP_SECRET).",
            hint=(
                "대회 모드의 실호가가 이 키에서 옵니다. 없으면 호가를 채울 수 없고,\n"
                "    대회 주문은 체결되지 않고 접수 상태로 쌓입니다(F-16 3.4 — 거부하지는 않습니다).\n"
                "    ① https://apiportal.koreainvestment.com 에서 API 신청 → 앱키·시크릿 발급\n"
                "    ② backend/.env 에 KIS_MODE · KIS_APP_KEY · KIS_APP_SECRET 을 넣으십시오.\n"
                "    ③ 발급 절차 전체는 docs/v2-design/api/version2.0/api_발급_가이드.md 2장\n"
                "    ④ 연결 확인은 `python manage.py kis_probe` 한 줄이면 됩니다."
            ),
        )

    try:
        rate_limit = int(os.getenv("KIS_RATE_LIMIT") or DEFAULT_RATE_LIMIT[environment])
    except ValueError:
        rate_limit = DEFAULT_RATE_LIMIT[environment]

    return KisConfig(
        app_key=app_key,
        app_secret=app_secret,
        environment=environment,
        host=KIS_HOSTS[environment],
        rate_limit=max(rate_limit, 1),
    )


def is_configured() -> bool:
    """자격증명이 있는가. **예외를 던지지 않고** 묻고 싶을 때 쓴다.

    폴링 잡이 매 회차 `load_config()` 를 try/except 로 감싸는 대신 이걸 먼저 본다.
    """
    return bool((os.getenv("KIS_APP_KEY") or "").strip() and (os.getenv("KIS_APP_SECRET") or "").strip())


# ─────────────────────────────────────────────────────────────────
# 1. 접근토큰
# ─────────────────────────────────────────────────────────────────


def _requests():
    """`requests` 를 지금 불러온다 (market/services.py 의 `_fetch_upbit_markets` 와 같은 이유)."""
    try:
        import requests        # noqa: PLC0415 — 지연 import 가 의도다
    except ImportError as exc:
        raise ExternalDataError(
            "requests 가 설치되어 있지 않습니다.",
            hint="backend 가상환경에서 `pip install -r requirements.txt` 를 실행하십시오.",
        ) from exc
    return requests


def get_access_token(config: KisConfig | None = None, *, force_refresh: bool = False) -> str:
    """유효한 접근토큰을 돌려준다. 없거나 곧 만료면 발급받아 `ExternalToken` 에 저장한다.

    Args:
        force_refresh: 캐시를 무시하고 새로 받는다. `kis_probe --refresh` 전용이다.

    ★★ **왜 DB 에 넣는가** ────────────────────────────────────────────────

    서버리스는 콜드스타트마다 메모리가 비므로, 토큰을 프로세스에 두면
    **호출할 때마다 재발급**하게 된다. 그런데 KIS 는 토큰 발급을 **1분에 1회**로
    제한한다(EGW00133). 즉 메모리 캐시는 느린 게 아니라 **아예 동작하지 않는다.**

    ★★ **동시 발급을 막는다** ──────────────────────────────────────────────

    잡 1 과 잡 2 가 같은 순간에 토큰을 필요로 할 수 있다. 둘 다 발급을 시도하면
    한쪽은 EGW00133 을 맞는다. 행을 `select_for_update` 로 잡고 **잠근 뒤 다시 확인**한다
    — 기다리는 동안 다른 쪽이 이미 받아 놓았을 수 있기 때문이다
    (double-checked locking. E-04 6장이 캐시 갱신에 쓰라고 한 것과 같은 기법이다).

    Raises:
        ExternalDataError: 발급 실패. 자격증명 오류는 재시도하지 않는다.
    """
    config = config or load_config()
    now = timezone.now()

    # ── ① 잠그지 않고 먼저 본다 (대부분 여기서 끝난다) ─────────────
    if not force_refresh:
        cached = ExternalToken.objects.filter(
            provider=BrokerProvider.KIS, environment=config.environment
        ).first()
        if cached is not None and cached.access_token and cached.expires_at - TOKEN_RENEW_MARGIN > now:
            return cached.access_token

    # ★★ **실패 기록은 트랜잭션 밖에서 해야 한다** (변경노트 E-46) ────────────
    #
    #   `_remember_failure()` 를 아래 `atomic()` 안에서 부르면, 곧이어 던지는 예외가
    #   **그 기록까지 함께 롤백한다.** 그러면 쿨다운이 영영 켜지지 않고, 5초 잡이
    #   회차마다 토큰 발급을 두드려 EGW00133 을 자초한다 — 정확히 막으려던 상황이다.
    #
    #   그래서 "시도했는가" 만 밖으로 들고 나와 예외를 잡은 뒤에 기록한다.
    attempted = False
    try:
        # ── ② 발급이 필요하다 — 행을 잠그고 다시 확인한다 ──────────
        #
        # ★ `transaction.atomic()` 안에서 **외부 호출을 한다.** 이 모듈의 다른 곳과
        #   반대되는 예외이고, 의도한 것이다. 토큰 발급은 "동시에 두 번 하면 안 되는"
        #   일이라 잠금 구간 안에서 해야 의미가 있다. 대신 짧다(1회 · 8초 타임아웃).
        with transaction.atomic():
            row = (
                ExternalToken.objects.select_for_update()
                .filter(provider=BrokerProvider.KIS, environment=config.environment)
                .first()
            )
            now = timezone.now()
            if (
                not force_refresh
                and row is not None
                and row.access_token
                and row.expires_at - TOKEN_RENEW_MARGIN > now
            ):
                # 기다리는 사이 다른 프로세스가 받아 놓았다.
                return row.access_token

            # ★ 발급 실패 직후의 연타를 막는다. `issued_at` 이 방금이고 토큰이 비어 있으면
            #   "조금 전에 실패했다"는 뜻이다 (아래 `_remember_failure` 참조).
            if row is not None and not row.access_token and now - row.issued_at < TOKEN_RETRY_COOLDOWN:
                wait = TOKEN_RETRY_COOLDOWN - (now - row.issued_at)
                raise ExternalDataError(
                    f"KIS 토큰 발급이 방금 실패했습니다 — {wait.total_seconds():.0f}초 뒤에 다시 시도합니다.",
                    hint=(
                        "KIS 는 토큰 발급을 1분에 1회로 제한합니다(EGW00133). "
                        "연달아 두드리면 복구가 오히려 늦어집니다."
                    ),
                )

            attempted = True
            payload = _issue_token(config)
            access_token = payload["access_token"]
            expires_at = payload["expires_at"]

            ExternalToken.objects.update_or_create(
                provider=BrokerProvider.KIS,
                environment=config.environment,
                defaults={
                    "access_token": access_token,
                    "token_type": payload.get("token_type") or "Bearer",
                    "issued_at": timezone.now(),
                    "expires_at": expires_at,
                },
            )
    except ExternalDataError:
        # ★ 쿨다운 때문에 튕긴 것은 "시도" 가 아니다 — 기록하면 타이머가 계속 되감긴다.
        if attempted:
            _remember_failure(config)
        raise

    logger.info(
        "KIS 접근토큰을 발급했습니다 (%s · 만료 %s)",
        config.environment, timezone.localtime(expires_at).isoformat(timespec="seconds"),
    )
    return access_token


def _issue_token(config: KisConfig) -> dict:
    """`POST /oauth2/tokenP`. 성공하면 `{access_token, token_type, expires_at}`.

    ★ **재시도하지 않는다.** 발급은 1분에 1회 제한이라 실패를 즉시 되풀이하면
      원인이 무엇이든 EGW00133 으로 바뀐다. 실패는 기억해 두고(`_remember_failure`)
      다음 회차에 맡긴다 — 폴링 잡은 어차피 몇 초 뒤에 다시 온다.
    """
    requests = _requests()
    try:
        response = requests.post(
            f"{config.host}{PATH_TOKEN}",
            json={
                "grant_type": "client_credentials",
                "appkey": config.app_key,
                "appsecret": config.app_secret,
            },
            headers={"content-type": "application/json"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    except Exception as exc:        # noqa: BLE001 — requests 예외 종류를 특정하지 않는다
        raise ExternalDataError(
            f"KIS 토큰 발급 요청이 실패했습니다: {type(exc).__name__}",
            hint="네트워크 상태와 KIS 서버 점검 여부를 확인하십시오.",
        ) from exc

    body = _safe_json(response)

    if response.status_code != 200 or "access_token" not in body:
        # ★ 본문을 그대로 싣지 않는다. **오류 코드와 메시지만** 꺼낸다 —
        #   KIS 오류 응답에는 앱키가 되비쳐 오는 경우가 있다 (모듈 docstring).
        code = str(body.get("error_code") or body.get("msg_cd") or response.status_code)
        message = str(body.get("error_description") or body.get("msg1") or "")[:200]
        raise ExternalDataError(
            f"KIS 토큰 발급이 거부되었습니다 (코드 {code}): {message}",
            hint=_token_error_hint(code),
        )

    return {
        "access_token": body["access_token"],
        "token_type": body.get("token_type", "Bearer"),
        "expires_at": _parse_token_expiry(body),
    }


def _token_error_hint(code: str) -> str:
    """오류 코드별로 **다음에 할 일**을 알려준다."""
    if code == "EGW00133":
        return (
            "토큰 발급은 1분에 1회입니다. 잠시 뒤 자동으로 다시 시도합니다.\n"
            "    이 오류가 계속 나면 같은 앱키를 여러 프로세스가 동시에 쓰고 있는지 확인하십시오."
        )
    if code in ("EGW00121", "EGW00123", "EGW00201"):
        return (
            "앱키·시크릿이 맞는지, 그리고 KIS_MODE 와 발급 환경이 같은지 확인하십시오.\n"
            "    모의투자용 키로 실전 도메인을 부르면(또는 그 반대) 인증이 거부됩니다."
        )
    return (
        "backend/.env 의 KIS_APP_KEY · KIS_APP_SECRET · KIS_MODE 를 확인하십시오.\n"
        "    발급 절차 → docs/v2-design/api/version2.0/api_발급_가이드.md 2장"
    )


def _remember_failure(config: KisConfig) -> None:
    """토큰 발급 실패를 **DB 에 기록한다** — 다음 회차가 연타하지 않게.

    ★ `access_token=""` 인 행이 "방금 실패했다" 는 표시다. 별도 컬럼을 만들지 않는
      이유는 이 상태가 **1분짜리 임시 사실**이기 때문이다. 스키마를 늘릴 값어치가 없다.

    ★ **반드시 `get_access_token()` 의 `atomic()` 밖에서 부른다.** 안에서 부르면
      뒤이어 던지는 예외가 이 기록까지 롤백한다 (변경노트 E-46).

    ★★ **살아 있는 토큰은 덮지 않는다** ────────────────────────────────────

    행이 아직 없을 때는 `select_for_update()` 가 잠글 대상이 없어 두 프로세스가
    **동시에 첫 발급을 시도**할 수 있다. 한쪽은 성공하고 다른 쪽은 EGW00133 을
    맞는데, 그때 실패한 쪽이 무조건 덮어쓰면 **방금 받은 멀쩡한 토큰이 지워진다.**
    있으면 그대로 두고, 다음 호출이 그 토큰을 쓰게 한다.
    """
    try:
        with transaction.atomic():
            row = (
                ExternalToken.objects.select_for_update()
                .filter(provider=BrokerProvider.KIS, environment=config.environment)
                .first()
            )
            if row is not None and row.access_token and row.is_valid:
                logger.info("KIS 토큰 발급은 실패했으나 유효한 토큰이 이미 있습니다 — 그대로 둡니다")
                return
            ExternalToken.objects.update_or_create(
                provider=BrokerProvider.KIS,
                environment=config.environment,
                defaults={
                    "access_token": "",
                    "issued_at": timezone.now(),
                    # 만료를 과거로 둬 "쓸 수 없는 토큰" 임을 분명히 한다.
                    "expires_at": timezone.now(),
                },
            )
    except Exception:       # noqa: BLE001 — 기록 실패가 원래 오류를 덮으면 안 된다
        logger.exception("KIS 토큰 발급 실패 기록에 실패했습니다")


def _parse_token_expiry(body: dict) -> datetime:
    """만료 시각. `access_token_token_expired` 우선, 없으면 `expires_in` 으로 계산한다.

    ★ **`access_token_token_expired` 는 naive 문자열이다** (`"2026-08-14 15:30:00"`).
      KIS 서버는 한국에 있으므로 **KST 로 읽는다.** UTC 로 읽으면 만료가 9시간
      뒤로 보여 이미 죽은 토큰을 계속 쓰게 되고, 매 호출이 EGW00123 으로 실패한다.
    """
    from core.time import KST      # noqa: PLC0415 — 순환 임포트 회피

    raw = str(body.get("access_token_token_expired") or "").strip()
    if raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        except ValueError:
            logger.warning("KIS 토큰 만료 형식을 해석하지 못했습니다 — expires_in 으로 계산합니다")

    try:
        seconds = int(body.get("expires_in") or 0)
    except (TypeError, ValueError):
        seconds = 0
    # 값이 없으면 24시간으로 본다. 문서가 정한 수명이다.
    return timezone.now() + timedelta(seconds=seconds or 86_400)


# ─────────────────────────────────────────────────────────────────
# 2. 시세 조회
# ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class KisOrderbook:
    """KIS 호가 응답을 우리 형식으로 옮긴 것.

    `levels` 는 `OrderbookCache.levels` 에 그대로 들어가는 JSON 배열이고,
    `market/quotes.py` 의 `Level` 이 읽는 키와 같다 — **한 곳에서 정한 형식을
    양쪽이 공유한다.**
    """

    symbol: str
    levels: list[dict]
    total_ask_qty: Decimal
    total_bid_qty: Decimal


@dataclass(frozen=True)
class KisQuote:
    """KIS 현재가 응답."""

    symbol: str
    price: Decimal
    prev_close: Decimal
    change: Decimal
    change_pct: Decimal
    volume: int
    open: Decimal
    high: Decimal
    low: Decimal


def fetch_orderbook(symbol: str, *, config: KisConfig | None = None) -> KisOrderbook:
    """호가 10단계 (`FHKST01010200`).

    ★ **응답이 컬럼으로 펼쳐져 온다.** 배열이 아니라 `askp1` … `askp10` ·
      `askp_rsqn1` … `askp_rsqn10` 이라는 **40개 키**다. 그래서 루프로 모은다.
    """
    config = config or load_config()
    body = _call(
        PATH_ORDERBOOK,
        tr_id=TR_ORDERBOOK,
        params={"FID_COND_MRKT_DIV_CODE": MARKET_DIV_CODE, "FID_INPUT_ISCD": symbol},
        config=config,
        label=f"{symbol} 호가",
    )
    output = body.get("output1") or {}
    if not output:
        raise ExternalDataError(
            f"KIS 호가 응답에 output1 이 없습니다 ({symbol}).",
            hint="종목코드가 6자리 국내 종목인지 확인하십시오. 상장폐지·거래정지 종목일 수 있습니다.",
        )

    levels = []
    for step in range(1, ORDERBOOK_DEPTH + 1):
        levels.append({
            "ask_price": _num(output.get(f"askp{step}")),
            "ask_qty": _num(output.get(f"askp_rsqn{step}")),
            "bid_price": _num(output.get(f"bidp{step}")),
            "bid_qty": _num(output.get(f"bidp_rsqn{step}")),
        })

    # ★ **전 단계가 0 이면 값이 없는 것으로 본다.** 장 시작 전이나 거래정지 종목이
    #   이렇게 온다. 그대로 캐시에 넣으면 `Orderbook.opposite()` 가 빈 목록을 주고
    #   체결이 "호가는 있는데 잔량이 없다" 는 이상한 상태로 들어간다.
    if all(level["ask_price"] == 0 and level["bid_price"] == 0 for level in levels):
        raise ExternalDataError(
            f"KIS 호가가 전 단계 0 입니다 ({symbol}) — 장 시작 전이거나 거래정지 종목입니다.",
            hint="장중(09:00~15:30 KST)에 다시 조회하십시오.",
        )

    return KisOrderbook(
        symbol=symbol,
        levels=levels,
        total_ask_qty=_decimal(output.get("total_askp_rsqn")),
        total_bid_qty=_decimal(output.get("total_bidp_rsqn")),
    )


def fetch_quote(symbol: str, *, config: KisConfig | None = None) -> KisQuote:
    """현재가 (`FHKST01010100`).

    ★ **전일 종가는 `stck_sdpr`(주식 기준가)에서 온다.** `stck_prpr - prdy_vrss` 로
      빼서 구할 수도 있지만, 권리락·액면분할이 있는 날은 그 뺄셈이 맞지 않는다.
      기준가는 상·하한가 판정(`market/quotes.py` 의 `price_limits`)에도 쓰이는 값이라
      **KIS 가 알려주는 값을 그대로 받는 편**이 정확하다.
    """
    config = config or load_config()
    body = _call(
        PATH_QUOTE,
        tr_id=TR_QUOTE,
        params={"FID_COND_MRKT_DIV_CODE": MARKET_DIV_CODE, "FID_INPUT_ISCD": symbol},
        config=config,
        label=f"{symbol} 현재가",
    )
    output = body.get("output") or {}
    price = _decimal(output.get("stck_prpr"))
    if price <= 0:
        raise ExternalDataError(
            f"KIS 현재가가 0 입니다 ({symbol}).",
            hint="상장폐지·거래정지 종목이거나 종목코드가 잘못되었을 수 있습니다.",
        )

    prev_close = _decimal(output.get("stck_sdpr"))
    change = _decimal(output.get("prdy_vrss"))
    # ★ **부호는 별도 컬럼에 있다** — `prdy_vrss` 는 절댓값으로 오는 경우가 있다.
    #   `prdy_vrss_sign`: 1 상한 · 2 상승 · 3 보합 · 4 하한 · 5 하락
    if str(output.get("prdy_vrss_sign") or "").strip() in ("4", "5") and change > 0:
        change = -change

    return KisQuote(
        symbol=symbol,
        price=price,
        prev_close=prev_close if prev_close > 0 else price - change,
        change=change,
        change_pct=_decimal(output.get("prdy_ctrt")),
        volume=int(_num(output.get("acml_vol"))),
        open=_decimal(output.get("stck_oprc")),
        high=_decimal(output.get("stck_hgpr")),
        low=_decimal(output.get("stck_lwpr")),
    )


# ─────────────────────────────────────────────────────────────────
# 3. HTTP 공통
# ─────────────────────────────────────────────────────────────────


def _call(path: str, *, tr_id: str, params: dict, config: KisConfig, label: str) -> dict:
    """GET 1건 — 예산 차감 · 인증 헤더 · `rt_cd` 검사 · 재시도를 한 곳에 모은다.

    ★ **예산 차감은 재시도 안쪽**이다. 재시도도 실제 호출이므로 세야 한다.
      `spend_api_budget` 은 자리가 날 때까지 기다리는 배치용 함수다 — 폴링 잡은
      기다리는 사람이 없으므로 이쪽이 맞다 (market/services.py 참조).
    """
    requests = _requests()
    token = get_access_token(config)

    def _once():
        spend_api_budget(config.budget_name, limit=config.rate_limit)
        response = requests.get(
            f"{config.host}{path}",
            params=params,
            headers={
                "content-type": "application/json",
                "authorization": f"Bearer {token}",
                "appkey": config.app_key,
                "appsecret": config.app_secret,
                "tr_id": tr_id,
                # 개인(P) / 법인(B). 개인 계정이므로 P 다.
                "custtype": "P",
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            # ★ 본문을 싣지 않는다 — 401/403 응답에 요청 헤더가 되비치는 경우가 있다.
            raise ValueError(f"HTTP {response.status_code}")
        body = _safe_json(response)
        _check_rt_cd(body, label=label)
        return body

    return retry(
        _once,
        label=f"KIS {label}",
        on_retry=lambda attempt, exc, delay: logger.warning(
            "KIS %s %d회 실패 (%s) — %.0f초 후 재시도", label, attempt, type(exc).__name__, delay
        ),
    )


def _check_rt_cd(body: dict, *, label: str) -> None:
    """`rt_cd` 가 "0" 이 아니면 실패다 (모듈 docstring 참조).

    ★ **인증 오류는 `ExternalDataError` 로 직접 던져 재시도를 끊는다.**
      `core.jobs.retry` 는 `ExternalDataError` 를 그대로 올려보내도록 만들어져 있다 —
      잘못된 키로 세 번 두드려 봐야 결과가 같고, 그 사이 유량만 태운다.
    """
    rt_cd = str(body.get("rt_cd") or "0")
    if rt_cd == "0":
        return

    code = str(body.get("msg_cd") or "")
    message = str(body.get("msg1") or "")[:200].strip()

    if code in ("EGW00121", "EGW00123", "EGW00133"):
        raise ExternalDataError(
            f"KIS 인증이 거부되었습니다 ({label} · {code}): {message}",
            hint=_token_error_hint(code),
        )

    # 유량 초과·일시 오류는 재시도할 값어치가 있다. `retry` 가 백오프로 다시 온다.
    raise ValueError(f"{code or 'rt_cd=' + rt_cd} {message}")


def _safe_json(response) -> dict:
    """JSON 이 아니어도 터지지 않는다. **본문을 예외에 싣지 않기 위해** 여기서 삼킨다."""
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _num(raw) -> int:
    """KIS 는 숫자를 **문자열로** 준다 (`"74300"` · `""` · `"0"`)."""
    try:
        return int(float(str(raw).replace(",", "").strip() or 0))
    except (TypeError, ValueError):
        return 0


def _decimal(raw) -> Decimal:
    try:
        return Decimal(str(raw).replace(",", "").strip() or 0)
    except Exception:       # noqa: BLE001 — decimal.InvalidOperation 등
        return Decimal(0)


__all__ = [
    "KisConfig",
    "KisNotConfigured",
    "KisOrderbook",
    "KisQuote",
    "fetch_orderbook",
    "fetch_quote",
    "get_access_token",
    "is_configured",
    "load_config",
]
