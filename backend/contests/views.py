"""대회 화면군 (U-02 · [ui/00-화면목록-라우팅] 2.1 의 #2 · #3 · #4 · #8 · #13).

    GET       /contests/                                   대회 목록
    GET       /contests/<slug>/                            대회 상세
    GET       /contests/<slug>/ranking/                    대회 랭킹
    GET       /contests/<slug>/fragments/ranking/          랭킹 표 (폴링 60초)
    GET/POST  /contests/<slug>/join/                       참가 신청
    GET       /contests/<slug>/participants/<pk>/          참가자 상세 (모달)

★★ **왜 `<id>` 가 아니라 `<slug>` 인가** ────────────────────────────────────

대회는 외부 공유가 잦다. `/contests/rfm-1/` 이 `/contests/3/` 보다 읽기 쉽고,
내부 id 를 노출하지 않아 "3이 있으면 1·2도 있겠네" 라는 추측도 막는다
(변경노트 B-3). `Contest.slug` 는 `unique=True` 로 이미 마이그레이션에 들어 있다.

★★ **이 화면들은 외부 API 를 절대 부르지 않는다** ─────────────────────────

순위·수익률은 정산 잡(15:40)이 만들어 둔 `ContestRanking` 을 **읽기만** 한다
(F-05 7장). 조회 시점에 다시 계산하면 홈 위젯과 랭킹 화면이 서로 다른 숫자를
말하고, 참가자 100명 × 보유 종목만큼의 평가 연산이 매 요청마다 돈다.

Django 관점 ─────────────────────────────────────────────────────────────────

FastAPI 라면 `@app.get("/contests/{slug}")` + Pydantic 응답 모델이었고, 화면은
React 가 그렸다. 여기서는 **뷰가 곧 화면**이다. 뷰는 서비스에서 조립된 값을 받아
템플릿에 넘기기만 하고, 판단(공개 범위·신청 가능 여부)은 전부 서비스가 끝내 둔다.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from contests import services
from contests.forms import JoinForm
from contests.models import (
    ApprovalMode,
    Contest,
    ContestStatus,
    DailySnapshot,
    Participation,
    ParticipationStatus,
    SnapshotHolding,
    Visibility,
)
from core.htmx import hx_redirect, is_htmx
from core.time import today_kst

# 참가자 상세 모달에 싣는 거래 이력 건수.
#
# ★ 전부 보여주지 않는다. 한 달 대회에서 활발한 참가자는 수백 건이 되고, 모달
#   하나가 그만큼 무거워진다. "언제 사고팔았는지" 를 보는 것이 목적이라
#   최근 몇 건이면 충분하다 — 전체 이력은 대회 결과 화면(다음 세션)의 몫이다.
RECENT_ORDER_LIMIT = 20


# ─────────────────────────────────────────────────────────────────
# 접근 제어
# ─────────────────────────────────────────────────────────────────


def _visible_contest(request, slug: str) -> Contest:
    """주소로 들어온 대회를 꺼내되, 볼 수 없는 대회면 404 를 낸다.

    ★★ **403 이 아니라 404 인 이유** — 403("권한이 없습니다")은 *그 대회가 존재
      한다*는 사실을 알려 준다. 비공개 대회는 존재 자체가 비공개다. slug 를
      바꿔 가며 찔러 보는 사람에게 "이 이름은 있고 저 이름은 없다" 를 알려주지
      않는다.

    규칙 ([ui/00-화면목록-라우팅] 5장 · F-02 3장):

        DRAFT      운영자에게만 — 참가자에게는 아직 존재하지 않는 대회다
        PUBLIC     누구나
        LINK       주소를 아는 사람은 볼 수 있다 (목록에는 안 뜬다)
        PRIVATE    참가자와 운영자만
    """
    contest = get_object_or_404(Contest, slug=slug)
    user = request.user

    if getattr(user, "is_staff", False):
        return contest

    if contest.status == ContestStatus.DRAFT:
        raise Http404("준비 중인 대회입니다.")

    if contest.visibility == Visibility.PRIVATE:
        joined = (
            user.is_authenticated
            and Participation.objects.filter(contest=contest, member=user).exists()
        )
        if not joined:
            raise Http404("비공개 대회입니다.")

    return contest


def _my_participation(request, contest: Contest) -> Participation | None:
    """이 대회에 대한 내 참가 행. 비로그인이면 `None`.

    ★ `PENDING`(승인 대기)도 그대로 돌려준다. 화면이 "승인을 기다리는 중" 을
      보여줘야 하므로 여기서 걸러내면 안 된다.
    """
    if not request.user.is_authenticated:
        return None
    return (
        Participation.objects.filter(contest=contest, member=request.user)
        .select_related("account")
        .first()
    )


# ─────────────────────────────────────────────────────────────────
# 1. 대회 목록 (U-02 4.1)
# ─────────────────────────────────────────────────────────────────


def contest_list(request):
    """대회 목록 — 진행 중 / 모집 중 / 종료 탭.

    ★ 비로그인도 볼 수 있다. 이 서비스가 무엇을 하는 곳인지 가입 전에 알 수
      있어야 한다 (라우팅 문서 5장).
    """
    tab = request.GET.get("tab") or services.DEFAULT_TAB
    if tab not in {key for key, _, _ in services.LIST_TABS}:
        tab = services.DEFAULT_TAB

    return render(
        request,
        "contests/list.html",
        {
            "context_type": "contest_list",
            "tabs": services.LIST_TABS,
            "active_tab": tab,
            "cards": services.contest_cards(member=request.user, tab=tab),
        },
    )


# ─────────────────────────────────────────────────────────────────
# 2. 대회 상세 (U-02 4.2)
# ─────────────────────────────────────────────────────────────────


def contest_detail(request, slug: str):
    """대회 상세 — 규칙 요약 · 규칙 전문 · 데이터 출처 · 참가 액션."""
    contest = _visible_contest(request, slug)
    participation = _my_participation(request, contest)
    today = today_kst()

    return render(
        request,
        "contests/detail.html",
        {
            "context_type": "contest_detail",
            "context_id": contest.slug,
            "contest": contest,
            "participation": participation,
            "notice": services.participation_notice(participation),
            # 참가자 전용 안내(대시보드 준비 중 등)를 띄울지. 승인된 사람만이다.
            "is_approved": (
                participation is not None
                and participation.status == ParticipationStatus.APPROVED
            ),
            "participant_count": services.approved_count(contest),
            "entry_open": services.is_entry_open(contest, today=today),
            "entry_deadline": services.entry_deadline_of(contest),
            "d_day": (contest.end_date - today).days,
            "rule_rows": services.rule_summary(contest),
            "data_sources": services.DATA_SOURCES,
            # 랭킹 탭으로 갈 수 있는가 — 시작 전에는 순위가 존재하지 않는다.
            "has_ranking": contest.status != ContestStatus.UPCOMING,
        },
    )


# ─────────────────────────────────────────────────────────────────
# 3. 대회 랭킹 (U-02 6장 · F-05 3장)
# ─────────────────────────────────────────────────────────────────


def _ranking_context(request, contest: Contest) -> dict:
    """랭킹 표와 그 주변 값. 페이지와 프래그먼트가 함께 쓴다."""
    rows = services.ranking_table(contest, request.user)
    my_row = next((row for row in rows if row.is_me), None)
    return {
        "contest": contest,
        "rows": rows,
        # ★★ "언제 기준 숫자인가" 를 반드시 화면에 적는다. `ContestRanking` 은
        #   영업일 15:40 정산(잡 7)에만 갱신되므로, 장중에 60초 폴링을 걸어도
        #   값은 전 영업일 15:40 것이다. 이 표시가 없으면 참가자는 방금 낸
        #   주문이 순위에 없는 것을 버그로 읽는다.
        "as_of": services.latest_ranking_date(contest),
        "my_row": my_row,
        "block_reason": services.portfolio_block_reason(contest),
        # 대회가 끝나면 폴링을 멈춘다 (HTMX 규약 5.2) — 더 바뀔 값이 없다.
        "is_live": contest.status in (ContestStatus.ONGOING, ContestStatus.SETTLING),
    }


def contest_ranking(request, slug: str):
    """랭킹 페이지 (표는 `_ranking_table.html` 이 그린다)."""
    contest = _visible_contest(request, slug)
    context = _ranking_context(request, contest)
    context["context_type"] = "contest_ranking"
    context["context_id"] = contest.slug
    return render(request, "contests/ranking.html", context)


def fragment_ranking(request, slug: str):
    """랭킹 표만 — 60초 폴링 (HTMX 규약 3.3).

    ★ 프래그먼트 뷰는 `base.html` 을 상속하지 않는 조각 하나만 렌더한다.
      상속하면 `<html>` 통째로 응답해 화면 속에 화면이 겹친다.
    """
    contest = _visible_contest(request, slug)
    return render(request, "contests/_ranking_table.html", _ranking_context(request, contest))


# ─────────────────────────────────────────────────────────────────
# 4. 참가 신청 (U-02 · F-02 6장)
# ─────────────────────────────────────────────────────────────────


@login_required
@require_http_methods(["GET", "POST"])
def contest_join(request, slug: str):
    """참가 신청 — 별칭 + 규칙 동의.

    ★★ **`GET` 에서 이미 참가 중이면 상세로 돌려보낸다.** 신청 화면을 다시
      보여 주고 눌렀을 때 `ALREADY_JOINED` 로 튕기는 것보다, 애초에 들어오지
      못하게 하는 편이 친절하다. 그래도 POST 에서 다시 검사한다 — 두 탭을
      열어 두고 양쪽에서 누르는 경우가 있다.
    """
    contest = _visible_contest(request, slug)
    detail_url = reverse("contests:detail", args=[contest.slug])
    participation = _my_participation(request, contest)

    if participation is not None and participation.status in (
        ParticipationStatus.PENDING,
        ParticipationStatus.APPROVED,
    ):
        messages.info(request, "이미 참가 중인 대회입니다.")
        return redirect(detail_url)

    if request.method == "POST":
        form = JoinForm(request.POST)
        if form.is_valid():
            try:
                joined = services.join_contest(
                    contest=contest,
                    member=request.user,
                    nickname=form.cleaned_data["nickname"],
                )
            except services.JoinRejected as rejected:
                # ★ 사유를 **입력칸 옆**(별칭 중복)이나 **폼 상단**(정원·마감)에
                #   나눠 붙인다. 전부 상단에 몰면 어느 칸을 고쳐야 할지 모른다.
                form.add_error(rejected.field or None, rejected.message)
            else:
                if joined.status == ParticipationStatus.APPROVED:
                    message = (
                        f"'{contest.name}' 참가가 확정됐습니다. "
                        f"대회 계좌에 {contest.initial_capital:,}원이 준비됐습니다."
                    )
                else:
                    message = (
                        f"'{contest.name}' 참가를 신청했습니다. "
                        "운영자 승인 후 대회 계좌가 만들어집니다."
                    )
                # 페이지를 통째로 옮기므로 토스트가 아니라 messages 로 남긴다
                # (HX-Redirect 를 받으면 그 응답의 토스트 이벤트는 버려진다).
                messages.success(request, message)
                if is_htmx(request):
                    return hx_redirect(detail_url)
                return redirect(detail_url)
    else:
        form = JoinForm(
            # 표시명이 있으면 별칭 기본값으로 제안한다. 대회마다 다른 별칭을
            # 쓸 수 있으니 **강제하지 않고 채워만** 둔다.
            initial={"nickname": (request.user.display_name or "")[:20]}
        )

    context = {
        "context_type": "contest_join",
        "context_id": contest.slug,
        "contest": contest,
        "form": form,
        "rule_rows": services.rule_summary(contest),
        "entry_deadline": services.entry_deadline_of(contest),
        "capacity_left": (
            contest.capacity - services.occupied_seats(contest) if contest.capacity else None
        ),
        "auto_approve": contest.approval_mode == ApprovalMode.AUTO,
    }
    # 검증 실패 응답은 422 가 아니라 200 이다 — HTMX 는 4xx 본문을 화면에 넣지
    # 않으므로, 에러가 담긴 폼을 보여주려면 200 으로 보내야 한다.
    template = "contests/_join_form.html" if is_htmx(request) else "contests/join.html"
    return render(request, template, context)


# ─────────────────────────────────────────────────────────────────
# 5. 참가자 상세 (U-02 7장 · A-02 4.2)
# ─────────────────────────────────────────────────────────────────


def participant_detail(request, slug: str, participation_id: int):
    """참가자 한 명의 포트폴리오 — 랭킹 표의 [열기] 가 부른다.

    ★★ **현금·잔고 절대금액은 내보내지 않는다** (A-02 4.2 · U-02 7장).
      종목·비중·손익률만 공개한다. 남의 순자산이 원 단위로 보이면 대회가
      "누가 돈이 많나" 로 바뀐다 — 우리가 비교하려는 것은 운용 결과다.

    ★★ **공개 범위를 여기서 다시 검사한다.** 랭킹 표에서 [열기] 버튼을 감추는
      것은 안내이지 보안이 아니다. 주소를 직접 치는 경로가 항상 남는다.
    """
    contest = _visible_contest(request, slug)
    participation = get_object_or_404(
        Participation.objects.select_related("account", "member"),
        pk=participation_id,
        contest=contest,
    )

    # 최신 순위 — 공개 범위 판정(TOP_N)의 기준값이다.
    latest = (
        participation.rankings.order_by("-date").values_list("rank", flat=True).first()
    )
    allowed = services.can_view_portfolio(
        contest=contest, viewer=request.user, participation=participation, rank=latest
    )

    context = {
        "contest": contest,
        "participation": participation,
        "allowed": allowed,
        "block_reason": services.portfolio_block_reason(contest),
    }

    if allowed:
        context.update(_portfolio_context(participation))

    template = (
        "contests/_participant_modal.html" if is_htmx(request) else "contests/participant.html"
    )
    return render(request, template, context)


def _portfolio_context(participation: Participation) -> dict:
    """보유 종목 · 수익 종목 · 거래 이력.

    ★★ **전부 정산이 만들어 둔 값을 읽는다.** 보유 종목은 마지막 정산일의
      `SnapshotHolding` 이다 — "지금 이 순간" 을 다시 평가하면 랭킹 표의 편입비와
      모달 안의 비중이 서로 다른 숫자가 된다(같은 화면에서!).
    """
    snapshot = (
        DailySnapshot.objects.filter(participation=participation)
        .order_by("-date")
        .first()
    )
    holdings = []
    if snapshot is not None:
        holdings = list(
            SnapshotHolding.objects.filter(snapshot=snapshot).order_by("-value")
        )

    # 수익 종목 — 실현 손익까지 포함한 종목별 합계 (F-05 3.4).
    #
    # ★ `symbol_profits()` 는 참가자 1명당 `Order` 전체를 집계하므로 **목록 화면에서
    #   N명 루프로 부르면 안 된다.** 모달은 클릭 한 번에 한 명이라 괜찮다.
    profits = services.symbol_profits(participation)
    names = {row.symbol: row.name for row in holdings}
    total_profit = sum(value for value in profits.values() if value > 0)
    profit_rows = [
        {
            "symbol": symbol,
            "name": names.get(symbol, ""),
            "pnl": value,
            # "수익 / 총수익(%)" — 이익을 낸 종목들 안에서의 기여도다.
            "share_pct": (round(value * 100 / total_profit, 1) if total_profit and value > 0 else None),
        }
        for symbol, value in sorted(profits.items(), key=lambda item: item[1], reverse=True)
    ]

    orders = []
    if participation.account_id:
        # 지연 import — 의존 방향(`trading ◀ contests`)은 허용되지만, 화면 하나
        # 때문에 모듈 최상단에 무거운 의존을 만들지 않는다.
        from trading.models import Order, OrderStatus       # noqa: PLC0415

        orders = list(
            Order.objects.filter(
                account_id=participation.account_id,
                status__in=(OrderStatus.FILLED, OrderStatus.PARTIAL),
            )
            # ★ `-filled_at` 로 정렬하지 않는다. 부분 체결 주문은 그 값이 비어 있을
            #   수 있고, NULL 이 섞이면 정렬 결과가 DB 설정에 따라 달라진다.
            #   주문 시각이면 "언제 사고팔았는가" 에 충분하다.
            .order_by("-created_at")[:RECENT_ORDER_LIMIT]
        )

    return {
        "snapshot": snapshot,
        "holdings": holdings,
        "profit_rows": profit_rows,
        "orders": orders,
    }
