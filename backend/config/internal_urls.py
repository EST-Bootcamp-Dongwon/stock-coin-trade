"""내부 잡 엔드포인트 — 라우팅과 레지스트리 (F-20 3장 · F-16 5.3).

    POST /internal/jobs/<잡-이름>     잡 실행
    GET  /internal/jobs/             등록된 잡 목록 (토큰 점검용)

★★ **이 파일이 왜 `config/` 에 있는가** ─────────────────────────────────────

레지스트리는 `market.services` · `insight.services` 를 직접 import 한다.
같은 코드를 `core/` 에 두면 **core → market 의존**이 생겨 규약 1.1 의 의존 방향
("core 는 아무것도 참조하지 않는다")이 깨진다.

    core/jobs.py       잡의 실행 정책 (기록·재시도)      ← 아무도 참조 안 함
    core/jobs_http.py  HTTP 입구의 정책 (본문·상태코드)  ← 아무도 참조 안 함
    config/            이 둘과 각 앱 서비스를 **엮는다** ← 전부 참조해도 되는 유일한 층

앱을 서로 엮는 일은 프로젝트 패키지(조립 지점)의 몫이다. Django 의 `config/urls.py`
가 원래 그 자리이고, 이 파일은 그 연장이다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 였다면 `APIRouter` 를 앱마다 만들어 `include_router` 로 붙였을 것이다.
여기서도 `market/urls.py` · `insight/urls.py` 를 각각 만들 수 있지만, 잡 엔드포인트는
**형태가 완전히 같은 것 4개**라 라우터를 앱마다 나누면 같은 껍데기가 네 번 반복된다.
딕셔너리 하나가 더 정직하다.
"""

from django.http import JsonResponse
from django.urls import re_path
from django.views.decorators.csrf import csrf_exempt

from contests.jobs import settle_contest, settle_daily, settle_weekly, snapshot_intraday
from core.jobs_http import JobSpec, as_bool, as_date, as_int, run_job_http
from core.time import today_kst
from insight.services import backfill_embeddings
from market.jobs import poll_orderbook, poll_quotes
from market.services import (
    sync_market_index,
    sync_stock_master,
    sync_trading_calendar,
    sync_upbit_markets,
)
from trading.jobs import cancel_stale_orders, match_pending_orders, open_pending_orders


def _sync_trading_calendar(*, year: int | None = None, **kwargs):
    """`year` 를 생략하면 **올해(KST)** 로 본다.

    ★ 서비스 함수 쪽에 기본값을 두지 않는 이유 — 커맨드는 `--year` 를 필수로 받는다.
    사람이 손으로 돌릴 때는 어느 해를 채우는지 명시하는 편이 안전하다. 반면 cron 은
    매달 같은 SQL 이 돌아야 하므로 "부르는 시점의 해" 가 유일하게 말이 되는 기본값이다.
    **기대가 다른 두 입구를 하나의 기본값으로 억지로 맞추지 않는다.**
    """
    return sync_trading_calendar(year=year if year is not None else today_kst().year, **kwargs)


# ── 잡 레지스트리 ───────────────────────────────────────────────
#
# URL 은 **하이픈**, `DataSyncLog.job_name` 은 **스네이크** 다.
#   · URL 은 남이 보는 주소라 하이픈이 관례다 (F-20 3장의 예시 SQL 도 하이픈이다)
#   · job_name 은 커맨드 이름(`manage.py sync_stock_master`)과 같아야 한다.
#     CLI 로 돌리든 HTTP 로 돌리든 **같은 이름으로 이력이 쌓여야** 연속 실패 판정과
#     Admin 필터가 성립하기 때문이다.
# 자동 변환(`replace("-", "_")`)에 맡기지 않고 둘 다 적는다 — 규칙이 아니라
# 매핑이라는 사실이 눈에 보이는 편이 낫다.
JOB_REGISTRY: dict[str, JobSpec] = {
    "sync-stock-master": JobSpec(
        job_name="sync_stock_master",
        service=sync_stock_master,
        params={"base_date": as_date, "with_sectors": as_bool},
        summary="pykrx 종목 마스터 적재 (F-20 잡 8 · 영업일 16:00 KST)",
    ),
    "sync-trading-calendar": JobSpec(
        job_name="sync_trading_calendar",
        service=_sync_trading_calendar,
        params={"year": as_int},
        summary="영업일 달력 적재 (F-20 잡 8 부속 · 월 1회 · 생략 시 올해)",
    ),
    "sync-upbit-markets": JobSpec(
        job_name="sync_upbit_markets",
        service=sync_upbit_markets,
        summary="업비트 마켓 목록 upsert (F-20 잡 11 · 매일 18:00 KST)",
    ),
    "backfill-embeddings": JobSpec(
        job_name="backfill_embeddings",
        service=backfill_embeddings,
        params={"limit": as_int, "batch_size": as_int, "rebuild": as_bool},
        summary="지식 문서 임베딩 채우기 (F-20 미등재 · 문서 승인 후 수동/저빈도)",
    ),
    # ── 시세 폴링 2종 (F-20 잡 1 · 2) ───────────────────────────
    #
    # ★★ **로컬 `run_trading_loop --loop` 와 함께 켜지 않는다.** 둘 다 돌면 같은
    #    종목을 두 배로 조회해 **초당 몇 건뿐인 KIS 유량이 절반으로 준다.**
    #    실행처는 하나만 고른다 (backend/sql/pg_cron_jobs.sql 4장).
    "poll-orderbook": JobSpec(
        job_name="poll_orderbook",
        service=poll_orderbook,
        summary="대회 종목 호가 10단계 갱신 — KIS (F-20 잡 2 · 장중 5초)",
    ),
    "poll-quotes": JobSpec(
        job_name="poll_quotes",
        service=poll_quotes,
        summary="우선순위 기반 현재가 갱신 — KIS→네이버→시뮬 (F-20 잡 1 · 장중 10초)",
    ),
    # ── 체결 3종 (F-20 잡 3 · 5 · 6) ────────────────────────────
    #
    # ★ 위의 적재 잡들과 성격이 다르다. **외부 API 를 부르지 않고 DB 안의 데이터만
    #   다룬다.** 그래서 실행이 짧고(수십 ms), 서버리스 한도(E-28)에 걸리지 않는다.
    #   느린 것은 `sync_stock_master`(88초)이지 이쪽이 아니다.
    "match-pending-orders": JobSpec(
        job_name="match_pending_orders",
        service=match_pending_orders,
        summary="미체결 주문 시장 체결 추종 + STOP 발동 (F-20 잡 3 · 장중 5초)",
    ),
    "open-market": JobSpec(
        job_name="open_market",
        service=open_pending_orders,
        summary="PENDING_OPEN 주문을 접수로 올린다 (F-20 잡 5 · 영업일 09:00 KST)",
    ),
    "close-market": JobSpec(
        job_name="close_market",
        service=cancel_stale_orders,
        summary="장 마감 미체결 대회 주문 자동 취소 (F-20 잡 6 · 영업일 15:35 KST)",
    ),
    # ── 정산 4종 + 벤치마크 (F-20 잡 4 · 7 · 9 · 10 · F-05 4.4) ──
    #
    # ★ 체결 잡과 마찬가지로 **외부 API 를 부르지 않는다** — `sync-market-index` 만
    #   예외로 pykrx 를 쓴다(호출 2건). 나머지 넷은 DB 안의 데이터만 다룬다.
    #
    # ★★ **날짜 인자는 전부 선택이다.** cron 은 넘기지 않고(=오늘/지난 주),
    #    운영자만 지정한다. 그 차이가 잡의 동작을 바꾼다 — `settle-daily` 는
    #    날짜를 받으면 휴장일 판정을 건너뛴다 (contests/jobs.py 참조).
    "snapshot-intraday": JobSpec(
        job_name="snapshot_intraday",
        service=snapshot_intraday,
        summary="참가자 장중 수익률 10분 스냅샷 (F-20 잡 4 · 장중 10분)",
    ),
    "settle-daily": JobSpec(
        job_name="settle_daily",
        service=settle_daily,
        params={"target_date": as_date},
        summary="일별 정산 — 스냅샷·NAV·순위·위반 (F-20 잡 7 · 영업일 15:40 KST)",
    ),
    "settle-weekly": JobSpec(
        job_name="settle_weekly",
        service=settle_weekly,
        params={"week_start": as_date},
        summary="주간 회전율 확정 + 4회 위반 자동 정지 (F-20 잡 9 · 월 06:00 KST)",
    ),
    "settle-contest": JobSpec(
        job_name="settle_contest",
        service=settle_contest,
        params={"target_date": as_date},
        summary="대회 상태 전이 + 종료 대회 최종 정산 (F-20 잡 10 · 매일 06:00 KST)",
    ),
    "sync-market-index": JobSpec(
        job_name="sync_market_index",
        service=sync_market_index,
        params={"base_date": as_date, "days": as_int},
        summary="KOSPI·KOSDAQ 일별 종가 적재 — NAV 차트 벤치마크 (F-05 4.4)",
    ),
}


# ── 뷰 ──────────────────────────────────────────────────────────
# ★★ `csrf_exempt` 가 필요한 이유 ─────────────────────────────────
#
# pg_net 은 브라우저가 아니다. 쿠키도 CSRF 토큰도 없다. `CsrfViewMiddleware` 는
# 세션 쿠키가 없으면 통과시키지만, **그 동작에 기대지 않고 명시적으로 면제**한다.
# 나중에 누가 인증 미들웨어를 손대도 이 경로는 흔들리지 않는다.
#
# CSRF 를 면제해도 안전한 이유는 **미들웨어가 이미 잠갔기** 때문이다. CSRF 는
# "브라우저가 사용자의 쿠키를 실어 남의 사이트에서 요청을 보내는 것"을 막는 장치인데,
# 여기는 쿠키로 인증하지 않는다. 헤더의 비밀값을 아는 쪽만 통과한다.
@csrf_exempt
def run_job(request, job_slug: str):
    """`POST /internal/jobs/<잡-이름>`."""
    spec = JOB_REGISTRY.get(job_slug)
    if spec is None:
        # ★ 여기까지 왔다는 것은 **토큰 검사를 이미 통과했다**는 뜻이다.
        #   그러므로 목록을 알려줘도 된다 — 오히려 알려줘야 한다. cron SQL 의 오타를
        #   찾는 사람에게 빈 404 를 주면 원인을 짚을 방법이 없다 (규약 8.5).
        #   토큰이 틀린 요청은 이 뷰에 도달하지 못하고 미들웨어에서 빈 404 로 끝난다.
        return JsonResponse(
            {
                "status": "UNKNOWN_JOB",
                "error": f"등록되지 않은 잡입니다: {job_slug}",
                "available": sorted(JOB_REGISTRY),
            },
            status=404,
            json_dumps_params={"ensure_ascii": False},
        )
    return run_job_http(request, spec)


@csrf_exempt
def job_index(request):
    """`GET /internal/jobs/` — 등록된 잡 목록.

    **부수효과가 없는 유일한 내부 엔드포인트다.** 토큰·방화벽·배포 주소가 제대로
    엮였는지 확인할 때 여기를 먼저 친다. 종목 마스터를 실제로 적재해 보면서
    배선을 점검할 수는 없다.

        curl -sS -H "X-Internal-Token: $TOKEN" https://<앱주소>/internal/jobs/
    """
    if request.method != "GET":
        return JsonResponse(
            {"status": "METHOD_NOT_ALLOWED", "error": "목록 조회는 GET 입니다."},
            status=405,
            json_dumps_params={"ensure_ascii": False},
        )
    return JsonResponse(
        {
            "status": "OK",
            "jobs": [
                {
                    "slug": slug,
                    "job_name": spec.job_name,
                    "params": sorted(spec.params),
                    "summary": spec.summary,
                }
                for slug, spec in sorted(JOB_REGISTRY.items())
            ],
            "common_params": ["dry_run", "force", "triggered_by"],
        },
        json_dumps_params={"ensure_ascii": False},
    )


# ── URL ─────────────────────────────────────────────────────────
# ★★ **끝 슬래시를 선택적으로 둔 이유** ─────────────────────────────────────
#
# `path("jobs/<slug>/")` 처럼 슬래시로 끝나게 정의하면, 슬래시 없이 온 POST 를
# `CommonMiddleware(APPEND_SLASH)` 가 **301 리다이렉트**로 돌려보낸다.
# 그런데 리다이렉트를 따라갈 때 **본문과 커스텀 헤더가 사라진다.**
#   → `X-Internal-Token` 이 빠지고 → 미들웨어가 404 를 준다
#   → cron SQL 은 "그런 경로 없음"만 보고 원인을 알 수 없다
# `re_path` 로 `/?` 를 붙여 양쪽 다 같은 뷰에 꽂는다. 잡 이름은 소문자·숫자·하이픈만
# 허용해 이상한 경로가 뷰까지 들어오지 않게 한다.
urlpatterns = [
    re_path(r"^jobs/?$", job_index, name="internal-job-index"),
    re_path(r"^jobs/(?P<job_slug>[a-z0-9-]+)/?$", run_job, name="internal-job-run"),
]
