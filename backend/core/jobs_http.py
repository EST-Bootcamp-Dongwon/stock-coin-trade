"""잡을 HTTP 로 부르는 입구 (F-20 3장 · F-16 5.3).

    pg_cron ──pg_net──▶ POST /internal/jobs/sync-stock-master ─┐
                                                               ├─▶ services.sync_stock_master()
    운영자 ──CLI──────▶ python manage.py sync_stock_master ────┘

**이 모듈에는 잡 로직이 하나도 없다.** `core/management/base.py` 의 `JobCommand` 가
CLI 쪽에서 하는 일(인자 풀기 · 결과 찍기 · 외부 장애와 코드 버그 가르기)을,
여기서는 HTTP 쪽 어휘(JSON 본문 · 상태 코드)로 다시 할 뿐이다. 둘은 **같은 서비스
함수를 부르는 두 개의 껍데기**다 (F-16 5.3).

★★ **`on_progress` · `notes` 를 응답 JSON 에 담는다** ─────────────────────────

CLI 는 진행 상황을 화면에 찍는다. HTTP 경로에서 그걸 버리면 pg_cron 이 돌린 잡은
**"몇 건 처리했다" 밖에 남지 않는다.** 그런데 이 잡들이 실제로 알려주는 것은 숫자가
아니라 품질 정보다:

    "업종이 매겨진 종목 2,729종"        ← 33종이 비었다는 뜻
    "2026년은 진행 중입니다 — 8월까지만" ← 남은 넉 달은 아직 못 채웠다
    "배치가 건드리지 않는 필드: ..."     ← 이 값들은 배치를 믿으면 안 된다

`DataSyncLog` 에는 이런 문장이 들어갈 자리가 없다(rows_affected 는 정수 하나다).
그래서 **응답 본문에 실어 보낸다.** pg_net 은 응답을 `net._http_response` 에
보관하므로, 운영자는 DB 안에서 그대로 읽을 수 있다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 였다면 `@app.post(...)` 에 Pydantic 모델을 붙여 본문 검증을 공짜로 받았을
것이다. Django 뷰는 `request.body` 가 그냥 bytes 라 파싱·검증을 직접 한다.
DRF 의 Serializer 를 쓸 수도 있지만 **여기서는 쓰지 않는다** — 내부 잡 엔드포인트는
DRF 의 인증·권한·렌더러 협상이 전부 불필요하고, 미들웨어가 이미 잠갔기 때문이다.
얇은 함수 뷰가 오히려 읽기 쉽다.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable

from django.http import JsonResponse
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from core.constants import TriggeredBy
from core.jobs import ExternalDataError, SyncResult, has_consecutive_failures

logger = logging.getLogger(__name__)

# 응답에 실을 진행 로그 최대 줄 수. `sync_stock_master --with-sectors` 가
# 60줄 남짓 뱉으므로 넉넉하지만, 무제한으로 두면 응답이 언제까지 커질지 알 수 없다.
PROGRESS_LINE_LIMIT = 200

# HTTP 로 부를 때 허용하는 실행 주체. CLI 는 여기 없다 — 커맨드가 아니기 때문이다.
HTTP_TRIGGERED_BY = (TriggeredBy.CRON, TriggeredBy.ADMIN)


class ProgressCollector:
    """`on_progress` 콜백. 커맨드의 `self.stdout.write` 자리에 들어간다.

    서비스는 자기가 화면에 찍히는지 JSON 에 담기는지 모른다 — 문자열을 넘길 뿐이다.
    그 무지가 서비스 계층을 CLI·HTTP 양쪽에서 쓸 수 있게 만든다.
    """

    def __init__(self, *, limit: int = PROGRESS_LINE_LIMIT, log_prefix: str = ""):
        self.limit = limit
        self.log_prefix = log_prefix
        self.lines: list[str] = []
        self.dropped = 0

    def __call__(self, message: str) -> None:
        text = str(message)
        # ★ 서버 로그에도 남긴다. pg_net 은 타임아웃이 나면 응답을 버리는데,
        #   그때 진행 상황이 통째로 사라지면 안 된다.
        logger.debug("%s%s", self.log_prefix, text)
        if len(self.lines) < self.limit:
            self.lines.append(text)
        else:
            self.dropped += 1

    def as_list(self) -> list[str]:
        if self.dropped:
            return [*self.lines, f"… 진행 로그 {self.dropped:,}줄 생략 (상한 {self.limit:,}줄)"]
        return list(self.lines)


# ── 본문 값 파서 ────────────────────────────────────────────────
# JSON 은 타입이 있지만 사람이 손으로 적는 SQL 안의 JSON 은 아무거나 들어온다.
# `"true"` 도 `true` 도 받아준다 — 다만 **말이 안 되는 값은 400 으로 거부한다.**


def as_bool(raw: Any) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str) and raw.strip().lower() in ("1", "true", "yes", "on"):
        return True
    if isinstance(raw, str) and raw.strip().lower() in ("0", "false", "no", "off", ""):
        return False
    raise ValueError(f"참/거짓이어야 합니다 (받은 값: {raw!r})")


def as_int(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"정수여야 합니다 (받은 값: {raw!r})") from None


def as_date(raw: Any) -> date:
    """`YYYY-MM-DD` 만 받는다."""
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    try:
        return datetime.strptime(str(raw), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"YYYY-MM-DD 형식이어야 합니다 (받은 값: {raw!r})") from None


@dataclass(frozen=True)
class JobSpec:
    """엔드포인트 하나의 정의.

    Attributes:
        job_name: `DataSyncLog.job_name` 에 남는 이름. **스네이크 케이스**다
            (URL 은 하이픈, 기록은 스네이크 — 아래 `slug_to_job_name` 주석 참조).
        service: 부를 서비스 함수. `triggered_by` · `dry_run` · `on_progress` 를
            키워드로 받고 `SyncResult` 를 돌려주는 규약을 지켜야 한다.
        params: 이 잡만의 인자. `{본문 키: 파서}` 형태다.
            **여기 없는 키가 오면 400 이다** — 오타를 조용히 삼키지 않는다.
        summary: `GET /internal/jobs/` 목록에 뜨는 한 줄 설명.
    """

    job_name: str
    service: Callable[..., SyncResult]
    params: dict[str, Callable[[Any], Any]] = field(default_factory=dict)
    summary: str = ""


class BadRequest(Exception):
    """본문이 잘못됐다 — 400 으로 돌려보낸다."""


def read_body(request) -> dict[str, Any]:
    """요청 본문을 dict 로 읽는다. 빈 본문은 `{}` 로 본다.

    ★ 빈 본문을 허용하는 이유 — cron SQL 이 인자 없이 부르는 잡
    (`sync_upbit_markets`)에 `body := '{}'` 를 강제하면 SQL 만 길어진다.

    Raises:
        BadRequest: JSON 이 아니거나 객체가 아닐 때.
    """
    raw = request.body or b""
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BadRequest(f"본문이 올바른 JSON 이 아닙니다: {exc}") from exc
    if not isinstance(body, dict):
        raise BadRequest("본문은 JSON 객체({...})여야 합니다.")
    return body


def parse_options(body: dict[str, Any], spec: JobSpec) -> dict[str, Any]:
    """본문을 서비스 함수의 키워드 인자로 바꾼다.

    Raises:
        BadRequest: 모르는 키가 있거나 값 형식이 틀렸을 때.
    """
    # ★ 공통 인자 3개는 모든 잡이 받는다. 잡별 `params` 와 이름이 겹치지 않게 주의.
    known = {"dry_run", "force", "triggered_by", *spec.params}
    unknown = sorted(set(body) - known)
    if unknown:
        # 모르는 키를 무시하면 **오타 난 인자가 조용히 안 먹는다.**
        # `{"with_sector": true}` 를 보내고 업종이 안 채워지는데 성공으로 뜨는 것보다,
        # 400 으로 "그런 인자 없다, 쓸 수 있는 건 이것들이다" 를 주는 편이 낫다.
        raise BadRequest(
            f"모르는 인자: {', '.join(unknown)} / "
            f"이 잡이 받는 인자: {', '.join(sorted(known)) or '없음'}"
        )

    options: dict[str, Any] = {}
    for key, parser in spec.params.items():
        if key in body:
            try:
                options[key] = parser(body[key])
            except ValueError as exc:
                raise BadRequest(f"인자 {key}: {exc}") from exc
    return options


def _parse_common(body_key: str, raw: Any, parser) -> Any:
    try:
        return parser(raw)
    except ValueError as exc:
        raise BadRequest(f"인자 {body_key}: {exc}") from exc


def run_job_http(request, spec: JobSpec) -> JsonResponse:
    """잡 하나를 실행하고 결과를 JSON 으로 돌려준다.

    상태 코드는 **운영자가 원인을 바로 좁힐 수 있게** 나눈다:

    | 코드 | 뜻 | 누구 잘못인가 |
    |---|---|---|
    | 200 | 정상 종료 | — |
    | 400 | 본문·인자가 잘못됨 | 부르는 쪽(cron SQL) |
    | 405 | POST 가 아님 | 부르는 쪽 |
    | 409 | 3회 연속 실패로 건너뜀 | 이미 알려진 장애 |
    | 503 | 외부 데이터 소스 장애 | 남의 서버 (KRX·업비트) |
    | 500 | 우리 코드의 버그 | 우리 |

    ★ **실패해도 200 을 주지 않는다.** pg_net 은 상태 코드를
    `net._http_response` 에 남기므로, 200 으로 뭉개면 DB 안에서 실패를 구분할 수
    없어진다. `DataSyncLog` 를 봐야만 알 수 있게 되는 건 관측 경로를 하나 줄이는 셈이다.
    """
    if request.method != "POST":
        # ★ GET 을 막는 이유는 형식이 아니라 **안전** 이다. 잡은 DB 를 바꾼다.
        #   링크를 눌렀다고, 크롤러가 지나갔다고 종목 마스터가 다시 적재되면 안 된다.
        response = _json(
            {
                "job": spec.job_name,
                "status": "METHOD_NOT_ALLOWED",
                "error": "이 엔드포인트는 POST 만 받습니다 (잡 실행은 부수효과가 있습니다).",
            },
            status=405,
        )
        # 405 에는 Allow 헤더를 붙이는 것이 규격이다 (RFC 9110 15.5.6).
        response["Allow"] = "POST"
        return response

    try:
        body = read_body(request)
        options = parse_options(body, spec)
        dry_run = _parse_common("dry_run", body.get("dry_run", False), as_bool)
        force = _parse_common("force", body.get("force", False), as_bool)
        triggered_by = body.get("triggered_by", TriggeredBy.CRON)
        if triggered_by not in HTTP_TRIGGERED_BY:
            raise BadRequest(
                f"triggered_by 는 {' 또는 '.join(HTTP_TRIGGERED_BY)} 여야 합니다 "
                f"(받은 값: {triggered_by!r}). CLI 는 커맨드 전용입니다."
            )
    except BadRequest as exc:
        return _json(
            {"job": spec.job_name, "status": "BAD_REQUEST", "error": str(exc)}, status=400
        )

    # ★★ **연속 실패한 잡은 스케줄러 경로에서 자동으로 멈춘다** (F-20 5장).
    #    외부 API 가 죽어 있는데 10초마다 계속 두드리면 상대에게도 우리에게도 손해다.
    #    CLI(`JobCommand`)는 같은 상황에서 **경고만 하고 실행한다** — 사람이 손으로
    #    돌리는 것은 보통 복구를 시도하는 중이기 때문이다. 자동과 수동의 기대가 다르다.
    #    `{"force": true}` 로 이 판정을 넘길 수 있다 (Admin 버튼용).
    if not force and not dry_run and has_consecutive_failures(spec.job_name):
        logger.warning("%s: 3회 연속 실패로 건너뜁니다 (force 로 무시 가능)", spec.job_name)
        return _json(
            {
                "job": spec.job_name,
                "status": "SKIPPED",
                "reason": "최근 3회 연속 실패 — 자동 비활성화 상태입니다 (F-20 5장).",
                "hint": (
                    "Admin > 배치 실행 이력 에서 스택트레이스를 확인하고 원인을 고친 뒤, "
                    "CLI 로 한 번 성공시키거나 본문에 {\"force\": true} 를 넣어 부르십시오."
                ),
                "admin_url": _admin_log_url(spec.job_name),
            },
            status=409,
        )

    progress = ProgressCollector(log_prefix=f"[{spec.job_name}] ")
    started_at = timezone.now()
    clock = time.monotonic()

    try:
        result: SyncResult = spec.service(
            triggered_by=triggered_by,
            dry_run=dry_run,
            on_progress=progress,
            **options,
        )
    except ExternalDataError as exc:
        # 외부 장애. `DataSyncLog` 에는 이미 FAILED 로 남아 있다(E-23).
        elapsed = time.monotonic() - clock
        logger.warning("%s: 외부 데이터 실패 (%.1f초) — %s", spec.job_name, elapsed, exc)
        return _json(
            {
                **_envelope(spec, started_at, elapsed, dry_run, triggered_by),
                "status": "FAILED",
                "error": str(exc),
                "hint": exc.hint,
                "progress": progress.as_list(),
            },
            status=503,
        )
    except Exception as exc:  # noqa: BLE001 — 코드 버그도 JSON 으로 답해야 한다
        # ★ 스택트레이스는 **응답에 싣지 않는다.** 파일 경로·라이브러리 버전이
        #   그대로 드러난다. 전체는 서버 로그와 `DataSyncLog.error` 에 있다.
        elapsed = time.monotonic() - clock
        logger.exception("%s: 처리 중 예외 (%.1f초)", spec.job_name, elapsed)
        return _json(
            {
                **_envelope(spec, started_at, elapsed, dry_run, triggered_by),
                "status": "ERROR",
                "error": f"{type(exc).__name__}: {exc}",
                "hint": "스택트레이스는 Admin > 배치 실행 이력 에 있습니다.",
                "progress": progress.as_list(),
            },
            status=500,
        )

    elapsed = time.monotonic() - clock
    logger.info(
        "%s: 완료 (%.1f초) 신규 %d · 갱신 %d · 건너뜀 %d%s",
        spec.job_name, elapsed, result.created, result.updated, result.skipped,
        " [dry-run]" if dry_run else "",
    )
    return _json(
        {
            **_envelope(spec, started_at, elapsed, dry_run, triggered_by),
            "status": "SUCCESS",
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
            "rows": result.rows,
            # ↓ 이 둘이 이 응답의 핵심이다 (모듈 docstring 참조)
            "notes": list(result.notes),
            "progress": progress.as_list(),
        },
        status=200,
    )


def _envelope(spec: JobSpec, started_at, elapsed: float, dry_run: bool, triggered_by: str) -> dict:
    """성공·실패가 공유하는 응답 머리말."""
    return {
        "job": spec.job_name,
        "dry_run": dry_run,
        "triggered_by": triggered_by,
        # ★ KST 로 적는다. `USE_TZ=True` 라 내부는 UTC 지만, 이 문자열을 읽는 사람은
        #   한국에 있고 잡 시각도 KST 로 정의돼 있다 (F-20 2장).
        "started_at": timezone.localtime(started_at).isoformat(),
        "elapsed_seconds": round(elapsed, 2),
        "admin_url": _admin_log_url(spec.job_name),
    }


def _admin_log_url(job_name: str) -> str:
    """이 잡의 실행 이력만 걸러 보는 Admin 링크.

    응답을 읽는 사람이 다음에 할 일은 십중팔구 "이력을 본다" 이다. 주소를 같이 준다.
    """
    try:
        return f"{reverse('admin:core_datasynclog_changelist')}?job_name={job_name}"
    except NoReverseMatch:
        # Admin 을 끈 배포에서도 잡은 돌아야 한다.
        return ""


def _json(payload: dict, *, status: int) -> JsonResponse:
    # `ensure_ascii=False` — 한글 메시지가 \uXXXX 로 깨져 나오면 psql 에서 읽을 수 없다.
    return JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})
